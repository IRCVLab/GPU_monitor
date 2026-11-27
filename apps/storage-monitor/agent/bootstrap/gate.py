#!/usr/bin/python3 -I
"""Forced command of the storage-viz `monitoring` SSH key (bootstrap tier).

authorized_keys runs this for every connection of the key. It reads
SSH_ORIGINAL_COMMAND, splits it on single spaces and serves exactly the verbs
of target-design §4.5. Anything else exits 2 with no side effect. No shell runs.

Every response starts with one header line,
`storage-viz-gate/<bootstrap_version> ok <body bytes>` or
`storage-viz-gate/<bootstrap_version> err <CODE>`, so central can tell a gate
refusal from data and detect a truncated body. Exit status: 0 ok, 1 err, 2 usage.

Standard library only, Python 3.8. Installed root-owned at
/usr/local/lib/storage-viz/gate; the update channel never replaces it.
"""
from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import BinaryIO, Dict, List, Optional, Sequence, Tuple

BOOTSTRAP_VERSION = 1
KiB = 1024
MiB = 1024 * KiB
MAX_COMMAND = 256
MAX_BUNDLE = 64 * MiB
MAX_SIGNATURE = 4 * KiB
MAX_JSON_FILE = 16 * KiB
MAX_MANIFEST = 64 * KiB
MAX_PENDING_REQUESTS = 128
STALE_REQUEST_SECONDS = 24 * 3600
SCAN_UNIT = "storage-viz-scan.service"
UPDATE_UNIT = "storage-viz-agent-update.service"
UNITS = (SCAN_UNIT, "storage-viz-scheduled-scan.service", UPDATE_UNIT)
TIMER = "storage-viz-scan.timer"
ENV = {"LC_ALL": "C", "TZ": "UTC", "PATH": "/usr/bin:/bin"}

SHA40 = re.compile(r"[0-9a-f]{40}")
SHA256 = re.compile(r"[0-9a-f]{64}")
VOLUME_ID = re.compile(r"[vx]-[0-9a-f]{16}")
REQUEST_ID = re.compile(r"[0-9a-f]{32}")
SIZE = re.compile(r"[1-9][0-9]{0,8}")
GENERATION = re.compile(r"[A-Za-z0-9_.-]+-[0-9]+-v1\.json")  # SHIM(M3..M9) S7

# verb -> (argument patterns, output cap in bytes)
VERBS: Dict[str, Tuple[Tuple[re.Pattern, ...], int]] = {
    "observe": ((), 64 * KiB),
    "get-manifest": ((), 64 * KiB),
    "get-capture": ((VOLUME_ID, SHA256), 512 * MiB),
    "rescan": ((REQUEST_ID,), 1 * KiB),
    "stage-update": ((SHA40, SIZE), 1 * KiB),
    "activate-update": ((SHA40,), 1 * KiB),
    "rollback-update": ((SHA40,), 1 * KiB),
    "get-status": ((), 64 * KiB),  # SHIM(M3..M9) S7
    "get-snapshot": ((GENERATION,), 512 * MiB),  # SHIM(M3..M9) S7
}


class Refused(Exception):
    """A domain refusal: the header carries `err <code>` and the gate exits 1."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class Usage(Exception):
    """Not a verb of the grammar: the gate exits 2 and changes nothing."""


class Paths:
    """Host paths of target-design §2.2. Tests pass a temporary root."""

    def __init__(self, root: Path = Path("/")) -> None:
        self.data = root / "var/lib/storage-viz"
        self.run = root / "run/storage-viz"
        self.opt = root / "opt/storage-viz"
        self.etc = root / "etc/storage-viz"
        self.requests = self.data / "requests"
        self.captures = self.data / "captures"
        self.update = self.data / "update"
        self.incoming = self.update / "incoming"
        self.scan_lock = self.run / "scan.lock"


class Host:
    """Side effects outside the data directories. Tests replace it."""

    def start_unit(self, unit: str) -> bool:
        argv = ["/usr/bin/sudo", "-n", "/usr/bin/systemctl", "start", "--no-block", unit]
        return _run(argv, 30) is not None

    def show(self, units: Sequence[str], properties: str) -> str:
        return _run(["/usr/bin/systemctl", "show", "--property=" + properties, *units], 5) or ""

    def agent_health(self, paths: Paths) -> str:
        entry = paths.opt / "current/bin/storage-viz-agent"
        if not entry.exists():
            return "missing"
        config = str(paths.etc / "agent.yaml")
        return "ok" if _run([str(entry), "check-config", "--config", config], 20) is not None else "broken"


def _run(argv: List[str], timeout: int) -> Optional[str]:
    try:
        done = subprocess.run(argv, env=ENV, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout.decode("utf-8", "replace")[:8 * KiB] if done.returncode == 0 else None


def parse(command: Optional[str]) -> Tuple[str, List[str]]:
    if not command or len(command) > MAX_COMMAND:
        raise Usage()
    verb, *args = command.split(" ")
    if verb not in VERBS:
        raise Usage()
    patterns = VERBS[verb][0]
    if len(args) != len(patterns) or not all(p.fullmatch(a) for p, a in zip(patterns, args)):
        raise Usage()
    return verb, args


def _open_regular(path: Path) -> BinaryIO:
    try:
        fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except OSError as exc:
        if exc.errno in (errno.ENOENT, errno.ELOOP, errno.ENOTDIR):
            raise FileNotFoundError(str(path)) from exc
        raise
    handle = os.fdopen(fd, "rb")
    if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
        handle.close()
        raise FileNotFoundError(str(path))
    return handle


def _read_small(path: Path, cap: int = MAX_JSON_FILE) -> Optional[bytes]:
    try:
        with _open_regular(path) as handle:
            data = handle.read(cap + 1)
    except FileNotFoundError:
        return None
    if len(data) > cap:
        raise Refused("FILE_TOO_LARGE")
    return data


def _read_json(path: Path, cap: int = MAX_JSON_FILE) -> object:
    """JSON from a small file, None when absent, {"invalid": true} when unusable."""
    try:
        data = _read_small(path, cap)
        return None if data is None else json.loads(data)
    except (Refused, OSError, ValueError):
        return {"invalid": True}


def lock_held(lock_path: Path, proc_locks: Path = Path("/proc/locks")) -> bool:
    """True when /proc/locks lists a lock on the scan lock's inode.

    The gate never takes the lock itself, so it cannot make a starting run or
    the updater lose it (target-design §3.1).
    """
    try:
        st = os.stat(str(lock_path))
        listing = proc_locks.read_text()
    except OSError:
        return False
    wanted = "%02x:%02x:%d" % (os.major(st.st_dev), os.minor(st.st_dev), st.st_ino)
    return any(wanted in line.split() for line in listing.splitlines())


def _pending_requests(paths: Paths) -> List[str]:
    try:
        names = os.listdir(str(paths.requests))
    except OSError:
        return []
    return sorted(name for name in names if REQUEST_ID.fullmatch(name))


def observe(paths: Paths, host: Host) -> bytes:
    held = lock_held(paths.scan_lock)
    run = _read_json(paths.data / "run.json")
    state = "running" if held else ("interrupted" if run is not None else "idle")
    try:
        manifest_bytes = _read_small(paths.data / "manifest.json", MAX_MANIFEST)
    except (Refused, OSError):
        manifest_bytes = b"\0"  # present but unusable: seq None, and its digest changes with it
    manifest = None
    if manifest_bytes is not None:
        try:
            seq = json.loads(manifest_bytes).get("seq")
        except (ValueError, AttributeError):
            seq = None
        manifest = {"seq": seq, "sha256": hashlib.sha256(manifest_bytes).hexdigest()}
    units = {}
    for block in host.show(UNITS, "Id,ActiveState,InvocationID").strip().split("\n\n"):
        props = dict(line.split("=", 1) for line in block.splitlines() if "=" in line)
        if props.get("Id") in UNITS:
            units[props["Id"]] = {"ActiveState": props.get("ActiveState"), "InvocationID": props.get("InvocationID") or None}
    try:
        boot_id = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except OSError:
        boot_id = None
    current = paths.opt / "current"
    report = {
        "schema": "storage-viz.observe/1",
        "bootstrap_version": BOOTSTRAP_VERSION,
        "now": time.time(),
        "boot_id": boot_id,
        "scan": {"state": state, "run": run if held else None},
        "requests_pending": _pending_requests(paths)[:16],
        "manifest": manifest,
        "progress": _read_json(paths.run / "progress.json"),
        "units": units,
        "update": _read_json(paths.update / "state.json"),
        "agent": {
            "release_sha": os.path.basename(os.readlink(str(current))) if current.is_symlink() else None,
            "health": host.agent_health(paths),
        },
        # SHIM(M3..M9) S7: what REMOTE_OBSERVATION_SCRIPT reads today, so the
        # central v1 telemetry parser keeps working over the gate.
        "legacy": {
            "lifecycle": _read_json(paths.data / "scan-lifecycle.json") or {},
            "published": _read_json(paths.data / "scan-status.json") or {},
            "progress": _read_json(paths.run / "scan-progress.json") or {},
            "services": host.show(UNITS[:2], "ActiveState,StateChangeTimestampMonotonic,"
                                  "ExecMainStartTimestampMonotonic,InactiveExitTimestampMonotonic").strip().split("\n\n"),
            "timer": host.show((TIMER,), "ActiveState,NextElapseUSecRealtime,NextElapseUSecMonotonic,Unit"),
            "remote_unix": time.time(),
            "remote_monotonic": time.clock_gettime(time.CLOCK_MONOTONIC),
        },
    }
    return _fit(report, VERBS["observe"][1])


# Fields observe may shrink, largest first, when the whole report would pass its
# cap. The scan state, units, update state and agent health always stay whole.
DEGRADABLE = (("legacy", "lifecycle"), ("legacy", "published"), ("legacy", "progress"), ("progress",),
              ("legacy", "services"), ("legacy", "timer"), ("scan", "run"))


def _fit(report: dict, cap: int) -> bytes:
    """Serialize the report, replacing the largest degradable fields until it fits."""
    while True:
        body = (json.dumps(report, sort_keys=True) + "\n").encode()
        if len(body) <= cap:
            return body
        sizes = []
        for path in DEGRADABLE:
            parent = report
            for key in path[:-1]:
                parent = parent[key]
            value = parent[path[-1]]
            if not (isinstance(value, dict) and value.get("truncated")):
                sizes.append((len(json.dumps(value)), path, parent))
        if not sizes:
            raise Refused("OUTPUT_TOO_LARGE")
        size, path, parent = max(sizes, key=lambda item: item[0])
        parent[path[-1]] = {"truncated": True, "bytes": size}


def _write_atomic(path: Path, data: bytes) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o640)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(str(tmp), str(path))
    except BaseException:
        try:
            os.unlink(str(tmp))
        except OSError:
            pass
        raise


def rescan(paths: Paths, host: Host, request_id: str) -> bytes:
    target = paths.requests / request_id
    known = target.exists() or _request_recorded(paths, request_id)
    if not known:
        now = time.time()
        pending = _pending_requests(paths)
        for name in pending:  # older than any job deadline: nobody waits for it
            try:
                if now - os.lstat(str(paths.requests / name)).st_mtime > STALE_REQUEST_SECONDS:
                    os.unlink(str(paths.requests / name))
            except OSError:
                pass
        if len(_pending_requests(paths)) >= MAX_PENDING_REQUESTS:
            return b"rejected TOO_MANY_PENDING\n"
        _write_atomic(target, (request_id + "\n").encode())
    if not lock_held(paths.scan_lock) and not host.start_unit(SCAN_UNIT):
        return b"rejected START_FAILED\n"
    return b"already_received\n" if known else b"received\n"


def _request_recorded(paths: Paths, request_id: str) -> bool:
    run = _read_json(paths.data / "run.json")
    if isinstance(run, dict) and request_id in (run.get("request_ids") or []):
        return True
    manifest = _read_json(paths.data / "manifest.json", MAX_MANIFEST)
    receipts = manifest.get("requests") if isinstance(manifest, dict) else None
    return isinstance(receipts, list) and any(isinstance(r, dict) and r.get("request_id") == request_id for r in receipts)


def _read_exact(stream: BinaryIO, size: int) -> bytes:
    chunks = []
    remaining = size
    while remaining:
        chunk = stream.read(min(remaining, MiB))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def stage_update(paths: Paths, stdin: BinaryIO, sha: str, size_text: str) -> bytes:
    size = int(size_text)
    if size > MAX_BUNDLE:
        raise Refused("TOO_LARGE")
    bundle = _read_exact(stdin, size)
    if len(bundle) != size:
        raise Refused("SHORT_READ")
    signature = stdin.read(MAX_SIGNATURE + 1)
    if not signature or len(signature) > MAX_SIGNATURE:
        raise Refused("BAD_SIGNATURE_SIZE")
    for name in os.listdir(str(paths.incoming)):  # one staged bundle at a time bounds the disk use
        if name.endswith((".tar.gz", ".tar.gz.sig")) and not name.startswith(sha):
            os.unlink(str(paths.incoming / name))
    _write_atomic(paths.incoming / f"{sha}.tar.gz.sig", signature)
    _write_atomic(paths.incoming / f"{sha}.tar.gz", bundle)
    return b"staged\n"


def request_update(paths: Paths, host: Host, action: str, sha: str) -> bytes:
    _write_atomic(paths.incoming / "activate", f"{action} {sha}\n".encode())
    if not host.start_unit(UPDATE_UNIT):
        raise Refused("START_FAILED")
    return b"requested\n"


def _stream_file(path: Path, cap: int, missing: str, out: BinaryIO) -> None:
    try:
        handle = _open_regular(path)
    except FileNotFoundError:
        raise Refused(missing)
    with handle:
        size = os.fstat(handle.fileno()).st_size
        if size > cap:
            raise Refused("TOO_LARGE")
        out.write(_header(f"ok {size}"))
        remaining = size
        while remaining:
            chunk = handle.read(min(remaining, MiB))
            if not chunk:
                break  # an immutable file shrank; central sees a short body
            out.write(chunk)
            remaining -= len(chunk)


def _header(text: str) -> bytes:
    return f"storage-viz-gate/{BOOTSTRAP_VERSION} {text}\n".encode()


def serve(command: Optional[str], paths: Paths, host: Host, stdin: BinaryIO, out: BinaryIO) -> int:
    try:
        verb, args = parse(command)
    except Usage:
        out.write(_header("err USAGE"))
        return 2
    cap = VERBS[verb][1]
    os.umask(0o027)
    try:
        if verb == "get-capture":
            _stream_file(paths.captures / args[0] / f"{args[1]}.json.gz", cap, "CAPTURE_GONE", out)
            return 0
        if verb == "get-snapshot":  # SHIM(M3..M9) S7
            _stream_file(paths.data / "snapshots" / args[0], cap, "SNAPSHOT_GONE", out)
            return 0
        if verb == "get-manifest":
            _stream_file(paths.data / "manifest.json", cap, "MANIFEST_MISSING", out)
            return 0
        if verb == "get-status":  # SHIM(M3..M9) S7
            _stream_file(paths.data / "scan-status.json", cap, "STATUS_MISSING", out)
            return 0
        if verb == "observe":
            body = observe(paths, host)
        elif verb == "rescan":
            body = rescan(paths, host, args[0])
        elif verb == "stage-update":
            body = stage_update(paths, stdin, args[0], args[1])
        else:
            body = request_update(paths, host, "activate" if verb == "activate-update" else "rollback", args[0])
        if len(body) > cap:
            raise Refused("OUTPUT_TOO_LARGE")
    except Refused as refusal:
        out.write(_header(f"err {refusal.code}"))
        return 1
    except OSError:
        out.write(_header("err IO_ERROR"))
        return 1
    out.write(_header(f"ok {len(body)}"))
    out.write(body)
    return 0


def main() -> int:
    code = serve(os.environ.get("SSH_ORIGINAL_COMMAND"), Paths(), Host(), sys.stdin.buffer, sys.stdout.buffer)
    sys.stdout.flush()
    return code


if __name__ == "__main__":
    sys.exit(main())
