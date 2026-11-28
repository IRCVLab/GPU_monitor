"""Fixed OpenSSH transports to storage hosts.

``GateTransport`` speaks the host gate (design §4.5): one verb per ssh exchange,
answered by a header line and a counted body.

"""
from __future__ import annotations

from dataclasses import dataclass
import os
import pathlib
import re
import stat
import signal
import subprocess
import sys
import tempfile
from typing import Any, Dict, Optional, Pattern, Protocol, Sequence, Tuple

from collector.ids import RELEASE_SHA_RE, REQUEST_ID_RE, SHA256_RE, VOLUME_ID_RE
from collector.inventory import Server

KNOWN_ACTIVE_STATES = frozenset({"active", "reloading", "inactive", "failed", "activating", "deactivating", "maintenance"})
DEFAULT_CONNECT_TIMEOUT_SECONDS = 10
DEFAULT_STATUS_TIMEOUT_SECONDS = 30
DEFAULT_CAPTURE_TIMEOUT_SECONDS = 3600
SSH_ERROR_EXIT = 255
AUTH_FAILURE_MARKERS = ("Permission denied", "Host key verification failed", "REMOTE HOST IDENTIFICATION HAS CHANGED")
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


GATE_HEADER_RE = re.compile(rb"storage-viz-gate/([1-9][0-9]{0,3}) (?:ok (0|[1-9][0-9]{0,9})|err ([A-Z0-9_]{1,64}))\n")
MAX_GATE_HEADER_BYTES = 64
NOT_GATE = "NOT_GATE"
"""The host answered without the gate's header line: it is not bootstrapped, or the bootstrap was reverted."""
GATE_CODES = frozenset({
    "USAGE", "IO_ERROR", "START_FAILED", "FILE_TOO_LARGE", "OUTPUT_TOO_LARGE", "SHORT_READ", "BAD_SIGNATURE_SIZE",
    "CAPTURE_GONE", "MANIFEST_MISSING", "UPDATES_MANUAL",
})
"""The gate's own refusal codes (agent/bootstrap/gate.py), kept as the error code. Any other code a host sends
becomes GATE_REFUSED with the host's text in ``host_code``, so a host can never claim one of central's own
codes (NOT_GATE, TIMEOUT, UNREACHABLE, AUTH_FAILED), which only central's observations produce."""
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
    "get-capture": GateVerb((VOLUME_ID_RE, SHA256_RE), 512 * MiB, DEFAULT_CAPTURE_TIMEOUT_SECONDS, "CAPTURE_TOO_LARGE"),
    "rescan": GateVerb((REQUEST_ID_RE,), 1 * KiB, DEFAULT_STATUS_TIMEOUT_SECONDS),
    "stage-update": GateVerb((RELEASE_SHA_RE, STAGE_SIZE_RE), 1 * KiB, 600),
    "activate-update": GateVerb((RELEASE_SHA_RE,), 1 * KiB, DEFAULT_STATUS_TIMEOUT_SECONDS),
    "rollback-update": GateVerb((RELEASE_SHA_RE,), 1 * KiB, DEFAULT_STATUS_TIMEOUT_SECONDS),
}
_SIZED_VERBS = frozenset({"get-capture"})
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


def classify_ssh_failure(stderr: Any) -> str:
    """Exit 255 is ssh's own failure. A refused key or a changed host key is AUTH_FAILED, the rest UNREACHABLE."""
    text = stderr.decode("utf-8", "replace") if isinstance(stderr, (bytes, bytearray)) else str(stderr or "")
    return "AUTH_FAILED" if any(marker in text for marker in AUTH_FAILURE_MARKERS) else "UNREACHABLE"


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
