#!/usr/bin/env python3
"""Host side of deploy/bootstrap-agent.sh: install or revert the bootstrap tier.

Runs as root on one storage host, from the payload directory that
bootstrap-agent.sh copied there (target-design §4.5, one-time bootstrap):

  install  back up what it replaces (once), give `monitoring` the forced
           command, create the directories, install the gate, the updater, the
           pinned signers, sudoers (`visudo -cf`) and units
           (`systemd-analyze verify`), write agent.yaml from scanner.yaml,
           activate the bundle through the updater, enable the timer and print
           `observe`.
  revert   restore authorized_keys, sudoers, units and the old /opt/storage-viz
           from that backup and re-enable the old timer if it was enabled.

Both converge when rerun. Standard library only, Python 3.8.
"""
from __future__ import annotations

import argparse
import fcntl
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
from typing import Dict, List, Optional, Sequence

GROUP = "storage-viz-collector"
USER = "monitoring"
UPDATE_UNIT = "storage-viz-agent-update.service"
SCAN_UNITS = ("storage-viz-scan.service", "storage-viz-scheduled-scan.service", "storage-viz-scan.timer")
TIMER = "storage-viz-scan.timer"
LEGACY_OPT = ("agent", "scanner", "storage_fstypes.py")
SCANNER_PATH = "/opt/storage-viz/current/bin/hstscan"
GATE = "/usr/local/lib/storage-viz/gate"


class BootstrapError(Exception):
    pass


class Host:
    """Root-only actions. A test passes `fake=True` and a temporary root."""

    def __init__(self, root: Path = Path("/"), fake: bool = False) -> None:
        self.root = root
        self.fake = fake
        self.commands: List[List[str]] = []

    def path(self, absolute: str) -> Path:
        return self.root / absolute.lstrip("/")

    def run(self, argv: Sequence[str], check: bool = True) -> subprocess.CompletedProcess:
        self.commands.append(list(argv))
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

    def install(self, server_id: Optional[str], monitoring_keys: Optional[Path]) -> None:
        self.host.run(["systemctl", "stop", TIMER], check=False)
        self.require_idle()
        self.take_backup()
        self.ensure_identity()
        self.write_authorized_keys(monitoring_keys)
        self.ensure_directories()
        self.install_tier()
        self.write_agent_config(server_id)
        self.activate_bundle()
        self.install_scan_units()
        for name in LEGACY_OPT:
            _remove(self.opt / name)
        self.host.run(["systemctl", "enable", "--now", TIMER])
        print(self.host.run(["runuser", "-u", USER, "--", "env", "SSH_ORIGINAL_COMMAND=observe", GATE], check=False).stdout)
        print("[+] bootstrap complete")

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

    def write_authorized_keys(self, monitoring_keys: Optional[Path]) -> None:
        ssh_dir = self.host.monitoring_home() / ".ssh"
        source = monitoring_keys or self.backup / "files/monitoring-ssh/authorized_keys"
        keys = []
        for line in source.read_text().splitlines() if source.exists() else []:
            fields = line.split()
            # Keep "type base64 [comment]" and drop any options before the key type.
            start = next((i for i, f in enumerate(fields) if f.startswith(("ssh-", "ecdsa-", "sk-"))), None)
            if start is not None and len(fields) > start + 1:
                keys.append(" ".join(fields[start:start + 3]))
        if not keys:
            raise BootstrapError("no central key for monitoring: pass --monitoring-key or keep its authorized_keys")
        ssh_dir.mkdir(parents=True, exist_ok=True)
        # Root owns both, so monitoring cannot change its own restriction.
        os.chmod(str(ssh_dir), 0o755)
        self.host.chown(ssh_dir, "root", "root")
        text = "".join(f'restrict,command="{GATE}" {key}\n' for key in sorted(set(keys)))
        _write(ssh_dir / "authorized_keys", text, 0o644)
        self.host.chown(ssh_dir / "authorized_keys", "root", "root")

    def directory_plan(self):
        """(path, mode, group) of every directory install creates or chmods, today's modes kept."""
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
        for path, mode, group in self.directory_plan():
            path.mkdir(parents=True, exist_ok=True)
            os.chmod(str(path), mode)
            self.host.chown(path, "root", group)

    def install_tier(self) -> None:
        lib = self.host.path("/usr/local/lib/storage-viz")
        self.install_file(self.payload / "bootstrap/gate.py", lib / "gate", 0o755)
        self.install_file(self.payload / "bootstrap/update.py", lib / "storage-viz-update", 0o755)
        self.install_file(self.payload / "bootstrap/allowed_signers", self.etc / "allowed_signers", 0o644)
        sudoers = self.host.path("/etc/sudoers.d")
        staged = sudoers / ".storage-viz.new"
        self.install_file(self.payload / "bootstrap/sudoers/storage-viz", staged, 0o440)
        self.host.run(["visudo", "-cf", str(staged)])
        os.replace(str(staged), str(sudoers / "storage-viz"))
        _remove(sudoers / "storage-viz-monitoring")  # the old blocking `systemctl start` rule
        self.install_units((UPDATE_UNIT,))

    def install_units(self, names: Sequence[str]) -> None:
        for name in names:
            self.install_file(self.payload / "bootstrap/systemd" / name, self.units / name, 0o644)
        self.host.run(["systemd-analyze", "verify", *[str(self.units / name) for name in names]])
        self.host.run(["systemctl", "daemon-reload"])

    def install_scan_units(self) -> None:
        self.install_units(SCAN_UNITS)

    def install_file(self, source: Path, dest: Path, mode: int) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(f".{dest.name}.tmp")
        shutil.copyfile(str(source), str(tmp))
        os.chmod(str(tmp), mode)
        self.host.chown(tmp, "root", "root")
        os.replace(str(tmp), str(dest))

    def write_agent_config(self, server_id: Optional[str]) -> None:
        agent_yaml = self.etc / "agent.yaml"
        if agent_yaml.exists():
            print(f"[=] keeping {agent_yaml}")
            return
        scanner_yaml = self.etc / "scanner.yaml"
        config = json.loads(scanner_yaml.read_text()) if scanner_yaml.exists() else {
            "server_id": server_id or socket.gethostname().split(".")[0],
            "threads": 4, "prune_home_mb": 50, "prune_data_mb": 100, "top": 200, "stale_days": 180,
        }
        if server_id and config.get("server_id") != server_id:
            raise BootstrapError(f"--server-id {server_id} differs from scanner.yaml server_id {config.get('server_id')}")
        config["scanner_path"] = SCANNER_PATH
        _write(agent_yaml, json.dumps(config, indent=2, sort_keys=True) + "\n", 0o644)
        print(f"[+] wrote {agent_yaml} for server_id {config['server_id']}")

    def activate_bundle(self) -> None:
        metadata = json.loads((self.payload / "agent/storage-viz-agent.json").read_text())
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
    args = parser.parse_args(argv)
    if os.geteuid() != 0:
        print("ERROR: run as root (bootstrap-agent.sh uses sudo)", file=sys.stderr)
        return 1
    bootstrap = Bootstrap(Host(), args.payload)
    try:
        if args.action == "install":
            bootstrap.install(args.server_id, args.monitoring_key)
        else:
            bootstrap.revert()
    except (BootstrapError, OSError, ValueError, KeyError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
