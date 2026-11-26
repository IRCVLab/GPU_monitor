#!/usr/bin/python3 -I
"""storage-viz-update: verify, self-test and activate an agent release (bootstrap tier).

The gate writes `update/incoming/activate` with one line, `activate <sha>` or
`rollback <sha>`, and starts storage-viz-agent-update.service, which runs this
as root. The steps follow target-design §4.5:

1. Take /run/storage-viz/scan.lock without waiting; if a scan holds it, record
   BUSY and exit 75, leaving the request for the next start.
2. An installed release is re-checked against its manifest. A new bundle is
   read once into memory, then its signature (`ssh-keygen -Y verify` against
   the pinned allowed_signers), manifest, rollback floor and bootstrap version
   are checked. A sha or archive digest in the bad list is refused.
3. Safe extract to releases/<sha>.tmp: regular files only, no absolute or `..`
   paths, no links or devices, modes 0644 or 0755, every digest checked.
4. Self-test the release under the scan unit's sandbox (systemd-run --pipe).
   A failure adds the sha and digest to `bad` and leaves `current` alone.
5. Rename releases/<sha>.tmp to releases/<sha>, then swap the `current`
   symlink with one rename and record {active, previous, bad, last_attempt}.

`rollback <sha>` is the bad-run signal: central sends it when a release fails
its first real run. It re-activates the recorded previous release and adds the
failed one to `bad`, so it never activates again.

Every step reads its state from disk, so a rerun after a crash converges.
Standard library only, Python 3.8. The update channel never replaces this file.
"""
from __future__ import annotations

import errno
import fcntl
import hashlib
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import time
from pathlib import Path, PurePosixPath
from typing import Callable, Dict, Optional, Tuple

BOOTSTRAP_VERSION = 1
APPLICATION = "storage-viz-agent"
SIGNING_NAMESPACE = "storage-viz-agent"
SIGNER_IDENTITY = "storage-viz-release"
MAX_BUNDLE = 64 * 1024 * 1024
MAX_SIGNATURE = 4096
MAX_MEMBERS = 256
MAX_EXPANDED = 256 * 1024 * 1024
KEEP_RELEASES = 3
EXIT_BUSY = 75
SHA40 = re.compile(r"[0-9a-f]{40}")
MANIFEST = "RELEASE-MANIFEST.json"
# The scan units' sandbox (systemd/storage-viz-scan.service). test_update
# checks that the unit file and this list agree, so the self-test runs a
# release under exactly the confinement its scans will have.
SCAN_SANDBOX = (
    "User=root",
    "Group=storage-viz-collector",
    "UMask=0027",
    "NoNewPrivileges=yes",
    "ProtectSystem=strict",
    "ProtectHome=read-only",
    "ReadWritePaths=/var/lib/storage-viz /run/storage-viz",
    "PrivateTmp=yes",
    "ProtectKernelTunables=yes",
    "ProtectKernelModules=yes",
    "ProtectControlGroups=yes",
    "RestrictAddressFamilies=AF_UNIX",
    "CapabilityBoundingSet=CAP_DAC_READ_SEARCH",
    "AmbientCapabilities=CAP_DAC_READ_SEARCH",
    "LockPersonality=yes",
    "MemoryDenyWriteExecute=yes",
    "RestrictRealtime=yes",
    "SystemCallArchitectures=native",
)


class Failed(Exception):
    """A refused or failed attempt; `code` goes into update/state.json."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


class Paths:
    def __init__(self, root: Path = Path("/")) -> None:
        self.opt = root / "opt/storage-viz"
        self.releases = self.opt / "releases"
        self.current = self.opt / "current"
        self.update = root / "var/lib/storage-viz/update"
        self.incoming = self.update / "incoming"
        self.state = self.update / "state.json"
        self.scan_lock = root / "run/storage-viz/scan.lock"
        self.signers = root / "etc/storage-viz/allowed_signers"
        self.config = root / "etc/storage-viz/agent.yaml"


def verify_signature(signers: Path, bundle: bytes, signature: bytes, scratch: Path) -> bool:
    sig_path = scratch / f".verify-{os.getpid()}.sig"
    sig_path.write_bytes(signature)
    try:
        done = subprocess.run(
            ["ssh-keygen", "-Y", "verify", "-f", str(signers), "-I", SIGNER_IDENTITY,
             "-n", SIGNING_NAMESPACE, "-s", str(sig_path)],
            input=bundle, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    finally:
        sig_path.unlink()
    return done.returncode == 0


def sandboxed_selftest(release: Path, config: Path) -> Tuple[bool, str]:
    argv = ["systemd-run", "--pipe", "--wait", "--quiet", "--collect"]
    for prop in SCAN_SANDBOX + ("TemporaryFileSystem=/tmp",):
        argv += ["-p", prop]
    argv += [str(release / "bin/storage-viz-agent"), "selftest", "--config", str(config)]
    try:
        done = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, timeout=300, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, type(exc).__name__
    return done.returncode == 0, done.stdout.decode("utf-8", "replace")[-400:]


class Updater:
    def __init__(
        self,
        paths: Paths,
        verify: Callable[[Path, bytes, bytes, Path], bool] = verify_signature,
        selftest: Callable[[Path, Path], Tuple[bool, str]] = sandboxed_selftest,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.paths = paths
        self.verify = verify
        self.selftest = selftest
        self.clock = clock

    # -- state -----------------------------------------------------------------

    def read_state(self) -> dict:
        try:
            state = json.loads(self.paths.state.read_text(encoding="utf-8"))
        except FileNotFoundError:
            state = {}
        except (OSError, ValueError):
            state = {}  # rebuilt below from what is on disk
        active = self.current_sha()
        bad = [b for b in state.get("bad", []) if isinstance(b, dict)] if isinstance(state, dict) else []
        previous = state.get("previous") if isinstance(state, dict) else None
        if not (isinstance(previous, str) and (self.paths.releases / previous).is_dir() and previous != active):
            previous = None
        return {"schema": "storage-viz.update/1", "active": active, "previous": previous, "bad": bad,
                "last_attempt": state.get("last_attempt") if isinstance(state, dict) else None}

    def write_state(self, state: dict) -> None:
        _write_atomic(self.paths.state, (json.dumps(state, sort_keys=True) + "\n").encode(), 0o640)

    def current_sha(self) -> Optional[str]:
        try:
            target = os.readlink(str(self.paths.current))
        except OSError:
            return None
        name = PurePosixPath(target).name
        return name if SHA40.fullmatch(name) and (self.paths.releases / name).is_dir() else None

    # -- the run ---------------------------------------------------------------

    def run(self) -> int:
        for directory in (self.paths.releases, self.paths.incoming, self.paths.scan_lock.parent):
            directory.mkdir(parents=True, exist_ok=True)
        lock_fd = os.open(str(self.paths.scan_lock), os.O_CREAT | os.O_RDWR | os.O_CLOEXEC, 0o640)
        try:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                if exc.errno not in (errno.EAGAIN, errno.EACCES, errno.EWOULDBLOCK):
                    raise
                state = self.read_state()
                state["last_attempt"] = self._attempt(None, None, "busy", "BUSY")
                self.write_state(state)
                return EXIT_BUSY
            return self._locked_run()
        finally:
            os.close(lock_fd)

    def _locked_run(self) -> int:
        if not (self.paths.incoming / "activate").exists():
            return 0  # nothing requested: a rerun after success changes nothing
        state = self.read_state()
        action, sha = None, None
        try:
            action, sha = self._read_request()
            if action == "rollback":
                outcome = self._rollback(state, sha)
            else:
                outcome = self._activate(state, sha)
            code, status = None, 0
        except Failed as failure:
            outcome, code, status = "refused" if failure.code in REFUSALS else "failed", failure.code, 1
            print(f"storage-viz-update: {failure}", file=sys.stderr)
        state["last_attempt"] = self._attempt(action, sha, outcome, code)
        self.write_state(state)
        _unlink(self.paths.incoming / "activate")
        self._prune(state)
        return status

    def _attempt(self, action, sha, outcome, code) -> dict:
        return {"action": action, "sha": sha, "outcome": outcome, "code": code, "finished_unix": int(self.clock())}

    def _read_request(self) -> Tuple[str, str]:
        try:
            raw = _read_regular(self.paths.incoming / "activate", 128)
        except FileNotFoundError:
            raise Failed("REQUEST_INVALID", "not a regular file")
        parts = raw.decode("ascii", "replace").strip().split(" ")
        if len(parts) != 2 or parts[0] not in ("activate", "rollback") or not SHA40.fullmatch(parts[1]):
            raise Failed("REQUEST_INVALID")
        return parts[0], parts[1]

    def _rollback(self, state: dict, sha: str) -> str:
        failed = state["active"]
        if failed is None or state["previous"] != sha:
            raise Failed("ROLLBACK_TARGET", f"previous is {state['previous']}")
        self._check_installed(sha)
        failed_manifest = self.release_manifest(failed)
        state["bad"].append({"sha": failed, "archive_sha256": failed_manifest.get("archive_sha256")})
        self._switch(sha)
        state.update(active=sha, previous=None)
        return "rolled_back"

    def _activate(self, state: dict, sha: str) -> str:
        if any(entry.get("sha") == sha for entry in state["bad"]):
            raise Failed("BAD_RELEASE")
        if state["active"] == sha:
            self._check_installed(sha)
            return "already_active"
        if (self.paths.releases / sha).is_dir():
            manifest = self._check_installed(sha)
            self._check_policy(state, manifest)
        else:
            self._install(state, sha)
        self._switch(sha)
        state.update(previous=state["active"], active=sha)
        for suffix in (".tar.gz", ".tar.gz.sig"):
            _unlink(self.paths.incoming / f"{sha}{suffix}")
        return "activated"

    # -- checks ----------------------------------------------------------------

    def release_manifest(self, sha: str) -> dict:
        try:
            return json.loads((self.paths.releases / sha / MANIFEST).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _check_installed(self, sha: str) -> dict:
        release = self.paths.releases / sha
        manifest = self.release_manifest(sha)
        files = manifest.get("files")
        if manifest.get("source_sha") != sha or not isinstance(files, dict):
            raise Failed("INSTALLED_CORRUPT", "manifest")
        on_disk = {str(p.relative_to(release)) for p in release.rglob("*") if not p.is_dir()} - {MANIFEST}
        if on_disk != set(files):
            raise Failed("INSTALLED_CORRUPT", "file set")
        for rel, digest in files.items():
            path = release / rel
            if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise Failed("INSTALLED_CORRUPT", rel)
        return manifest

    def _check_policy(self, state: dict, manifest: dict) -> None:
        floor = self.release_manifest(state["active"]).get("rollback_floor_seq", 0) if state["active"] else 0
        if manifest["release_seq"] < floor:
            raise Failed("FLOOR", f"release_seq {manifest['release_seq']} < floor {floor}")
        if manifest["bootstrap_version"] > BOOTSTRAP_VERSION:
            raise Failed("BOOTSTRAP_TOO_OLD", f"needs bootstrap {manifest['bootstrap_version']}")

    def _install(self, state: dict, sha: str) -> None:
        try:
            bundle = _read_regular(self.paths.incoming / f"{sha}.tar.gz", MAX_BUNDLE)
            signature = _read_regular(self.paths.incoming / f"{sha}.tar.gz.sig", MAX_SIGNATURE)
        except FileNotFoundError:
            raise Failed("BUNDLE_MISSING")
        archive_sha256 = hashlib.sha256(bundle).hexdigest()
        if any(entry.get("archive_sha256") == archive_sha256 for entry in state["bad"]):
            raise Failed("BAD_RELEASE", "archive digest")
        if not self.verify(self.paths.signers, bundle, signature, self.paths.update):
            raise Failed("SIGNATURE")
        members, manifest = read_bundle(bundle, sha)
        self._check_policy(state, manifest)
        staging = self.paths.releases / f"{sha}.tmp"
        shutil.rmtree(str(staging), ignore_errors=True)
        extract(members, staging)
        # Recorded so a later rollback can put this archive on the bad list.
        manifest_path = staging / MANIFEST
        manifest_path.write_bytes((json.dumps(dict(manifest, archive_sha256=archive_sha256), sort_keys=True, indent=2) + "\n").encode())
        ok, output = self.selftest(staging, self.paths.config)
        if not ok:
            shutil.rmtree(str(staging), ignore_errors=True)
            state["bad"].append({"sha": sha, "archive_sha256": archive_sha256})
            raise Failed("SELFTEST_FAILED", output)
        _fsync_tree(staging)
        os.rename(str(staging), str(self.paths.releases / sha))
        _fsync_dir(self.paths.releases)

    def _switch(self, sha: str) -> None:
        link = self.paths.opt / "current.new"
        _unlink(link)
        os.symlink(f"releases/{sha}", str(link))
        os.replace(str(link), str(self.paths.current))
        _fsync_dir(self.paths.opt)

    def _prune(self, state: dict) -> None:
        keep = {state["active"], state["previous"]} - {None}
        bad = {entry.get("sha") for entry in state["bad"]}
        others = sorted(
            (p for p in self.paths.releases.iterdir() if p.name not in keep),
            key=lambda p: p.stat().st_mtime, reverse=True,
        )
        spare = KEEP_RELEASES - len(keep)
        for path in others:
            if not SHA40.fullmatch(path.name) or path.name in bad or spare <= 0:
                shutil.rmtree(str(path), ignore_errors=True)
            else:
                spare -= 1


REFUSALS = {"BAD_RELEASE", "FLOOR", "BOOTSTRAP_TOO_OLD", "ROLLBACK_TARGET"}


def read_bundle(bundle: bytes, sha: str) -> Tuple[Dict[str, Tuple[bytes, int]], dict]:
    """Parse the archive in memory: every member a safe regular file, digests matching."""
    members: Dict[str, Tuple[bytes, int]] = {}
    expanded = 0
    try:
        with tarfile.open(fileobj=io.BytesIO(bundle), mode="r:gz") as tar:
            for info in tar:
                if len(members) >= MAX_MEMBERS:
                    raise Failed("UNSAFE_ARCHIVE", "too many members")
                name = info.name
                parts = PurePosixPath(name).parts
                if (not info.isreg() or name.startswith("/") or not parts
                        or any(p in ("", ".", "..") for p in parts) or PurePosixPath(*parts).as_posix() != name):
                    raise Failed("UNSAFE_ARCHIVE", name)
                if info.mode not in (0o644, 0o755) or name in members:
                    raise Failed("UNSAFE_ARCHIVE", f"{name} mode {oct(info.mode)}")
                expanded += info.size
                if expanded > MAX_EXPANDED:
                    raise Failed("UNSAFE_ARCHIVE", "expanded size")
                handle = tar.extractfile(info)
                members[name] = (handle.read() if handle else b"", info.mode)
    except (tarfile.TarError, OSError, EOFError) as exc:
        raise Failed("UNSAFE_ARCHIVE", str(exc))
    try:
        manifest = json.loads(members.pop(MANIFEST)[0])
    except (KeyError, ValueError):
        raise Failed("MANIFEST", "missing or not JSON")
    ints = ("release_seq", "rollback_floor_seq", "bootstrap_version")
    if (not isinstance(manifest, dict) or manifest.get("application") != APPLICATION
            or manifest.get("source_sha") != sha or not isinstance(manifest.get("files"), dict)
            or not all(type(manifest.get(key)) is int and manifest[key] >= 0 for key in ints)
            or manifest["rollback_floor_seq"] > manifest["release_seq"]):
        raise Failed("MANIFEST", "fields")
    if set(manifest["files"]) != set(members):
        raise Failed("DIGEST", "file set differs from manifest")
    for name, (data, _mode) in members.items():
        if hashlib.sha256(data).hexdigest() != manifest["files"][name]:
            raise Failed("DIGEST", name)
    return members, manifest


def extract(members: Dict[str, Tuple[bytes, int]], staging: Path) -> None:
    staging.mkdir(mode=0o755)
    for name in sorted(members):
        data, mode = members[name]
        target = staging / name
        target.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        fd = os.open(str(target), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            os.fchmod(handle.fileno(), mode)


def _read_regular(path: Path, cap: int) -> bytes:
    try:
        fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except OSError as exc:
        raise FileNotFoundError(str(path)) from exc
    with os.fdopen(fd, "rb") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise FileNotFoundError(str(path))
        data = handle.read(cap + 1)
    if len(data) > cap:
        raise Failed("TOO_LARGE", path.name)
    return data


def _write_atomic(path: Path, data: bytes, mode: int) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    with open(str(tmp), "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fchmod(handle.fileno(), mode)
        os.fsync(handle.fileno())
    os.replace(str(tmp), str(path))
    _fsync_dir(path.parent)


def _fsync_dir(path: Path) -> None:
    fd = os.open(str(path), os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _fsync_tree(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_file():
            with open(str(path), "rb") as handle:
                os.fsync(handle.fileno())
        else:
            _fsync_dir(path)
    _fsync_dir(root)


def _unlink(path: Path) -> None:
    try:
        os.unlink(str(path))
    except FileNotFoundError:
        pass


def main() -> int:
    return Updater(Paths()).run()


if __name__ == "__main__":
    sys.exit(main())
