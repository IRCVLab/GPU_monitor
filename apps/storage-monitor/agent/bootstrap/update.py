#!/usr/bin/python3 -I
"""storage-viz-update: verify, self-test and activate an agent release (bootstrap tier).

The gate writes `update/incoming/activate` with one line, `activate <sha>` or
`rollback <sha>`, and starts storage-viz-agent-update.service, which runs this
as root. The steps follow target-design §4.5:

1. Take /run/storage-viz/scan.lock without waiting; if a scan holds it, record
   BUSY and exit 75, leaving the request for the next start. Otherwise rename
   the request to `activate.processing`, so a request the gate writes during
   the run survives and is processed next in the same run.
2. An installed release is re-checked against its manifest. A new bundle is
   read once into memory, then its signature (`ssh-keygen -Y verify` against
   the pinned allowed_signers), manifest, rollback floor and bootstrap version
   are checked. A sha or archive digest in the bad list is refused.
   The floor is max(floor_high_water, active release's rollback_floor_seq).
   floor_high_water only rises, so central cannot walk a host down one
   signed release at a time.
3. Safe extract to releases/<sha>.tmp: regular files only, no absolute or `..`
   paths, no links or devices, modes 0644 or 0755, every digest checked.
4. Self-test the release under the scan unit's sandbox (systemd-run --pipe).
   A failure leaves `current` alone and backs the sha off (6 h, doubling, at
   most 7 days); it may be environmental, so it is not a permanent mark.
5. Rename releases/<sha>.tmp to releases/<sha>, then swap the `current`
   symlink with one rename and record the state.

Permanent `bad` marks: a signed bundle whose content or floor fails (never on a
mismatch with the requested sha), and a release rolled back by `rollback <sha>`,
the bad-run signal central sends when a release fails its first real run. That
re-activates the recorded previous release only if its release_seq is at least
floor_high_water: a floor raise is final, so a rollback can never undo a fix.

Every step reads its state from disk, so a rerun after a crash converges.
Standard library only, Python 3.8. The update channel never replaces this file.

`update.py --root DIR` is the manual path of a host without units (TrueNAS): the
same steps over the dataset layout, with the self-test run directly instead of
under systemd-run. deploy/bootstrap-truenas.py stages the bundle and runs it.
"""
from __future__ import annotations

import errno
import fcntl
import hashlib
import io
import json
import os
import runpy
import shutil
import stat
import subprocess
import sys
import tarfile
import time
from pathlib import Path, PurePosixPath
from typing import Callable, Dict, Optional, Sequence, Tuple

BOOTSTRAP_VERSION = 2
APPLICATION = "storage-viz-agent"
SIGNING_NAMESPACE = "storage-viz-agent"
SIGNER_IDENTITY = "storage-viz-release"
MAX_BUNDLE = 64 * 1024 * 1024
MAX_SIGNATURE = 4096
MAX_MEMBERS = 256
MAX_EXPANDED = 256 * 1024 * 1024
KEEP_RELEASES = 3
KEEP_BAD = 64
SELFTEST_BACKOFF_BASE = 6 * 3600
SELFTEST_BACKOFF_MAX = 7 * 24 * 3600
MAX_REQUESTS_PER_RUN = 4
EXIT_BUSY = 75
if __package__:
    from .gate import SHA40
else:
    gate_path = Path(__file__).with_name("gate" if Path(__file__).suffix == "" else "gate.py")
    SHA40 = runpy.run_path(str(gate_path))["SHA40"]
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
    """``dataset`` is the gate's one-directory layout (gate.Paths): DIR/opt, DIR/data, DIR/run,
    DIR/agent.yaml, and the pinned signers in DIR/bootstrap beside this file."""

    def __init__(self, root: Path = Path("/"), dataset: Optional[Path] = None) -> None:
        if dataset is None:
            self.opt = root / "opt/storage-viz"
            self.update = root / "var/lib/storage-viz/update"
            self.scan_lock = root / "run/storage-viz/scan.lock"
            self.signers = root / "etc/storage-viz/allowed_signers"
            self.config = root / "etc/storage-viz/agent.yaml"
        else:
            self.opt = dataset / "opt"
            self.update = dataset / "data/update"
            self.scan_lock = dataset / "run/scan.lock"
            self.signers = dataset / "bootstrap/allowed_signers"
            self.config = dataset / "agent.yaml"
        self.releases = self.opt / "releases"
        self.current = self.opt / "current"
        self.incoming = self.update / "incoming"
        self.state = self.update / "state.json"


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


def direct_selftest(release: Path, config: Path) -> Tuple[bool, str]:
    """The self-test without the systemd sandbox, for a host with no units (TrueNAS, ``--root``).
    The cron job runs scans there as plain root, so this is the confinement they have."""
    argv = [str(release / "bin/storage-viz-agent"), "selftest", "--config", str(config)]
    try:
        done = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              env={"LC_ALL": "C", "TZ": "UTC", "PATH": "/usr/bin:/bin"}, timeout=300, check=False)
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
        if not isinstance(state, dict):
            state = {}
        active = self.current_sha()
        bad = [b for b in state.get("bad", []) if isinstance(b, dict)]
        previous = state.get("previous")
        if not (isinstance(previous, str) and (self.paths.releases / previous).is_dir() and previous != active):
            previous = None
        high_water = state.get("floor_high_water")
        high_water = high_water if type(high_water) is int and high_water >= 0 else 0
        if active:  # a lost state file still keeps the active release's floor
            high_water = max(high_water, self._manifest_floor(active))
        backoff = state.get("selftest_backoff")
        backoff = {k: v for k, v in backoff.items() if SHA40.fullmatch(k) and isinstance(v, dict)} if isinstance(backoff, dict) else {}
        return {"schema": "storage-viz.update/1", "active": active, "previous": previous, "bad": bad,
                "floor_high_water": high_water, "selftest_backoff": backoff,
                "last_attempt": state.get("last_attempt")}

    def _manifest_floor(self, sha: str) -> int:
        floor = self.release_manifest(sha).get("rollback_floor_seq", 0)
        return floor if type(floor) is int else 0

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
                pending = self.paths.incoming / "activate"
                if not pending.exists():
                    pending = self._processing
                try:  # name the waiting request, so central sees what is deferred
                    action, sha = self._read_request(pending)
                except Failed:
                    action, sha = None, None
                state["last_attempt"] = self._attempt(action, sha, "busy", "BUSY")
                self.write_state(state)
                return EXIT_BUSY
            return self._locked_run()
        finally:
            os.close(lock_fd)

    @property
    def _processing(self) -> Path:
        return self.paths.incoming / "activate.processing"

    def _claim(self) -> bool:
        """Move the newest request to activate.processing; a crash leftover stays claimed."""
        try:
            os.replace(str(self.paths.incoming / "activate"), str(self._processing))
        except FileNotFoundError:
            pass
        return os.path.lexists(str(self._processing))

    def _locked_run(self) -> int:
        status = 0  # nothing requested: a rerun after success changes nothing
        for _ in range(MAX_REQUESTS_PER_RUN):
            if not self._claim():
                break
            status = self._process_claimed()
        return status

    def _process_claimed(self) -> int:
        state = self.read_state()
        action, sha = None, None
        try:
            action, sha = self._read_request(self._processing)
            if action == "rollback":
                outcome = self._rollback(state, sha)
            else:
                outcome = self._activate(state, sha)
            code, status = None, 0
        except Failed as failure:
            outcome, code, status = "refused" if failure.code in REFUSALS else "failed", failure.code, 1
            print(f"storage-viz-update: {failure}", file=sys.stderr)
        except OSError as error:  # ENOSPC, a file/directory clash in the bundle, ...
            outcome, code, status = "failed", "IO_ERROR", 1
            print(f"storage-viz-update: IO_ERROR: {error}", file=sys.stderr)
        state["bad"] = state["bad"][-KEEP_BAD:]
        state["last_attempt"] = self._attempt(action, sha, outcome, code)
        self.write_state(state)
        _unlink(self._processing)
        self._prune(state)
        return status

    def _attempt(self, action, sha, outcome, code) -> dict:
        return {"action": action, "sha": sha, "outcome": outcome, "code": code, "finished_unix": int(self.clock())}

    def _read_request(self, path: Path) -> Tuple[str, str]:
        try:
            raw = _read_regular(path, 128)
        except FileNotFoundError:
            raise Failed("REQUEST_INVALID", "the request vanished")
        parts = raw.decode("ascii", "replace").strip().split(" ")
        if len(parts) != 2 or parts[0] not in ("activate", "rollback") or not SHA40.fullmatch(parts[1]):
            raise Failed("REQUEST_INVALID")
        return parts[0], parts[1]

    def _rollback(self, state: dict, sha: str) -> str:
        failed = state["active"]
        if failed is None or state["previous"] != sha:
            raise Failed("ROLLBACK_TARGET", f"previous is {state['previous']}")
        manifest = self._check_installed(sha)
        # A floor raise (a security fix, a schema boundary) is final: rolling back past it
        # would let central undo the fix and ban it. Routine rollbacks pass, because the
        # trailing floor sits 20 releases back.
        if manifest.get("release_seq", -1) < state["floor_high_water"]:
            raise Failed("FLOOR", f"previous release_seq {manifest.get('release_seq')} < floor {state['floor_high_water']}")
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
        backoff = state["selftest_backoff"].get(sha)
        if backoff and self.clock() < backoff.get("retry_after", 0):
            raise Failed("SELFTEST_BACKOFF", f"retry after {backoff['retry_after']}")
        if (self.paths.releases / sha).is_dir():
            manifest = self._check_installed(sha)
            self._check_policy(state, manifest)
        else:
            manifest = self._install(state, sha)
        self._switch(sha)
        state["selftest_backoff"].pop(sha, None)
        state["floor_high_water"] = max(state["floor_high_water"], manifest["rollback_floor_seq"])
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

    def _check_floor(self, state: dict, manifest: dict) -> None:
        floor = max(state["floor_high_water"], self._manifest_floor(state["active"]) if state["active"] else 0)
        if manifest["release_seq"] < floor:
            raise Failed("FLOOR", f"release_seq {manifest['release_seq']} < floor {floor}")

    def _check_policy(self, state: dict, manifest: dict) -> None:
        self._check_floor(state, manifest)
        if manifest["bootstrap_version"] > BOOTSTRAP_VERSION:
            raise Failed("BOOTSTRAP_TOO_OLD", f"needs bootstrap {manifest['bootstrap_version']}")

    def _install(self, state: dict, sha: str) -> dict:
        try:
            bundle = _read_regular(self.paths.incoming / f"{sha}.tar.gz", MAX_BUNDLE)
            signature = _read_regular(self.paths.incoming / f"{sha}.tar.gz.sig", MAX_SIGNATURE)
        except FileNotFoundError:
            raise Failed("BUNDLE_MISSING")
        archive_sha256 = hashlib.sha256(bundle).hexdigest()
        if any(entry.get("archive_sha256") == archive_sha256 for entry in state["bad"]):
            raise Failed("BAD_RELEASE", "archive digest")
        if not self.verify(self.paths.signers, bundle, signature, self.paths.update):
            raise Failed("SIGNATURE")  # no mark: anyone who can stage can send garbage
        try:
            members, manifest = read_bundle(bundle, sha)
            self._check_floor(state, manifest)
        except Failed as failure:
            # A signed bundle that is broken or below the floor never becomes valid. Mark only
            # what does not depend on the name central requested: an archive whose manifest
            # cannot be trusted marks its digest alone, and a name mismatch marks nothing.
            if failure.code == "UNSAFE_ARCHIVE":
                state["bad"].append({"sha": None, "archive_sha256": archive_sha256})
            elif failure.code != "SHA_MISMATCH":
                state["bad"].append({"sha": sha, "archive_sha256": archive_sha256})
            raise
        self._check_policy(state, manifest)  # BOOTSTRAP_TOO_OLD clears once the host's bootstrap tier is upgraded
        staging = self.paths.releases / f"{sha}.tmp"
        shutil.rmtree(str(staging), ignore_errors=True)
        extract(members, staging)
        # Recorded so a later rollback can put this archive on the bad list.
        manifest_path = staging / MANIFEST
        manifest_path.write_bytes((json.dumps(dict(manifest, archive_sha256=archive_sha256), sort_keys=True, indent=2) + "\n").encode())
        ok, output = self.selftest(staging, self.paths.config)
        if not ok:
            shutil.rmtree(str(staging), ignore_errors=True)
            failures = state["selftest_backoff"].get(sha, {}).get("failures", 0) + 1
            delay = min(SELFTEST_BACKOFF_BASE * 2 ** (failures - 1), SELFTEST_BACKOFF_MAX)
            state["selftest_backoff"][sha] = {"failures": failures, "retry_after": int(self.clock()) + delay}
            raise Failed("SELFTEST_FAILED", output)
        _fsync_tree(staging)
        os.rename(str(staging), str(self.paths.releases / sha))
        _fsync_dir(self.paths.releases)
        return manifest

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


REFUSALS = {"BAD_RELEASE", "FLOOR", "BOOTSTRAP_TOO_OLD", "ROLLBACK_TARGET", "SELFTEST_BACKOFF"}


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
    if isinstance(manifest, dict) and manifest.get("source_sha") != sha:
        # Central named a different sha than CI signed: says nothing about either release.
        raise Failed("SHA_MISMATCH", f"bundle is {manifest.get('source_sha')!r}")
    ints = ("release_seq", "rollback_floor_seq", "bootstrap_version")
    if (not isinstance(manifest, dict) or manifest.get("application") != APPLICATION
            or not isinstance(manifest.get("files"), dict)
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
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise Failed("UNREADABLE", f"{path.name}: {exc.strerror}") from exc
    with os.fdopen(fd, "rb") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise Failed("UNREADABLE", f"{path.name}: not a regular file")
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


def main(argv: Sequence[str] = ()) -> int:
    """No arguments: the update unit. ``--root DIR``: a manual run on a host with the dataset layout,
    where deploy/bootstrap-truenas.py has staged the bundle and written the request."""
    args = list(argv)
    if not args:
        return Updater(Paths()).run()
    if len(args) != 2 or args[0] != "--root" or not os.path.isabs(args[1]) or os.path.normpath(args[1]) != args[1]:
        print("usage: update.py [--root DIR]", file=sys.stderr)
        return 2
    return Updater(Paths(dataset=Path(args[1])), selftest=direct_selftest).run()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
