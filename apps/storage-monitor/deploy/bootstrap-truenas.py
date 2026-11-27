#!/usr/bin/env python3
"""Set up, upgrade or revert the storage-viz agent on the TrueNAS SCALE host (Nexus).

Runs on central and drives the host over SSH as its admin account (truenas_admin).
TrueNAS regenerates /etc and keeps / and /usr read-only, so nothing goes there: the
agent, its data and the gate live in the pool dataset tank/storage-viz, and the
user, its key and the cron job persist through the middleware (midclt).

  install   create the dataset and the storage-viz-collector group if missing, lay
            out /mnt/tank/storage-viz, install gate.py, update.py, the pinned
            allowed_signers and agent.yaml, install the signed bundle through
            `update.py --root` (signature, tar checks, self-test, then `current`),
            create or update the `monitoring` user with the gate as its forced
            command, create or update the cron job, then run one `observe`
            through the gate with central's key.
  upgrade   install a newer signed bundle the same way. update.py keeps the
            previous release for a manual rollback. This is how the host gets
            agent updates: its inventory entry says `agent_updates: manual`.
  revert    remove the cron job and the user. --purge also removes the dataset
            and the group.

Every step reads the host's state first, so a rerun converges and changes nothing
that is already right. The sudo password comes from $STORAGE_VIZ_SUDO_PASSWORD or,
with --sudo-password-stdin, the first line of stdin. It goes to `sudo -S` on
stdin only: never in an argument, never printed. A sudo command gets no other
stdin, so a password sudo did not read cannot land in a file.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT))

from agent.bootstrap import update  # noqa: E402

SERVER_ID = "nexus"
DATASET = "tank/storage-viz"
ROOT = "/mnt/tank/storage-viz"
USER_DATASETS = ("/mnt/tank/datasets", "/mnt/tank/workspace")
GROUP = "storage-viz-collector"
USER = "monitoring"
SHELL = "/usr/bin/sh"  # sshd runs a forced command through the user's shell; nologin would refuse it
CRON_DESCRIPTION = "storage-viz agent"
PYTHON = "/usr/bin/python3"
PASSWORD_ENV = "STORAGE_VIZ_SUDO_PASSWORD"
DEFAULT_BUNDLE_DIR = Path("/opt/storage-viz-dashboard/agent")
BUNDLE = "storage-viz-agent.tar.gz"
SIGNATURE = BUNDLE + ".sig"
METADATA = "storage-viz-agent.json"
BOOTSTRAP_FILES = {  # installed name -> source in this repository
    "gate.py": APP_ROOT / "agent/bootstrap/gate.py",
    "update.py": APP_ROOT / "agent/bootstrap/update.py",
    "allowed_signers": APP_ROOT / "agent/bootstrap/allowed_signers",
}
GATE_COMMAND = f"{PYTHON} -I {ROOT}/bootstrap/gate.py --root {ROOT} --no-units"
AGENT_COMMAND = f"{ROOT}/opt/current/bin/storage-viz-agent run --config {ROOT}/agent.yaml --scheduled"
STAGING_RE = re.compile(r"\A/tmp/storage-viz-setup\.[A-Za-z0-9]{6,}\Z")
KEY_RE = re.compile(r"\A(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp(?:256|384|521)) [A-Za-z0-9+/=]+(?: [\x21-\x7e][\x20-\x7e]*)?\Z")
# Directory, mode, group. The agent runs as root from cron; monitoring (in GROUP) reads
# data and run, and writes only data/requests. setgid gives the agent's files GROUP.
DIRECTORIES = (
    ((ROOT, f"{ROOT}/opt", f"{ROOT}/opt/releases", f"{ROOT}/bootstrap", f"{ROOT}/home"), "0755", "root"),
    ((f"{ROOT}/data", f"{ROOT}/run", f"{ROOT}/data/captures", f"{ROOT}/data/update", f"{ROOT}/data/update/incoming"),
     "2750", GROUP),
    ((f"{ROOT}/data/requests",), "2770", GROUP),
)


class SetupError(Exception):
    pass


class Secret:
    """The sudo password: no repr, no str, only bytes for sudo's stdin."""

    def __init__(self, value: str) -> None:
        if not value or "\n" in value:
            raise SetupError("the sudo password is empty or spans lines")
        self._value = value

    def stdin(self) -> bytes:
        return self._value.encode() + b"\n"

    def scrub(self, text: str) -> str:
        return text.replace(self._value, "***")

    def __repr__(self) -> str:
        return "Secret(***)"

    __str__ = __repr__


@dataclass(frozen=True)
class Reply:
    returncode: int
    stdout: bytes
    stderr: bytes


class SshRunner:
    """Runs one remote command per call: `ssh -i KEY USER@HOST -- COMMAND`."""

    def __init__(self, host: str, port: int, known_hosts: Optional[Path]) -> None:
        self.host, self.port, self.known_hosts = host, port, known_hosts

    def run(self, user: str, key: Path, command: str, stdin: bytes) -> Reply:
        argv = ["ssh", "-i", str(key), "-p", str(self.port), "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
                "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=15"]
        if self.known_hosts is not None:
            argv += ["-o", f"UserKnownHostsFile={self.known_hosts}"]
        argv += [f"{user}@{self.host}", "--", command]
        done = subprocess.run(argv, input=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=900, check=False)
        return Reply(done.returncode, done.stdout, done.stderr)


Runner = Any  # anything with SshRunner.run's signature; tests pass a fake host


class Nexus:
    """The admin's session on the host. Every remote command goes through `call`."""

    def __init__(self, runner: Runner, admin: str, admin_key: Path, password: Optional[Secret],
                 say: Callable[[str], None] = print) -> None:
        self.runner, self.admin, self.admin_key, self.password, self._say = runner, admin, admin_key, password, say

    def say(self, text: str) -> None:
        self._say(self.password.scrub(text) if self.password else text)

    def call(self, argv: Sequence[str], stdin: bytes = b"", check: bool = True, quiet: bool = False) -> Reply:
        command = shlex.join(argv)
        if not quiet:
            self.say(f"$ {command}")
        reply = self.runner.run(self.admin, self.admin_key, command, stdin)
        if check and reply.returncode != 0:
            detail = reply.stderr.decode("utf-8", "replace").strip()[-800:]
            raise SetupError(self.password.scrub(f"`{command}` exited {reply.returncode}: {detail}") if self.password
                             else f"`{command}` exited {reply.returncode}: {detail}")
        return reply

    def sudo(self, argv: Sequence[str], check: bool = True) -> Reply:
        if self.password is None:
            raise SetupError(f"this step needs sudo: set ${PASSWORD_ENV} or pass --sudo-password-stdin")
        return self.call(["sudo", "-S", "-k", "-p", "", "--", *argv], stdin=self.password.stdin(), check=check)

    def midclt(self, method: str, *args: Any) -> Any:
        reply = self.call(["midclt", "call", method, *(json.dumps(arg, sort_keys=True) for arg in args)])
        text = reply.stdout.decode("utf-8", "replace").strip()
        try:
            return json.loads(text) if text else None
        except ValueError:
            raise SetupError(f"midclt {method} printed no JSON: {text[:200]}")

    def query(self, method: str, field: str, value: str) -> List[dict]:
        rows = self.midclt(method, [[field, "=", value]])
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise SetupError(f"{method} returned {rows!r}")
        return rows

    def upload(self, path: str, data: bytes) -> None:
        self.call(["sh", "-c", 'umask 077 && cat > "$1"', "sh", path], stdin=data)


# ------------------------------------------------------------------ central-side inputs


@dataclass(frozen=True)
class Bundle:
    sha: str
    release_seq: int
    data: bytes
    signature: bytes


def read_bundle(bundle_dir: Path, signers: Path) -> Bundle:
    """The dashboard release's agent bundle, checked here before anything reaches the host."""
    try:
        metadata = json.loads((bundle_dir / METADATA).read_text())
        data = (bundle_dir / BUNDLE).read_bytes()
        signature = (bundle_dir / SIGNATURE).read_bytes()
    except (OSError, ValueError) as exc:
        raise SetupError(f"no usable agent bundle in {bundle_dir}: {exc}")
    sha = metadata.get("source_sha") if isinstance(metadata, dict) else None
    if not isinstance(sha, str) or not update.SHA40.fullmatch(sha):
        raise SetupError(f"{bundle_dir / METADATA} names no source_sha")
    with tempfile.TemporaryDirectory(prefix="storage-viz-verify.") as scratch:
        if not update.verify_signature(signers, data, signature, Path(scratch)):
            raise SetupError(f"the bundle's signature does not verify against {signers}")
    try:
        _members, manifest = update.read_bundle(data, sha)
    except update.Failed as failure:
        raise SetupError(f"the bundle fails the updater's archive checks: {failure}")
    if manifest["bootstrap_version"] > update.BOOTSTRAP_VERSION:
        raise SetupError(f"the bundle needs bootstrap {manifest['bootstrap_version']}")
    return Bundle(sha, manifest["release_seq"], data, signature)


def key_line(public_key: Path) -> str:
    """monitoring's sshpubkey: central's key behind the gate as a forced command."""
    lines = [line.strip() for line in public_key.read_text().splitlines() if line.strip() and not line.startswith("#")]
    if len(lines) != 1 or not KEY_RE.fullmatch(lines[0]):
        raise SetupError(f"{public_key} must hold exactly one public key line with no options")
    return f'restrict,command="{GATE_COMMAND}" {lines[0]}'


def agent_config() -> dict:
    return {
        "server_id": SERVER_ID,
        "scanner_path": f"{ROOT}/opt/current/bin/hstscan",
        "data_dir": f"{ROOT}/data",
        "run_dir": f"{ROOT}/run",
        "host_profile": "truenas",
        "volume_aliases": {path: f"{SERVER_ID}-{path.rsplit('/', 1)[1]}" for path in USER_DATASETS},
        "volume_media": {path: "ssd" for path in USER_DATASETS},
        "threads": 4, "prune_home_mb": 50, "prune_data_mb": 100, "top": 200, "stale_days": 180,
    }


def cron_job() -> dict:
    return {"user": "root", "command": AGENT_COMMAND, "description": CRON_DESCRIPTION,
            "schedule": {"minute": "*/5", "hour": "*", "dom": "*", "month": "*", "dow": "*"},
            "enabled": True, "stdout": True, "stderr": False}


def changed(wanted: Mapping[str, Any], row: Mapping[str, Any]) -> Dict[str, Any]:
    return {key: value for key, value in wanted.items() if row.get(key) != value}


# ------------------------------------------------------------------ steps


def ensure_dataset(nexus: Nexus) -> None:
    rows = nexus.query("pool.dataset.query", "id", DATASET)
    if not rows:
        nexus.midclt("pool.dataset.create", {"name": DATASET, "type": "FILESYSTEM", "acltype": "POSIX",
                                             "aclmode": "DISCARD", "exec": "ON"})
        rows = nexus.query("pool.dataset.query", "id", DATASET)
    if len(rows) != 1 or rows[0].get("mountpoint") != ROOT:
        raise SetupError(f"{DATASET} is not mounted at {ROOT}: {[row.get('mountpoint') for row in rows]}")
    if (rows[0].get("exec") or {}).get("value") == "OFF":
        raise SetupError(f"{DATASET} has exec=off; the agent runs from it")


def ensure_group(nexus: Nexus) -> int:
    rows = nexus.query("group.query", "group", GROUP)
    if not rows:
        nexus.midclt("group.create", {"name": GROUP, "smb": False})
        rows = nexus.query("group.query", "group", GROUP)
    if len(rows) != 1 or type(rows[0].get("id")) is not int:
        raise SetupError(f"group {GROUP} did not resolve to one middleware id: {rows}")
    return rows[0]["id"]


def lay_out(nexus: Nexus) -> None:
    for paths, mode, group in DIRECTORIES:
        nexus.sudo(["install", "-d", "-o", "root", "-g", group, "-m", mode, *paths])


def install_files(nexus: Nexus, staging: str) -> None:
    for name, source in BOOTSTRAP_FILES.items():
        nexus.upload(f"{staging}/{name}", source.read_bytes())
        nexus.sudo(["install", "-o", "root", "-g", "root", "-m", "0644", f"{staging}/{name}", f"{ROOT}/bootstrap/{name}"])
    config = (json.dumps(agent_config(), indent=2, sort_keys=True) + "\n").encode()
    nexus.upload(f"{staging}/agent.yaml", config)
    nexus.sudo(["install", "-o", "root", "-g", GROUP, "-m", "0640", f"{staging}/agent.yaml", f"{ROOT}/agent.yaml"])


def install_release(nexus: Nexus, staging: str, bundle: Bundle) -> None:
    """Stage the bundle and run update.py --root: the host verifies, extracts and self-tests it."""
    current = nexus.sudo(["readlink", f"{ROOT}/opt/current"], check=False)
    if current.returncode == 0 and current.stdout.decode().strip() == f"releases/{bundle.sha}":
        nexus.say(f"[=] agent release {bundle.sha} is already current")
        return
    incoming = f"{ROOT}/data/update/incoming"
    for name, data in ((f"{bundle.sha}.tar.gz", bundle.data), (f"{bundle.sha}.tar.gz.sig", bundle.signature),
                       ("activate", f"activate {bundle.sha}\n".encode())):
        nexus.upload(f"{staging}/{name}", data)
        nexus.sudo(["install", "-o", "root", "-g", GROUP, "-m", "0640", f"{staging}/{name}", f"{incoming}/{name}"])
    done = nexus.sudo([PYTHON, "-I", f"{ROOT}/bootstrap/update.py", "--root", ROOT], check=False)
    state_reply = nexus.sudo(["cat", f"{ROOT}/data/update/state.json"], check=False)
    try:
        state = json.loads(state_reply.stdout)
    except ValueError:
        state = {}
    if done.returncode == update.EXIT_BUSY:
        raise SetupError("a scan holds the lock; the request stays queued, rerun once the scan ends")
    if done.returncode != 0 or state.get("active") != bundle.sha:
        raise SetupError(f"update.py did not activate {bundle.sha}: {state.get('last_attempt')} "
                         f"{done.stderr.decode('utf-8', 'replace')[-400:]}")
    nexus.say(f"[+] agent release {bundle.sha} (release_seq {bundle.release_seq}) is current; previous {state.get('previous')}")


def ensure_user(nexus: Nexus, group_id: int, sshpubkey: str) -> None:
    wanted = {"full_name": "storage-viz gate", "group": group_id, "home": f"{ROOT}/home", "shell": SHELL,
              "sshpubkey": sshpubkey, "password_disabled": True}
    rows = nexus.query("user.query", "username", USER)
    if not rows:
        nexus.midclt("user.create", dict(wanted, username=USER, group_create=False, home_create=False, smb=False))
    else:
        row = dict(rows[0], group=(rows[0].get("group") or {}).get("id"))
        diff = changed(wanted, row)
        if diff:
            nexus.midclt("user.update", rows[0]["id"], diff)
        else:
            nexus.say(f"[=] user {USER} is as wanted")
    keys = nexus.sudo(["cat", f"{ROOT}/home/.ssh/authorized_keys"], check=False)
    if keys.returncode != 0 or keys.stdout.decode("utf-8", "replace").strip() != sshpubkey.strip():
        raise SetupError(f"{ROOT}/home/.ssh/authorized_keys does not hold exactly the forced-command line; the "
                         f"middleware may have changed it: {keys.stdout.decode('utf-8', 'replace').strip()[:300]!r}")


def ensure_cron(nexus: Nexus) -> None:
    wanted = cron_job()
    rows = nexus.query("cronjob.query", "description", CRON_DESCRIPTION)
    if len(rows) > 1:
        raise SetupError(f"{len(rows)} cron jobs are named {CRON_DESCRIPTION!r}; remove the extras")
    if not rows:
        nexus.midclt("cronjob.create", wanted)
        return
    diff = changed(wanted, rows[0])
    if diff:
        nexus.midclt("cronjob.update", rows[0]["id"], diff)
    else:
        nexus.say("[=] cron job is as wanted")


def observe(runner: Runner, identity: Path, sha: str, say: Callable[[str], None]) -> dict:
    """One `observe` as central sends it: central's key, the monitoring account, the gate."""
    say(f"$ ssh {USER}@host observe")
    reply = runner.run(USER, identity, "observe", b"")
    header, _, body = reply.stdout.partition(b"\n")
    if reply.returncode != 0 or not re.fullmatch(rb"storage-viz-gate/[0-9]+ ok [0-9]+", header):
        raise SetupError(f"observe through the gate failed: {header[:120]!r} {reply.stderr[-300:]!r}")
    report = json.loads(body)
    agent = report.get("agent") or {}
    if report.get("scheduler") != "cron" or agent.get("release_sha") != sha or agent.get("health") != "ok":
        raise SetupError(f"observe shows scheduler {report.get('scheduler')!r}, agent {agent!r}")
    say(f"[+] observe through the gate: scan {report['scan']['state']}, release {sha}, health ok")
    return report


class Staging:
    """A private scratch directory on the host for files on their way to a sudo install."""

    def __init__(self, nexus: Nexus) -> None:
        self.nexus = nexus
        self.path = ""

    def __enter__(self) -> str:
        path = self.nexus.call(["mktemp", "-d", "/tmp/storage-viz-setup.XXXXXX"]).stdout.decode().strip()
        if not STAGING_RE.fullmatch(path):
            raise SetupError(f"mktemp printed {path!r}")
        self.path = path
        return path

    def __exit__(self, *exc: Any) -> None:
        if self.path:
            self.nexus.call(["rm", "-rf", "--", self.path], check=False)


def install(nexus: Nexus, bundle: Bundle, sshpubkey: str, observer: Callable[[str], dict]) -> None:
    ensure_dataset(nexus)
    group_id = ensure_group(nexus)
    lay_out(nexus)
    with Staging(nexus) as staging:
        install_files(nexus, staging)
        install_release(nexus, staging, bundle)
    ensure_user(nexus, group_id, sshpubkey)
    ensure_cron(nexus)
    observer(bundle.sha)


def upgrade(nexus: Nexus, bundle: Bundle, observer: Optional[Callable[[str], dict]]) -> None:
    rows = nexus.query("pool.dataset.query", "id", DATASET)
    if not rows:
        raise SetupError(f"{DATASET} does not exist: run install first")
    with Staging(nexus) as staging:
        install_release(nexus, staging, bundle)
    if observer is not None:
        observer(bundle.sha)


def revert(nexus: Nexus, purge: bool) -> None:
    for row in nexus.query("cronjob.query", "description", CRON_DESCRIPTION):
        nexus.midclt("cronjob.delete", row["id"])
    for row in nexus.query("user.query", "username", USER):
        nexus.midclt("user.delete", row["id"])
    if not purge:
        nexus.say(f"[=] kept {DATASET} and group {GROUP}; --purge removes them")
        return
    for row in nexus.query("group.query", "group", GROUP):
        nexus.midclt("group.delete", row["id"])
    if nexus.query("pool.dataset.query", "id", DATASET):
        nexus.midclt("pool.dataset.delete", DATASET, {"recursive": True})


def read_password(args: argparse.Namespace, environ: Mapping[str, str], stdin: Any) -> Optional[Secret]:
    if args.sudo_password_stdin:
        return Secret(stdin.readline().rstrip("\n"))
    if environ.get(PASSWORD_ENV):
        return Secret(environ[PASSWORD_ENV])
    return None


def main(argv: Optional[Sequence[str]] = None, runner: Optional[Runner] = None,
         environ: Mapping[str, str] = os.environ, stdin: Any = sys.stdin, say: Callable[[str], None] = print) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--upgrade", action="store_true", help="install a newer signed bundle only")
    action.add_argument("--revert", action="store_true", help="remove the cron job and the user")
    parser.add_argument("--purge", action="store_true", help="with --revert: also remove the dataset and the group")
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, default=22)
    parser.add_argument("--known-hosts", type=Path, help="known_hosts for the host key (default: ssh's own)")
    parser.add_argument("--admin", default="truenas_admin")
    parser.add_argument("--admin-key", type=Path, required=True, help="the admin's private key")
    parser.add_argument("--monitoring-key", type=Path, help="central's public key for monitoring (KEY.pub)")
    parser.add_argument("--monitoring-identity", type=Path, help="its private key, for the final observe (default: KEY)")
    parser.add_argument("--bundle-dir", type=Path, default=DEFAULT_BUNDLE_DIR)
    parser.add_argument("--signers", type=Path, default=BOOTSTRAP_FILES["allowed_signers"])
    parser.add_argument("--sudo-password-stdin", action="store_true", help=f"read the sudo password from stdin, not ${PASSWORD_ENV}")
    args = parser.parse_args(argv)
    if args.purge and not args.revert:
        parser.error("--purge needs --revert")
    if not args.revert and not args.upgrade and args.monitoring_key is None:
        parser.error("install needs --monitoring-key")
    runner = runner or SshRunner(args.host, args.port, args.known_hosts)
    password: Optional[Secret] = None
    try:
        password = read_password(args, environ, stdin)
        nexus = Nexus(runner, args.admin, args.admin_key, password, say)
        identity = args.monitoring_identity or (args.monitoring_key.with_suffix("") if args.monitoring_key else None)
        observer = (lambda sha: observe(runner, identity, sha, nexus.say)) if identity else None
        if args.revert:
            revert(nexus, args.purge)
        else:
            if password is None:
                raise SetupError(f"set ${PASSWORD_ENV} or pass --sudo-password-stdin")
            bundle = read_bundle(args.bundle_dir, args.signers)
            if args.upgrade:
                upgrade(nexus, bundle, observer)
            else:
                install(nexus, bundle, key_line(args.monitoring_key), observer)
    except (SetupError, OSError, ValueError, subprocess.TimeoutExpired) as error:
        text = str(error)
        say(f"ERROR: {password.scrub(text) if password else text}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
