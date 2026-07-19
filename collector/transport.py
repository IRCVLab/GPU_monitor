"""Fixed OpenSSH/SFTP transport for remote storage-viz snapshots."""
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
import tempfile
try:
    import resource
except ImportError:  # pragma: no cover - non-POSIX fallback
    resource = None
from typing import Any, Mapping, Optional, Protocol, Tuple

from collector.inventory import Server

STATUS_PATH = "/var/lib/storage-viz/scan-status.json"
SNAPSHOT_DIR = "/var/lib/storage-viz/snapshots"
RESCAN_COMMAND = ("sudo", "-n", "/usr/bin/systemctl", "start", "storage-viz-scan.service")
ACTIVE_STATE_COMMAND = ("/usr/bin/systemctl", "show", "--property=ActiveState", "--value", "storage-viz-scan.service")
ACTIVE_SCAN_STATES = frozenset({"active", "activating", "reloading"})
KNOWN_ACTIVE_STATES = frozenset({"active", "reloading", "inactive", "failed", "activating", "deactivating", "maintenance"})
MAX_STATUS_BYTES = 64 * 1024
MAX_SNAPSHOT_BYTES = 512 * 1024 * 1024
DEFAULT_CONNECT_TIMEOUT_SECONDS = 10
DEFAULT_STATUS_TIMEOUT_SECONDS = 30
DEFAULT_SNAPSHOT_TIMEOUT_SECONDS = 3600
DEFAULT_RESCAN_TIMEOUT_SECONDS = 6 * 60 * 60
DEFAULT_ACTIVE_STATE_TIMEOUT_SECONDS = 10
MAX_RESCAN_TIMEOUT_SECONDS = 24 * 60 * 60
GENERATION_RE = re.compile(r"^(?P<server_id>[A-Za-z0-9_.-]+)-(?P<started>[0-9]+)-v1\.json$")
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")


class TransportError(Exception):
    """Bounded typed transport failure without sensitive process details."""

    def __init__(self, code: str, message: str):
        self.code = _safe_code(code)
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

    def fetch_snapshot(self, server: Server, expected_status: Mapping[str, Any] | None = None) -> Tuple[dict[str, Any], bytes]:
        first = dict(expected_status) if expected_status is not None else self.fetch_status(server)
        generation = validate_snapshot_status(first, server, self._max_snapshot_bytes)
        remote = f"{SNAPSHOT_DIR}/{generation}"
        tmp_name = self._mk_secure_temp(".snapshot")
        try:
            self._run_sftp(server, _batch_get(remote, tmp_name), timeout=self._snapshot_timeout, limit_bytes=min(first["byte_size"], self._max_snapshot_bytes), limit_code="SNAPSHOT_TOO_LARGE", local_path=tmp_name)
            data = _read_regular_bounded(tmp_name, self._max_snapshot_bytes, "SNAPSHOT_TOO_LARGE")
            if len(data) != first["byte_size"]:
                raise TransportError("SIZE_MISMATCH", "snapshot size changed during download")
            second = self.fetch_status(server)
            if status_tuple(first) != status_tuple(second):
                raise TransportError("RACE", "status changed during snapshot download")
            return second, data
        finally:
            _unlink_quiet(tmp_name)

    def scan_active_state(self, server: Server) -> str:
        self._validate_server(server)
        result = self._run_ssh(server, list(ACTIVE_STATE_COMMAND), timeout=self._active_state_timeout)
        _check_returncode(result, "ACTIVE_STATE_FAILED")
        raw = getattr(result, "stdout", b"")
        if isinstance(raw, str):
            text = raw
        elif isinstance(raw, (bytes, bytearray)):
            text = bytes(raw).decode("utf-8", errors="replace")
        else:
            raise TransportError("BAD_ACTIVE_STATE", "active state output is malformed")
        if len(text) > 64 or text.count("\n") > 1:
            raise TransportError("BAD_ACTIVE_STATE", "active state output is malformed")
        state = text.strip()
        if state not in KNOWN_ACTIVE_STATES:
            raise TransportError("BAD_ACTIVE_STATE", "active state output is malformed")
        return state

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

    def _run_sftp(self, server: Server, batch: bytes, *, timeout: int, limit_bytes: int, limit_code: str, local_path: str) -> Any:
        argv = self._sftp_argv(server)
        try:
            result = self._runner.run(argv, input=batch, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, check=False, shell=False, preexec_fn=_fsize_limiter(limit_bytes))
        except (subprocess.TimeoutExpired, TimeoutError) as exc:
            raise TransportError("TIMEOUT", "transport command timed out") from exc
        except Exception as exc:
            raise TransportError("UNREACHABLE", "transport command failed") from exc
        _check_returncode(result, "UNREACHABLE", limit_code=limit_code, local_path=local_path, limit_bytes=limit_bytes)
        return result

    def _run_ssh(self, server: Server, command: list[str], *, timeout: int) -> Any:
        argv = self._ssh_argv(server) + ["--", server.host] + command
        try:
            result = self._runner.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, check=False, shell=False)
        except (subprocess.TimeoutExpired, TimeoutError) as exc:
            raise TransportError("TIMEOUT", "transport command timed out") from exc
        except Exception as exc:
            raise TransportError("UNREACHABLE", "transport command failed") from exc
        return result

    def _common_options(self, server: Server) -> list[str]:
        return [
            "-o", "BatchMode=yes",
            "-o", "StrictHostKeyChecking=yes",
            "-o", "IdentitiesOnly=yes",
            "-o", f"ConnectTimeout={self._connect_timeout}",
            "-o", f"UserKnownHostsFile={server.known_hosts_file}",
            "-o", f"User={server.username}",
            "-i", str(server.identity_file),
        ]

    def _sftp_argv(self, server: Server) -> list[str]:
        return ["sftp", *self._common_options(server), "-P", str(server.port), "-b", "-", server.host]

    def _ssh_argv(self, server: Server) -> list[str]:
        return ["ssh", *self._common_options(server), "-p", str(server.port)]

    def _validate_server(self, server: Server) -> None:
        if server.username != "monitoring":
            raise TransportError("BAD_INVENTORY", "server username must be monitoring")
        if not isinstance(server.host, str) or any(ch in server.host for ch in " ;|&`$\\\n\r\t/[]{}()<>"):
            raise TransportError("BAD_INVENTORY", "server host is unsafe")
        if not isinstance(server.port, int) or isinstance(server.port, bool) or not 1 <= server.port <= 65535:
            raise TransportError("BAD_INVENTORY", "server port is unsafe")
        for label, value in (("identity", str(server.identity_file)), ("known_hosts", str(server.known_hosts_file))):
            if not value.startswith("/etc/storage-viz/") or _has_control(value):
                raise TransportError("BAD_INVENTORY", f"{label} path is unsafe")


def _fsize_limiter(limit_bytes: int):
    """Return a POSIX preexec_fn that only applies RLIMIT_FSIZE before exec.

    This bounds bytes the OpenSSH/SFTP child can write to local temp files on
    supported POSIX central hosts. On hosts without the standard-library
    resource module, the callback is a no-op and normal pre/post size checks
    still apply.
    """
    cap = _bounded_positive(limit_bytes, "file size limit", 1, MAX_SNAPSHOT_BYTES)

    def apply_limit() -> None:
        if resource is not None:
            resource.setrlimit(resource.RLIMIT_FSIZE, (cap, cap))

    apply_limit.limit_bytes = cap  # type: ignore[attr-defined]
    return apply_limit


def _batch_get(remote: str, local: str) -> bytes:
    if _has_control(remote) or _has_control(local):
        raise TransportError("BAD_PATH", "batch path is unsafe")
    return f"get {shlex.quote(remote)} {shlex.quote(local)}\n".encode("utf-8")


def validate_snapshot_status(status: Mapping[str, Any], server: Server, max_snapshot_bytes: int) -> str:
    generation = _safe_generation(status.get("generation"), server, status.get("scan_started_unix"))
    if status.get("server_id") != server.id:
        raise TransportError("BAD_STATUS", "status server_id mismatch")
    if status.get("status") not in {"complete", "partial"}:
        raise TransportError("BAD_STATUS", "status is not downloadable")
    if not isinstance(status.get("config_digest"), str) or not HEX64_RE.fullmatch(status["config_digest"]):
        raise TransportError("BAD_STATUS", "status config digest is invalid")
    if not isinstance(status.get("scan_finished_unix"), int) or isinstance(status.get("scan_finished_unix"), bool) or status["scan_finished_unix"] < 0:
        raise TransportError("BAD_STATUS", "status timestamp is invalid")
    size = status.get("byte_size")
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        raise TransportError("BAD_STATUS", "snapshot size is invalid")
    if size > max_snapshot_bytes:
        raise TransportError("SNAPSHOT_TOO_LARGE", "snapshot exceeds maximum size")
    sha = status.get("sha256")
    if not isinstance(sha, str) or not HEX64_RE.fullmatch(sha):
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
    if not isinstance(status.get("config_digest"), str) or not HEX64_RE.fullmatch(status["config_digest"]):
        raise TransportError("BAD_STATUS", "status config digest is invalid")
    if not isinstance(status.get("scan_finished_unix"), int) or isinstance(status.get("scan_finished_unix"), bool) or status["scan_finished_unix"] < 0:
        raise TransportError("BAD_STATUS", "status timestamp is invalid")
    if status["status"] in {"complete", "partial"}:
        if not isinstance(status.get("byte_size"), int) or isinstance(status.get("byte_size"), bool) or status["byte_size"] <= 0:
            raise TransportError("BAD_STATUS", "snapshot size is invalid")
        if not isinstance(status.get("sha256"), str) or not HEX64_RE.fullmatch(status["sha256"]):
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
