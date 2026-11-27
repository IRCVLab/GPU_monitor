"""Fixed OpenSSH transports to storage hosts.

``GateTransport`` speaks the host gate (design §4.5): one verb per ssh exchange,
answered by a header line and a counted body.

SHIM(M2b..M3b) S2: ``OpenSshTransport`` (SFTP, the Python probe and the blocking
``systemctl start``) serves hosts that are not bootstrapped yet. It goes once
every host reports the gate transport.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
import pathlib
import re
import shlex
import stat
import signal
import subprocess
import sys
import tempfile
from typing import Any, Dict, Mapping, Optional, Pattern, Protocol, Sequence, Tuple

from collector.ids import RELEASE_SHA_RE, REQUEST_ID_RE, SHA256_RE, VOLUME_ID_RE
from collector.inventory import Server

STATUS_PATH = "/var/lib/storage-viz/scan-status.json"
SNAPSHOT_DIR = "/var/lib/storage-viz/snapshots"
MANIFEST_PATH = "/var/lib/storage-viz/manifest.json"
CAPTURE_DIR = "/var/lib/storage-viz/captures"
MAX_MANIFEST_BYTES = 64 * 1024
MISSING_FILE_MARKERS = ("not found", "No such file")
RESCAN_COMMAND = ("sudo", "-n", "/usr/bin/systemctl", "start", "storage-viz-scan.service")
KNOWN_ACTIVE_STATES = frozenset({"active", "reloading", "inactive", "failed", "activating", "deactivating", "maintenance"})
MAX_STATUS_BYTES = 64 * 1024
MAX_SNAPSHOT_BYTES = 512 * 1024 * 1024
DEFAULT_CONNECT_TIMEOUT_SECONDS = 10
DEFAULT_STATUS_TIMEOUT_SECONDS = 30
DEFAULT_SNAPSHOT_TIMEOUT_SECONDS = 3600
DEFAULT_RESCAN_TIMEOUT_SECONDS = 6 * 60 * 60
DEFAULT_ACTIVE_STATE_TIMEOUT_SECONDS = 10
MAX_RESCAN_TIMEOUT_SECONDS = 24 * 60 * 60
SSH_ERROR_EXIT = 255
AUTH_FAILURE_MARKERS = ("Permission denied", "Host key verification failed", "REMOTE HOST IDENTIFICATION HAS CHANGED")
GENERATION_RE = re.compile(r"^(?P<server_id>[A-Za-z0-9_.-]+)-(?P<started>[0-9]+)-v1\.json$")
KEY_ROOT = "/etc/storage-viz/"

FILE_LIMIT_EXEC = """
import os
import resource
import signal
import sys

cap = int(sys.argv[1])
resource.setrlimit(resource.RLIMIT_FSIZE, (cap, cap))
for name in ("SIGPIPE", "SIGXFZ", "SIGXFSZ"):
    signum = getattr(signal, name, None)
    if signum is not None:
        signal.signal(signum, signal.SIG_DFL)
argv = sys.argv[2:]
os.execvp(argv[0], argv)
"""


class TransportError(Exception):
    """Bounded typed transport failure without sensitive process details."""

    def __init__(self, code: str, message: str):
        self.code = _safe_code(code)
        self.host_code: Optional[str] = None
        """The host's own ``err <CODE>`` text, for a gate refusal."""
        super().__init__(_safe_message(message))


class Runner(Protocol):
    def run(self, argv: list[str], **kwargs: Any) -> Any: ...


@dataclass(frozen=True)
class _SubprocessRunner:
    def run(self, argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(argv, **kwargs)


class OpenSshTransport:
    def __init__(
        self,
        *,
        runner: Optional[Runner] = None,
        temp_dir: str | os.PathLike[str] | None = None,
        connect_timeout_seconds: int = DEFAULT_CONNECT_TIMEOUT_SECONDS,
        process_timeout_seconds: int | None = None,
        status_timeout_seconds: int | None = None,
        snapshot_timeout_seconds: int | None = None,
        rescan_timeout_seconds: int | None = None,
        active_state_timeout_seconds: int | None = None,
        max_status_bytes: int = MAX_STATUS_BYTES,
        max_snapshot_bytes: int = MAX_SNAPSHOT_BYTES,
    ) -> None:
        legacy = process_timeout_seconds if process_timeout_seconds is not None else None
        self._runner = runner or _SubprocessRunner()
        self._temp_dir = temp_dir
        self._connect_timeout = _bounded_positive(connect_timeout_seconds, "connect timeout", 1, 300)
        self._status_timeout = _bounded_positive(status_timeout_seconds if status_timeout_seconds is not None else (legacy or DEFAULT_STATUS_TIMEOUT_SECONDS), "status timeout", 1, 3600)
        self._snapshot_timeout = _bounded_positive(snapshot_timeout_seconds if snapshot_timeout_seconds is not None else (legacy or DEFAULT_SNAPSHOT_TIMEOUT_SECONDS), "snapshot timeout", 1, 24 * 60 * 60)
        self._rescan_timeout = _bounded_positive(rescan_timeout_seconds if rescan_timeout_seconds is not None else (legacy or DEFAULT_RESCAN_TIMEOUT_SECONDS), "rescan timeout", 1, MAX_RESCAN_TIMEOUT_SECONDS)
        self._active_state_timeout = _bounded_positive(active_state_timeout_seconds if active_state_timeout_seconds is not None else (legacy or DEFAULT_ACTIVE_STATE_TIMEOUT_SECONDS), "active-state timeout", 1, 300)
        self._max_status_bytes = _bounded_positive(max_status_bytes, "max status bytes", 1, MAX_STATUS_BYTES)
        self._max_snapshot_bytes = _bounded_positive(max_snapshot_bytes, "max snapshot bytes", 1, MAX_SNAPSHOT_BYTES)

    def fetch_status(self, server: Server) -> dict[str, Any]:
        self._validate_server(server)
        tmp_name = self._mk_secure_temp(".status")
        try:
            self._run_sftp(server, _batch_get(STATUS_PATH, tmp_name), timeout=self._status_timeout, limit_bytes=self._max_status_bytes, limit_code="STATUS_TOO_LARGE", local_path=tmp_name)
            data = _read_regular_bounded(tmp_name, self._max_status_bytes, "STATUS_TOO_LARGE")
            try:
                status_obj = json.loads(data.decode("utf-8"))
            except Exception as exc:
                raise TransportError("MALFORMED_STATUS", "status JSON is malformed") from exc
            if not isinstance(status_obj, dict):
                raise TransportError("MALFORMED_STATUS", "status JSON must be object")
            return status_obj
        finally:
            _unlink_quiet(tmp_name)

    def run_probe(self, server: Server) -> bytes:
        """Run the fixed legacy observation probe (SHIM(M2b..M3b) S2) and return its stdout."""
        from collector.legacy import MAX_PROBE_BYTES, REMOTE_OBSERVATION_SCRIPT
        self._validate_server(server)
        result = self._run_ssh(server, ["python3", "-c", shlex.quote(REMOTE_OBSERVATION_SCRIPT)], timeout=self._active_state_timeout)
        _check_returncode(result, "TELEMETRY_UNAVAILABLE")
        raw = getattr(result, "stdout", b"")
        if not isinstance(raw, (str, bytes)) or len(raw) > MAX_PROBE_BYTES:
            raise TransportError("BAD_TELEMETRY", "telemetry exceeds maximum size")
        return raw

    def fetch_snapshot(self, server: Server, expected_status: Mapping[str, Any] | None = None) -> Tuple[dict[str, Any], bytes]:
        first = dict(expected_status) if expected_status is not None else self.fetch_status(server)
        generation = validate_snapshot_status(first, server, self._max_snapshot_bytes)
        remote = f"{SNAPSHOT_DIR}/{generation}"
        tmp_name = self._mk_secure_temp(".snapshot")
        try:
            # The cap is the declared byte_size, which validate_snapshot_status already held to the maximum.
            # Outgrowing it means the file disagrees with its publication; a failed transfer that reached
            # it is still a transfer error. Neither is the permanent SNAPSHOT_TOO_LARGE.
            self._run_sftp(server, _batch_get(remote, tmp_name), timeout=self._snapshot_timeout, limit_bytes=min(first["byte_size"], self._max_snapshot_bytes), limit_code="SIZE_MISMATCH", local_path=None)
            data = _read_regular_bounded(tmp_name, self._max_snapshot_bytes, "SNAPSHOT_TOO_LARGE")
            if len(data) != first["byte_size"]:
                raise TransportError("SIZE_MISMATCH", "snapshot size changed during download")
            second = self.fetch_status(server)
            if status_tuple(first) != status_tuple(second):
                raise TransportError("RACE", "status changed during snapshot download")
            return second, data
        finally:
            _unlink_quiet(tmp_name)

    def fetch_manifest(self, server: Server) -> bytes:
        """manifest.json of a v2 host (SHIM(M2b..M3b) S2: the gate's get-manifest replaces it)."""
        return self._get_file(server, MANIFEST_PATH, MAX_MANIFEST_BYTES, "MANIFEST_TOO_LARGE", "MANIFEST_MISSING",
                              timeout=self._status_timeout)

    def fetch_volume_capture(self, server: Server, volume_id: str, sha256: str, cap: int) -> bytes:
        """One immutable v2 capture, at most ``cap`` bytes (SHIM(M2b..M3b) S2: the gate's get-capture)."""
        if not VOLUME_ID_RE.fullmatch(volume_id) or not SHA256_RE.fullmatch(sha256):
            raise TransportError("BAD_PATH", "capture name is unsafe")
        return self._get_file(server, f"{CAPTURE_DIR}/{volume_id}/{sha256}.json.gz", cap, "CAPTURE_TOO_LARGE",
                              "CAPTURE_GONE", timeout=self._snapshot_timeout)

    def _get_file(self, server: Server, remote: str, cap: int, limit_code: str, missing_code: str, *, timeout: int) -> bytes:
        self._validate_server(server)
        cap = _bounded_positive(cap, "file size limit", 1, self._max_snapshot_bytes)
        tmp_name = self._mk_secure_temp(".v2")
        try:
            try:
                self._run_sftp(server, _batch_get(remote, tmp_name), timeout=timeout, limit_bytes=cap,
                               limit_code=limit_code, local_path=tmp_name)
            except TransportError as exc:
                stderr = getattr(exc, "stderr", b"")
                if exc.code == "UNREACHABLE" and any(marker.encode() in stderr for marker in MISSING_FILE_MARKERS):
                    raise TransportError(missing_code, "remote file is missing") from exc
                raise
            return _read_regular_bounded(tmp_name, cap, limit_code)
        finally:
            _unlink_quiet(tmp_name)

    def start_rescan(self, server: Server) -> None:
        self._validate_server(server)
        result = self._run_ssh(server, list(RESCAN_COMMAND), timeout=self._rescan_timeout)
        _check_returncode(result, "RESCAN_FAILED")

    def _mk_secure_temp(self, suffix: str) -> str:
        fd, tmp_name = tempfile.mkstemp(prefix="storage-viz-", suffix=suffix, dir=self._temp_dir)
        try:
            os.fchmod(fd, 0o600)
        finally:
            os.close(fd)
        _assert_secure_regular(tmp_name)
        return tmp_name

    def _run_sftp(self, server: Server, batch: bytes, *, timeout: int, limit_bytes: int, limit_code: str, local_path: str | None) -> Any:
        """Run one sftp batch under a file-size cap. SIGXFSZ raises ``limit_code``, and so does a failure
        that left ``local_path`` at the cap; pass None where reaching the cap proves nothing."""
        cap = _bounded_positive(limit_bytes, "file size limit", 1, MAX_SNAPSHOT_BYTES)
        argv = [sys.executable, "-I", "-S", "-c", FILE_LIMIT_EXEC, str(cap), *self._sftp_argv(server)]
        try:
            result = self._runner.run(
                argv, input=batch, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=timeout, check=False, shell=False,
            )
        except (subprocess.TimeoutExpired, TimeoutError) as exc:
            raise TransportError("TIMEOUT", "transport command timed out") from exc
        except Exception as exc:
            raise TransportError("UNREACHABLE", "transport command failed") from exc
        if getattr(result, "returncode", 0) == SSH_ERROR_EXIT:
            raise TransportError(classify_ssh_failure(getattr(result, "stderr", b"")), "sftp connection failed")
        try:
            _check_returncode(result, "UNREACHABLE", limit_code=limit_code, local_path=local_path, limit_bytes=limit_bytes)
        except TransportError as exc:
            # Kept off the message, which stays bounded; callers read it to tell a missing file apart.
            stderr = getattr(result, "stderr", b"")
            exc.stderr = stderr if isinstance(stderr, bytes) else str(stderr or "").encode()
            raise
        return result

    def _run_ssh(self, server: Server, command: list[str], *, timeout: int) -> Any:
        argv = self._ssh_argv(server) + ["--", server.host] + command
        try:
            result = self._runner.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, check=False, shell=False)
        except (subprocess.TimeoutExpired, TimeoutError) as exc:
            raise TransportError("TIMEOUT", "transport command timed out") from exc
        except Exception as exc:
            raise TransportError("UNREACHABLE", "transport command failed") from exc
        if getattr(result, "returncode", 0) == SSH_ERROR_EXIT:
            raise TransportError(classify_ssh_failure(getattr(result, "stderr", b"")), "ssh connection failed")
        return result

    def _common_options(self, server: Server) -> list[str]:
        return ssh_options(server, self._connect_timeout)

    def _sftp_argv(self, server: Server) -> list[str]:
        return ["sftp", *self._common_options(server), "-P", str(server.port), "-b", "-", server.host]

    def _ssh_argv(self, server: Server) -> list[str]:
        return ["ssh", *self._common_options(server), "-p", str(server.port)]

    def _validate_server(self, server: Server) -> None:
        validate_server(server)


# ------------------------------------------------------------------ gate transport

GATE_HEADER_RE = re.compile(rb"storage-viz-gate/([1-9][0-9]{0,3}) (?:ok (0|[1-9][0-9]{0,9})|err ([A-Z0-9_]{1,64}))\n")
MAX_GATE_HEADER_BYTES = 64
NOT_GATE = "NOT_GATE"
"""The host answered without the gate's header line: it is not bootstrapped, or the bootstrap was reverted."""
GATE_CODES = frozenset({
    "USAGE", "IO_ERROR", "START_FAILED", "FILE_TOO_LARGE", "OUTPUT_TOO_LARGE", "SHORT_READ", "BAD_SIGNATURE_SIZE",
    "CAPTURE_GONE", "MANIFEST_MISSING", "SNAPSHOT_GONE", "STATUS_MISSING", "UPDATES_MANUAL",
})
"""The gate's own refusal codes (agent/bootstrap/gate.py), kept as the error code. Any other code a host sends
becomes GATE_REFUSED with the host's text in ``host_code``, so a host can never claim one of central's own
codes (NOT_GATE, TIMEOUT, UNREACHABLE, AUTH_FAILED), which only central's observations produce."""
SNAPSHOT_FILE_RE = re.compile(r"\A[A-Za-z0-9_.-]+-[0-9]+-v1\.json\Z")
STAGE_SIZE_RE = re.compile(r"\A[1-9][0-9]{0,8}\Z")
KiB = 1024
MiB = 1024 * KiB


@dataclass(frozen=True)
class GateVerb:
    args: Tuple[Pattern[str], ...]
    cap: int
    """The most body bytes central accepts, the gate's own output cap (§4.5)."""
    timeout: int
    too_large: str = "REPLY_TOO_LARGE"
    """The code for a body over the cap."""


GATE_VERBS: Dict[str, GateVerb] = {
    "observe": GateVerb((), 64 * KiB, DEFAULT_STATUS_TIMEOUT_SECONDS),
    "get-manifest": GateVerb((), 64 * KiB, DEFAULT_STATUS_TIMEOUT_SECONDS, "MANIFEST_TOO_LARGE"),
    "get-capture": GateVerb((VOLUME_ID_RE, SHA256_RE), 512 * MiB, DEFAULT_SNAPSHOT_TIMEOUT_SECONDS, "CAPTURE_TOO_LARGE"),
    "rescan": GateVerb((REQUEST_ID_RE,), 1 * KiB, DEFAULT_STATUS_TIMEOUT_SECONDS),
    "stage-update": GateVerb((RELEASE_SHA_RE, STAGE_SIZE_RE), 1 * KiB, 600),
    "activate-update": GateVerb((RELEASE_SHA_RE,), 1 * KiB, DEFAULT_STATUS_TIMEOUT_SECONDS),
    "rollback-update": GateVerb((RELEASE_SHA_RE,), 1 * KiB, DEFAULT_STATUS_TIMEOUT_SECONDS),
    # SHIM(M3b..M5c) S7: the central callers of the v1 verbs go with S3 and S4.
    "get-status": GateVerb((), 64 * KiB, DEFAULT_STATUS_TIMEOUT_SECONDS, "STATUS_TOO_LARGE"),
    "get-snapshot": GateVerb((SNAPSHOT_FILE_RE,), 512 * MiB, DEFAULT_SNAPSHOT_TIMEOUT_SECONDS, "SNAPSHOT_TOO_LARGE"),
}
_SIZED_VERBS = frozenset({"get-capture", "get-snapshot"})
"""Verbs whose body length the publication declares: given as ``cap``, a body of another length is SIZE_MISMATCH."""


class GateTransport:
    """``run_verb`` sends exactly one gate verb over ssh and returns the body of an ``ok`` reply.

    stdout and stderr go to private temporary files under a file-size limit, so no
    reply can grow past its verb's cap in memory or on disk. Failures are typed:
    ssh's own failure (exit 255) is AUTH_FAILED or UNREACHABLE, a timeout TIMEOUT,
    a gate refusal its own code, a reply without the gate header NOT_GATE, and a
    body that disagrees with its header SIZE_MISMATCH or BAD_REPLY.
    """

    def __init__(self, *, runner: Optional[Runner] = None, temp_dir: str | os.PathLike[str] | None = None,
                 connect_timeout_seconds: int = DEFAULT_CONNECT_TIMEOUT_SECONDS, key_root: str = KEY_ROOT) -> None:
        self._runner = runner or _SubprocessRunner()
        self._temp_dir = temp_dir
        self._connect_timeout = _bounded_positive(connect_timeout_seconds, "connect timeout", 1, 300)
        if not key_root.startswith("/") or not key_root.endswith("/"):
            raise ValueError("key root must be an absolute directory path ending in /")
        self._key_root = key_root

    def run_verb(self, server: Server, verb: str, args: Sequence[str] = (), stdin: Optional[bytes] = None,
                 cap: Optional[int] = None) -> bytes:
        """The body of the gate's ``ok`` reply. ``cap`` lowers the verb's cap, for a body of known size."""
        spec = GATE_VERBS.get(verb)
        if spec is None or len(args) != len(spec.args) or not all(
                isinstance(arg, str) and pattern.fullmatch(arg) for pattern, arg in zip(spec.args, args)):
            raise TransportError("BAD_VERB", "not a gate verb")
        validate_server(server, self._key_root)
        limit = spec.cap if cap is None else _bounded_positive(cap, "reply cap", 1, spec.cap)
        command = " ".join((verb, *args))
        argv = [sys.executable, "-I", "-S", "-c", FILE_LIMIT_EXEC, str(MAX_GATE_HEADER_BYTES + limit),
                "ssh", *ssh_options(server, self._connect_timeout), "-p", str(server.port), "--", server.host, command]
        out_name, err_name = self._temp(".gate-out"), self._temp(".gate-err")
        try:
            with open(out_name, "wb") as out, open(err_name, "wb") as err:
                options: Dict[str, Any] = {"input": stdin} if stdin is not None else {"stdin": subprocess.DEVNULL}
                try:
                    result = self._runner.run(argv, stdout=out, stderr=err, timeout=spec.timeout, check=False,
                                              shell=False, **options)
                except (subprocess.TimeoutExpired, TimeoutError) as exc:
                    raise TransportError("TIMEOUT", "gate command timed out") from exc
                except Exception as exc:
                    raise TransportError("UNREACHABLE", "gate command failed") from exc
            returncode = getattr(result, "returncode", 1)
            if returncode == SSH_ERROR_EXIT:
                raise TransportError(classify_ssh_failure(_read_head(err_name)), "ssh connection failed")
            if returncode == -getattr(signal, "SIGXFSZ", -9999):
                raise TransportError(spec.too_large, "gate reply exceeds its cap")
            reply = _read_regular_bounded(out_name, MAX_GATE_HEADER_BYTES + limit, spec.too_large)
        finally:
            _unlink_quiet(out_name)
            _unlink_quiet(err_name)
        return parse_gate_reply(reply, returncode, limit, spec, sized=verb in _SIZED_VERBS and cap is not None)

    def _temp(self, suffix: str) -> str:
        fd, name = tempfile.mkstemp(prefix="storage-viz-", suffix=suffix, dir=self._temp_dir)
        try:
            os.fchmod(fd, 0o600)
        finally:
            os.close(fd)
        _assert_secure_regular(name)
        return name


def parse_gate_reply(reply: bytes, returncode: int, limit: int, spec: GateVerb, *, sized: bool = False) -> bytes:
    """Check the header line and the counted body of one gate reply. ``sized``: the body must be ``limit`` bytes."""
    match = GATE_HEADER_RE.match(reply[:MAX_GATE_HEADER_BYTES])
    if match is None:
        raise TransportError(NOT_GATE, "the host did not answer with the gate header")
    if match.group(3) is not None:
        host_code = match.group(3).decode()
        code = spec.too_large if host_code == "TOO_LARGE" else host_code if host_code in GATE_CODES else "GATE_REFUSED"
        error = TransportError(code, "the gate refused the verb")
        error.host_code = host_code
        raise error
    declared = int(match.group(2))
    body = reply[match.end():]
    if returncode != 0:
        raise TransportError("BAD_REPLY", "an ok header with a nonzero exit status")
    if declared > limit:
        raise TransportError(spec.too_large if not sized or declared > spec.cap else "SIZE_MISMATCH",
                             "the gate reply is larger than allowed")
    if len(body) != declared or (sized and declared != limit):
        raise TransportError("SIZE_MISMATCH" if sized else "BAD_REPLY", "the gate body length disagrees with its header")
    return body


def _read_head(path: str, limit: int = 4096) -> bytes:
    try:
        with open(path, "rb") as handle:
            return handle.read(limit)
    except OSError:
        return b""


def ssh_options(server: Server, connect_timeout: int) -> list[str]:
    """BatchMode, the inventory's pinned known_hosts with strict checking, and only the server's key."""
    return [
        "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=yes",
        "-o", "IdentitiesOnly=yes",
        "-o", f"ConnectTimeout={connect_timeout}",
        "-o", f"UserKnownHostsFile={server.known_hosts_file}",
        "-o", f"User={server.username}",
        "-i", str(server.identity_file),
    ]


def validate_server(server: Server, key_root: str = KEY_ROOT) -> None:
    if server.username != "monitoring":
        raise TransportError("BAD_INVENTORY", "server username must be monitoring")
    if not isinstance(server.host, str) or any(ch in server.host for ch in " ;|&`$\\\n\r\t/[]{}()<>"):
        raise TransportError("BAD_INVENTORY", "server host is unsafe")
    if not isinstance(server.port, int) or isinstance(server.port, bool) or not 1 <= server.port <= 65535:
        raise TransportError("BAD_INVENTORY", "server port is unsafe")
    for label, value in (("identity", str(server.identity_file)), ("known_hosts", str(server.known_hosts_file))):
        if not value.startswith(key_root) or _has_control(value):
            raise TransportError("BAD_INVENTORY", f"{label} path is unsafe")


def _batch_get(remote: str, local: str) -> bytes:
    if _has_control(remote) or _has_control(local):
        raise TransportError("BAD_PATH", "batch path is unsafe")
    return f"get {shlex.quote(remote)} {shlex.quote(local)}\n".encode("utf-8")


def classify_ssh_failure(stderr: Any) -> str:
    """Exit 255 is ssh's own failure. A refused key or a changed host key is AUTH_FAILED, the rest UNREACHABLE."""
    text = stderr.decode("utf-8", "replace") if isinstance(stderr, (bytes, bytearray)) else str(stderr or "")
    return "AUTH_FAILED" if any(marker in text for marker in AUTH_FAILURE_MARKERS) else "UNREACHABLE"


def validate_snapshot_status(status: Mapping[str, Any], server: Server, max_snapshot_bytes: int) -> str:
    generation = _safe_generation(status.get("generation"), server, status.get("scan_started_unix"))
    if status.get("server_id") != server.id:
        raise TransportError("BAD_STATUS", "status server_id mismatch")
    if status.get("status") not in {"complete", "partial"}:
        raise TransportError("BAD_STATUS", "status is not downloadable")
    if not isinstance(status.get("config_digest"), str) or not SHA256_RE.fullmatch(status["config_digest"]):
        raise TransportError("BAD_STATUS", "status config digest is invalid")
    if not isinstance(status.get("scan_finished_unix"), int) or isinstance(status.get("scan_finished_unix"), bool) or status["scan_finished_unix"] < 0:
        raise TransportError("BAD_STATUS", "status timestamp is invalid")
    size = status.get("byte_size")
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        raise TransportError("BAD_STATUS", "snapshot size is invalid")
    if size > max_snapshot_bytes:
        raise TransportError("SNAPSHOT_TOO_LARGE", "snapshot exceeds maximum size")
    sha = status.get("sha256")
    if not isinstance(sha, str) or not SHA256_RE.fullmatch(sha):
        raise TransportError("BAD_STATUS", "status sha256 is invalid")
    return generation


def validate_status_envelope(raw: Mapping[str, Any], server: Server) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise TransportError("BAD_STATUS", "status must be object")
    status = dict(raw)
    _safe_generation(status.get("generation"), server, status.get("scan_started_unix"))
    if status.get("server_id") != server.id:
        raise TransportError("BAD_STATUS", "status server_id mismatch")
    if status.get("status") not in {"complete", "partial", "failed"}:
        raise TransportError("BAD_STATUS", "status value is invalid")
    if not isinstance(status.get("config_digest"), str) or not SHA256_RE.fullmatch(status["config_digest"]):
        raise TransportError("BAD_STATUS", "status config digest is invalid")
    if not isinstance(status.get("scan_finished_unix"), int) or isinstance(status.get("scan_finished_unix"), bool) or status["scan_finished_unix"] < 0:
        raise TransportError("BAD_STATUS", "status timestamp is invalid")
    if status["status"] in {"complete", "partial"}:
        if not isinstance(status.get("byte_size"), int) or isinstance(status.get("byte_size"), bool) or status["byte_size"] <= 0:
            raise TransportError("BAD_STATUS", "snapshot size is invalid")
        if not isinstance(status.get("sha256"), str) or not SHA256_RE.fullmatch(status["sha256"]):
            raise TransportError("BAD_STATUS", "status sha256 is invalid")
    return status


def _safe_generation(value: Any, server: Server | None = None, scan_started_unix: Any = None) -> str:
    if not isinstance(value, str):
        raise TransportError("BAD_GENERATION", "generation is unsafe")
    match = GENERATION_RE.fullmatch(value)
    if not match:
        raise TransportError("BAD_GENERATION", "generation is unsafe")
    if any(ch in value for ch in "/\\ \t\r\n;|&`$<>*?[]{}()'") or value in {".", ".."}:
        raise TransportError("BAD_GENERATION", "generation is unsafe")
    captured_server_id = match.group("server_id")
    if captured_server_id in {".", ".."}:
        raise TransportError("BAD_GENERATION", "generation server is unsafe")
    if server is not None and captured_server_id != server.id:
        raise TransportError("BAD_GENERATION", "generation does not match server")
    started_text = match.group("started")
    if len(started_text) > 1 and started_text.startswith("0"):
        raise TransportError("BAD_GENERATION", "generation timestamp is not canonical")
    try:
        started = int(started_text)
    except ValueError as exc:
        raise TransportError("BAD_GENERATION", "generation timestamp is invalid") from exc
    if started < 0 or started >= 4_102_444_800:
        raise TransportError("BAD_GENERATION", "generation timestamp out of range")
    if scan_started_unix is not None and scan_started_unix != started:
        raise TransportError("BAD_GENERATION", "generation timestamp mismatch")
    return value


def status_tuple(status: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        status.get("generation"), status.get("byte_size"), status.get("sha256"),
        status.get("server_id"), status.get("config_digest"), status.get("scan_finished_unix"), status.get("status"),
    )


def _read_regular_bounded(path: str, max_bytes: int, code: str) -> bytes:
    _assert_secure_regular(path)
    size = os.path.getsize(path)
    if size > max_bytes:
        raise TransportError(code, "transport file exceeds maximum size")
    data = pathlib.Path(path).read_bytes()
    _assert_secure_regular(path)
    if len(data) > max_bytes:
        raise TransportError(code, "transport file exceeds maximum size")
    return data


def _assert_secure_regular(path: str) -> None:
    st = os.lstat(path)
    if not stat.S_ISREG(st.st_mode) or stat.S_ISLNK(st.st_mode):
        raise TransportError("BAD_TEMP", "temporary file is unsafe")
    if stat.S_IMODE(st.st_mode) != 0o600:
        raise TransportError("BAD_TEMP", "temporary file mode is unsafe")


def _check_returncode(result: Any, code: str, *, limit_code: str | None = None, local_path: str | None = None, limit_bytes: int | None = None) -> None:
    returncode = getattr(result, "returncode", 1)
    if returncode != 0:
        if limit_code and returncode == -getattr(signal, "SIGXFSZ", -9999):
            raise TransportError(limit_code, "transport exceeded file size limit")
        if limit_code and local_path is not None and limit_bytes is not None and _regular_size_at_least(local_path, limit_bytes):
            raise TransportError(limit_code, "transport exceeded file size limit")
        raise TransportError(code, "transport command returned nonzero status")


def _regular_size_at_least(path: str, limit_bytes: int) -> bool:
    try:
        st = os.lstat(path)
    except OSError:
        return False
    return stat.S_ISREG(st.st_mode) and not stat.S_ISLNK(st.st_mode) and st.st_size >= limit_bytes


def _bounded_positive(value: int, label: str, lo: int, hi: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < lo or value > hi:
        raise ValueError(f"{label} must be in [{lo}, {hi}]")
    return value


def _safe_code(code: str) -> str:
    if not re.fullmatch(r"[A-Z0-9_:-]{1,128}", code):
        return "TRANSPORT_ERROR"
    return code


def _safe_message(message: str) -> str:
    text = str(message).splitlines()[0]
    text = re.sub(r"/[^\s]+", "[path]", text)
    text = text.replace("Traceback", "error")
    if "PRIVATE KEY" in text or "ssh-" in text:
        text = "sensitive error redacted"
    return text[:200]


def _has_control(value: str) -> bool:
    return any(ord(ch) < 32 or ord(ch) == 127 for ch in value)


def _unlink_quiet(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass
