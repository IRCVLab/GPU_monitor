#!/usr/bin/env python3
"""Host side of deploy/bootstrap-agent.sh: install or revert the bootstrap tier.

Runs as root on one storage host, from the payload directory that
bootstrap-agent.sh copied there (target-design §4.5, one-time bootstrap):

  install  in three phases.
           1. Preflight, writing nothing: no scan running, the central key
              found, agent.yaml computed, the bundle's signature verified
              against the payload's allowed_signers, the release self-tested
              from a temp copy, sudoers checked with `visudo -cf`.
           2. Additive: back up what it will replace (once), create the
              directories, install the gate, the updater, the signers and
              agent.yaml, activate the bundle through the update unit, and
              `systemd-analyze verify` the scan units against the release.
           3. Disruptive: stop the timer, give `monitoring` the forced command,
              swap sudoers and the scan units, remove the legacy files, enable
              the timer, print `observe`.
           A failure in phase 2 or 3 of a first bootstrap reverts at once.
  revert   restore authorized_keys, sudoers, units and the old /opt/storage-viz
           from that backup and re-enable the old timer if it was enabled.

`install` needs --central-speaks-gate: today's central reads hosts over SFTP and
a Python probe, which the forced command refuses, so a host must not be
bootstrapped before central has the gate transport (M3b). Both ship together.

Both converge when rerun. Standard library only, Python 3.8.
"""
from __future__ import annotations

import argparse
import fcntl
import importlib.util
import json
import os
import pwd
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable, Dict, List, NamedTuple, Optional, Sequence, Tuple

GROUP = "storage-viz-collector"
USER = "monitoring"
UPDATE_UNIT = "storage-viz-agent-update.service"
SCAN_UNITS = ("storage-viz-scan.service", "storage-viz-scheduled-scan.service", "storage-viz-scan.timer")
TIMER = "storage-viz-scan.timer"
LEGACY_OPT = ("agent", "scanner", "storage_fstypes.py")
SCANNER_PATH = "/opt/storage-viz/current/bin/hstscan"
GATE = "/usr/local/lib/storage-viz/gate"
KEY_TYPES = ("ssh-ed25519", "ssh-rsa", "ssh-dss")
# Options of central's existing line that still apply under the forced command.
KEEP_OPTIONS = ("from", "cert-authority", "principals", "expiry-time")


class KeyLine(NamedTuple):
    options: List[str]  # raw option tokens, quoting kept
    keytype: str
    blob: str
    comment: str


def _is_key_type(token: str) -> bool:
    return (token in KEY_TYPES or token.startswith(("ecdsa-sha2-", "sk-ecdsa-sha2-", "sk-ssh-ed25519"))
            or token.endswith("-cert-v01@openssh.com"))


def _split_unquoted(text: str, separators: str, stop_at_space: bool = False) -> Tuple[List[str], str]:
    """Split on separators outside double quotes (backslash escapes a quote)."""
    parts, current, quoted, index = [], [], False, 0
    while index < len(text):
        char = text[index]
        if char == "\\" and quoted and index + 1 < len(text):
            current.append(text[index:index + 2])
            index += 2
            continue
        if char == '"':
            quoted = not quoted
        elif not quoted and stop_at_space and char in " \t":
            break
        elif not quoted and char in separators:
            parts.append("".join(current))
            current = []
            index += 1
            continue
        current.append(char)
        index += 1
    parts.append("".join(current))
    return parts, text[index:]


def parse_key_line(line: str) -> Optional[KeyLine]:
    """An authorized_keys key line, or None for blank lines, comments and lines sshd ignores."""
    text = line.strip()
    if not text or text.startswith("#"):
        return None
    options: List[str] = []
    if not _is_key_type(text.split()[0]):
        options, rest = _split_unquoted(text, ",", stop_at_space=True)
        text = rest.strip()
    fields = text.split(None, 2)
    if len(fields) < 2 or not _is_key_type(fields[0]):
        return None
    return KeyLine(options, fields[0], fields[1], fields[2] if len(fields) > 2 else "")


def restricted_line(key: KeyLine) -> str:
    kept = [o for o in key.options if o.split("=", 1)[0].strip().lower() in KEEP_OPTIONS]
    options = ",".join([f'restrict,command="{GATE}"'] + kept)
    return " ".join(part for part in (options, key.keytype, key.blob, key.comment) if part) + "\n"


def authorized_keys_text(original: str, central: Optional[str]) -> Tuple[str, List[str]]:
    """monitoring's new authorized_keys, and the other active keys it leaves as they were.

    Central's key line (from `central`, or the one active key in `original`)
    gets `restrict,command=<gate>` and keeps its from=, cert-authority,
    principals= and expiry-time= options. Every other line, comments included,
    is kept byte for byte.
    """
    lines = original.splitlines(True)
    parsed = [parse_key_line(line) for line in lines]
    active = [key for key in parsed if key]
    if central is not None:
        central_keys = [key for key in map(parse_key_line, central.splitlines()) if key]
    elif len(active) == 1:
        central_keys = active
    else:
        raise BootstrapError(f"monitoring has {len(active)} active keys; pass --monitoring-key with central's key")
    if not central_keys:
        raise BootstrapError("no central key for monitoring: pass --monitoring-key or keep its authorized_keys")
    blobs = {key.blob for key in central_keys}
    out, written, others = [], set(), []
    for line, key in zip(lines, parsed):
        if key and key.blob in blobs:
            if key.blob not in written:
                out.append(restricted_line(key))
                written.add(key.blob)
            continue
        out.append(line if line.endswith("\n") else line + "\n")
        if key:
            others.append(f"{key.keytype} {key.comment}".strip())
    for key in central_keys:
        if key.blob not in written:
            out.append(restricted_line(key))
            written.add(key.blob)
    return "".join(out), others


class BootstrapError(Exception):
    pass


class Host:
    """Root-only actions. A test passes `fake=True` and a temporary root."""

    def __init__(self, root: Path = Path("/"), fake: bool = False,
                 fail: Optional[Callable[[List[str]], bool]] = None) -> None:
        self.root = root
        self.fake = fake
        self.fail = fail or (lambda argv: False)
        self.commands: List[List[str]] = []

    def path(self, absolute: str) -> Path:
        return self.root / absolute.lstrip("/")

    def run(self, argv: Sequence[str], check: bool = True) -> subprocess.CompletedProcess:
        self.commands.append(list(argv))
        if self.fake and self.fail(list(argv)):
            if check:
                raise BootstrapError(f"{' '.join(argv)} failed (injected)")
            return subprocess.CompletedProcess(list(argv), 1, "", "")
        if self.fake and argv[0] not in ("visudo", "cp"):
            return subprocess.CompletedProcess(list(argv), 0, "", "")
        done = subprocess.run(list(argv), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True, check=False)
        if check and done.returncode != 0:
            raise BootstrapError(f"{' '.join(argv)} failed ({done.returncode}):\n{done.stdout}")
        return done

    def chown(self, path: Path, user: str, group: str) -> None:
        if not self.fake:
            shutil.chown(str(path), user, group)

    def monitoring_home(self) -> Path:
        if self.fake:
            return self.path(f"/home/{USER}")
        return Path(pwd.getpwnam(USER).pw_dir)


class Bootstrap:
    def __init__(self, host: Host, payload: Path) -> None:
        self.host = host
        self.payload = payload
        p = host.path
        self.backup = p("/var/backups/storage-viz-bootstrap")
        self.data = p("/var/lib/storage-viz")
        self.etc = p("/etc/storage-viz")
        self.units = p("/etc/systemd/system")
        self.opt = p("/opt/storage-viz")

    # -- what bootstrap replaces, relative to the root, plus the monitoring key dir
    def managed(self) -> Dict[str, Path]:
        p = self.host.path
        paths = {rel: p("/" + rel) for rel in (
            "etc/sudoers.d/storage-viz-monitoring", "etc/sudoers.d/storage-viz",
            "etc/storage-viz/agent.yaml", "etc/storage-viz/allowed_signers",
            "usr/local/lib/storage-viz", "opt/storage-viz",
            "var/lib/storage-viz/requests", "var/lib/storage-viz/update",
        )}
        for unit in SCAN_UNITS + (UPDATE_UNIT,):
            paths[f"etc/systemd/system/{unit}"] = self.units / unit
        try:
            paths["monitoring-ssh"] = self.host.monitoring_home() / ".ssh"
        except KeyError:
            pass  # no monitoring account yet: nothing of it to back up
        return paths

    # -- backup and revert -------------------------------------------------------

    def take_backup(self) -> None:
        manifest_path = self.backup / "manifest.json"
        if manifest_path.exists():
            print(f"[=] keeping the existing pre-bootstrap backup in {self.backup}")
            return
        shutil.rmtree(str(self.backup), ignore_errors=True)  # an interrupted backup is redone whole
        (self.backup / "files").mkdir(parents=True, mode=0o700)
        os.chmod(str(self.backup), 0o700)
        state = {}
        for key, path in self.managed().items():
            present = os.path.lexists(str(path))
            state[key] = "present" if present else "absent"
            if present:
                target = self.backup / "files" / key
                target.parent.mkdir(parents=True, exist_ok=True)
                self.host.run(["cp", "-a", str(path), str(target)])
        directories = {}
        for path, _mode, _group in self.directory_plan():
            if path.is_dir():
                st = os.lstat(str(path))
                directories[str(path.relative_to(self.host.root))] = [st.st_mode & 0o7777, st.st_uid, st.st_gid]
            else:
                directories[str(path.relative_to(self.host.root))] = None
        enabled = self.host.run(["systemctl", "is-enabled", TIMER], check=False).stdout.strip() == "enabled"
        record = {"paths": state, "directories": directories, "timer_enabled": enabled,
                  "monitoring_existed": "monitoring-ssh" in state, "taken_unix": int(time.time())}
        _write(manifest_path, json.dumps(record, indent=2, sort_keys=True) + "\n", 0o600)
        print(f"[+] backed up {sum(v == 'present' for v in state.values())} paths to {self.backup}")

    def revert(self) -> None:
        manifest_path = self.backup / "manifest.json"
        if not manifest_path.exists():
            raise BootstrapError(f"no complete backup at {self.backup}; nothing to revert")
        record = json.loads(manifest_path.read_text())
        self.host.run(["systemctl", "disable", "--now", TIMER], check=False)
        managed = self.managed()
        for key, state in sorted(record["paths"].items()):
            path = managed.get(key)
            if path is None:
                continue
            _remove(path)
            if state == "present":
                path.parent.mkdir(parents=True, exist_ok=True)
                self.host.run(["cp", "-a", str(self.backup / "files" / key), str(path)])
        for rel, owner in sorted(record["directories"].items(), reverse=True):
            path = self.host.root / rel
            if owner is None:
                if path.is_dir() and not any(path.iterdir()):
                    path.rmdir()
            elif path.is_dir():
                os.chmod(str(path), owner[0])
                if not self.host.fake:
                    os.chown(str(path), owner[1], owner[2])
        self.host.run(["systemctl", "daemon-reload"])
        if record["timer_enabled"]:
            self.host.run(["systemctl", "enable", "--now", TIMER])
        done = self.backup.with_name(f"{self.backup.name}.reverted-{int(time.time())}")
        os.rename(str(self.backup), str(done))
        print(f"[+] reverted; the backup moved to {done}")
        if not record["monitoring_existed"]:
            print(f"[!] bootstrap created the {USER} account; it is left in place")

    # -- install -------------------------------------------------------------------

    def install(self, server_id: Optional[str], monitoring_keys: Optional[Path], central_speaks_gate: bool = False) -> None:
        if not central_speaks_gate:
            raise BootstrapError("central reaches hosts over SFTP until M3b ships the gate transport; "
                                 "bootstrap only with that release (--central-speaks-gate)")
        # 1. Preflight: everything that can fail, writing nothing.
        self.require_idle()
        keys_text, other_keys = self.authorized_keys(monitoring_keys)
        config = self.agent_config(server_id)
        metadata = self.preflight_bundle(config)
        self.host.run(["visudo", "-cf", str(self.payload / "bootstrap/sudoers/storage-viz")])
        first = not (self.backup / "manifest.json").exists()
        self.take_backup()
        try:
            # 2. Additive: nothing the legacy agent or today's central uses changes.
            self.ensure_identity()
            self.ensure_directories()
            self.install_tier()
            self.write_agent_config(config)
            self.activate_bundle(metadata)
            self.verify_scan_units()
            # 3. Disruptive.
            self.host.run(["systemctl", "stop", TIMER], check=False)
            self.require_idle()
            self.write_authorized_keys(keys_text)
            self.swap_sudoers()
            self.install_units(SCAN_UNITS)
            for name in LEGACY_OPT:
                _remove(self.opt / name)
            self.host.run(["systemctl", "enable", "--now", TIMER])
        except (BootstrapError, OSError, ValueError, KeyError) as error:
            if not first:
                raise BootstrapError(f"{error}\n(rerun stopped part way; rerun it, or use --revert)")
            print(f"[!] {error}\n[!] reverting the bootstrap", file=sys.stderr)
            self.revert()
            raise BootstrapError(f"bootstrap failed and was reverted: {error}")
        for key in other_keys:
            print(f"[!] monitoring keeps another key without the forced command: {key}")
        print(self.host.run(["runuser", "-u", USER, "--", "env", "SSH_ORIGINAL_COMMAND=observe", GATE], check=False).stdout)
        print("[+] bootstrap complete")

    def payload_updater(self):
        """The updater module in the payload: the same signature and archive checks the host runs."""
        spec = importlib.util.spec_from_file_location("payload_update", str(self.payload / "bootstrap/update.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def preflight_bundle(self, config: dict) -> dict:
        update = self.payload_updater()
        metadata = json.loads((self.payload / "agent/storage-viz-agent.json").read_text())
        bundle = (self.payload / "agent/storage-viz-agent.tar.gz").read_bytes()
        signature = (self.payload / "agent/storage-viz-agent.tar.gz.sig").read_bytes()
        with tempfile.TemporaryDirectory(prefix="storage-viz-bootstrap.") as tmp:
            if not update.verify_signature(self.payload / "bootstrap/allowed_signers", bundle, signature, Path(tmp)):
                raise BootstrapError("the bundle's signature does not verify against the payload's allowed_signers")
            try:
                members, manifest = update.read_bundle(bundle, metadata["source_sha"])
            except update.Failed as failure:
                raise BootstrapError(f"the bundle is invalid: {failure}")
            if manifest["bootstrap_version"] > update.BOOTSTRAP_VERSION:
                raise BootstrapError(f"the bundle needs bootstrap {manifest['bootstrap_version']}")
            release = Path(tmp) / "release"
            update.extract(members, release)
            config_path = Path(tmp) / "agent.yaml"
            config_path.write_text(json.dumps(dict(config, scanner_path=str(release / "bin/hstscan"))))
            done = self.host.run([str(release / "bin/storage-viz-agent"), "selftest", "--config", str(config_path)], check=False)
            if done.returncode != 0:
                raise BootstrapError(f"the release self-test failed on this host:\n{done.stdout}")
        return metadata

    def require_idle(self) -> None:
        lock = self.host.path("/run/storage-viz/scan.lock")
        if not lock.exists():
            return
        fd = os.open(str(lock), os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise BootstrapError("a scan is running; rerun the bootstrap after it ends")
        finally:
            os.close(fd)

    def ensure_identity(self) -> None:
        if self.host.run(["getent", "group", GROUP], check=False).returncode != 0:
            self.host.run(["groupadd", "--system", GROUP])
        if self.host.run(["id", USER], check=False).returncode != 0:
            self.host.run(["useradd", "--system", "--create-home", "--shell", "/bin/sh", USER])
        self.host.run(["usermod", "-a", "-G", GROUP, USER])
        if not self.host.fake and pwd.getpwnam(USER).pw_shell.endswith(("nologin", "false")):
            raise BootstrapError(f"{USER} needs a login shell: sshd runs the forced command through it")

    def authorized_keys(self, monitoring_keys: Optional[Path]) -> Tuple[str, List[str]]:
        """The new authorized_keys text, from the pre-bootstrap file so reruns agree."""
        backed_up = self.backup / "files/monitoring-ssh/authorized_keys"
        try:
            live = self.host.monitoring_home() / ".ssh/authorized_keys"
        except KeyError:
            live = None
        source = backed_up if (self.backup / "manifest.json").exists() else live
        original = source.read_text() if source is not None and source.exists() else ""
        central = monitoring_keys.read_text() if monitoring_keys else None
        return authorized_keys_text(original, central)

    def write_authorized_keys(self, text: str) -> None:
        # Root owns the home, .ssh and the file (StrictModes accepts root), so the
        # monitoring account can neither edit nor rename its own restriction.
        home = self.host.monitoring_home()
        ssh_dir = home / ".ssh"
        for path in (home, ssh_dir):
            path.mkdir(parents=True, exist_ok=True)
            os.chmod(str(path), 0o755)
            self.host.chown(path, "root", "root")
        _write(ssh_dir / "authorized_keys", text, 0o644)
        self.host.chown(ssh_dir / "authorized_keys", "root", "root")

    def directory_plan(self):
        """(path, mode, group) of every directory install creates or chmods, for the backup."""
        try:
            home = ((self.host.monitoring_home(), 0o755, "root"),)  # chowned to root with authorized_keys
        except KeyError:
            home = ()
        return home + self.data_directories()

    def data_directories(self):
        """(path, mode, group) of the directories ensure_directories creates, today's modes kept."""
        return (
            (self.data, 0o750, GROUP), (self.data / "snapshots", 0o750, GROUP),
            # setgid: files the gate writes as monitoring get the collector group, which
            # the updater and scan units can read without CAP_DAC_OVERRIDE.
            (self.data / "requests", 0o2770, GROUP), (self.data / "update", 0o750, GROUP),
            (self.data / "update/incoming", 0o2770, GROUP), (self.host.path("/run/storage-viz"), 0o750, GROUP),
            (self.opt, 0o755, "root"), (self.opt / "releases", 0o755, "root"),
            (self.host.path("/usr/local/lib/storage-viz"), 0o755, "root"), (self.etc, 0o755, "root"),
        )

    def ensure_directories(self) -> None:
        for path, mode, group in self.data_directories():
            path.mkdir(parents=True, exist_ok=True)
            os.chmod(str(path), mode)
            self.host.chown(path, "root", group)

    def install_tier(self) -> None:
        lib = self.host.path("/usr/local/lib/storage-viz")
        self.install_file(self.payload / "bootstrap/gate.py", lib / "gate", 0o755)
        self.install_file(self.payload / "bootstrap/update.py", lib / "storage-viz-update", 0o755)
        self.install_file(self.payload / "bootstrap/allowed_signers", self.etc / "allowed_signers", 0o644)
        self.install_units((UPDATE_UNIT,))

    def swap_sudoers(self) -> None:
        sudoers = self.host.path("/etc/sudoers.d")
        staged = sudoers / ".storage-viz.new"
        self.install_file(self.payload / "bootstrap/sudoers/storage-viz", staged, 0o440)
        self.host.run(["visudo", "-cf", str(staged)])
        os.replace(str(staged), str(sudoers / "storage-viz"))
        _remove(sudoers / "storage-viz-monitoring")  # the old blocking `systemctl start` rule

    def install_units(self, names: Sequence[str]) -> None:
        for name in names:
            self.install_file(self.payload / "bootstrap/systemd" / name, self.units / name, 0o644)
        self.host.run(["systemd-analyze", "verify", *[str(self.units / name) for name in names]])
        self.host.run(["systemctl", "daemon-reload"])

    def verify_scan_units(self) -> None:
        """Check the scan units against the activated release before they replace the old ones."""
        with tempfile.TemporaryDirectory(prefix="storage-viz-units.") as tmp:
            for name in SCAN_UNITS:
                shutil.copyfile(str(self.payload / "bootstrap/systemd" / name), str(Path(tmp) / name))
            self.host.run(["systemd-analyze", "verify", *[str(Path(tmp) / name) for name in SCAN_UNITS]])

    def install_file(self, source: Path, dest: Path, mode: int) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(f".{dest.name}.tmp")
        shutil.copyfile(str(source), str(tmp))
        os.chmod(str(tmp), mode)
        self.host.chown(tmp, "root", "root")
        os.replace(str(tmp), str(dest))

    def agent_config(self, server_id: Optional[str]) -> dict:
        agent_yaml = self.etc / "agent.yaml"
        if agent_yaml.exists():
            config = json.loads(agent_yaml.read_text())
            if server_id and config.get("server_id") != server_id:
                raise BootstrapError(f"--server-id {server_id} differs from agent.yaml server_id {config.get('server_id')}")
            return config
        scanner_yaml = self.etc / "scanner.yaml"
        config = json.loads(scanner_yaml.read_text()) if scanner_yaml.exists() else {
            "server_id": server_id or socket.gethostname().split(".")[0],
            "threads": 4, "prune_home_mb": 50, "prune_data_mb": 100, "top": 200, "stale_days": 180,
        }
        if server_id and config.get("server_id") != server_id:
            raise BootstrapError(f"--server-id {server_id} differs from scanner.yaml server_id {config.get('server_id')}")
        config["scanner_path"] = SCANNER_PATH
        return config

    def write_agent_config(self, config: dict) -> None:
        agent_yaml = self.etc / "agent.yaml"
        if agent_yaml.exists():
            print(f"[=] keeping {agent_yaml}")
            return
        _write(agent_yaml, json.dumps(config, indent=2, sort_keys=True) + "\n", 0o644)
        print(f"[+] wrote {agent_yaml} for server_id {config['server_id']}")

    def activate_bundle(self, metadata: dict) -> None:
        sha = metadata["source_sha"]
        incoming = self.data / "update/incoming"
        for suffix in (".tar.gz", ".tar.gz.sig"):
            self.install_file(self.payload / f"agent/storage-viz-agent{suffix}", incoming / f"{sha}{suffix}", 0o640)
        _write(incoming / "activate", f"activate {sha}\n", 0o640)
        self.host.run(["systemctl", "start", UPDATE_UNIT], check=False)
        if self.host.fake:
            return
        state = json.loads((self.data / "update/state.json").read_text())
        if state.get("active") != sha:
            raise BootstrapError(f"the updater did not activate {sha}: {state.get('last_attempt')}")
        print(f"[+] agent release {sha} (release_seq {metadata['release_seq']}) is active")


def _write(path: Path, text: str, mode: int) -> None:
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.")
    with os.fdopen(fd, "w") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(tmp, mode)
    os.replace(tmp, str(path))


def _remove(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(str(path))
    elif os.path.lexists(str(path)):
        path.unlink()


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("action", choices=("install", "revert"))
    parser.add_argument("--payload", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--server-id")
    parser.add_argument("--monitoring-key", type=Path, help="central's public key(s) for this host")
    parser.add_argument("--central-speaks-gate", action="store_true",
                        help="central runs the gate transport (M3b); required for install")
    args = parser.parse_args(argv)
    if os.geteuid() != 0:
        print("ERROR: run as root (bootstrap-agent.sh uses sudo)", file=sys.stderr)
        return 1
    bootstrap = Bootstrap(Host(), args.payload)
    try:
        if args.action == "install":
            bootstrap.install(args.server_id, args.monitoring_key, args.central_speaks_gate)
        else:
            bootstrap.revert()
    except (BootstrapError, OSError, ValueError, KeyError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
