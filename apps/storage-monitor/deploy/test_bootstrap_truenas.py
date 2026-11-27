"""bootstrap-truenas.py: every command it sends the TrueNAS host, and that the sudo password never leaks."""
from __future__ import annotations

import importlib.util
import io
import json
import shlex
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT))

from agent.bootstrap import fixtures  # noqa: E402

spec = importlib.util.spec_from_file_location("bootstrap_truenas", APP_ROOT / "deploy/bootstrap-truenas.py")
tool = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = tool  # dataclasses resolve annotations through the module
spec.loader.exec_module(tool)

PASSWORD = "correct horse battery staple"
SHA, NEWER = "a" * 40, "b" * 40
ROOT = tool.ROOT
STAGING = "/tmp/storage-viz-setup.Ab12Cd"
CENTRAL_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGNlbnRyYWwta2V5LWZvci10ZXN0cw storage-viz@central"
SUDO = ["sudo", "-S", "-k", "-p", "", "--"]


class FakeNexus:
    """The host as the tool sees it over ssh: the middleware, the files sudo touches, and the gate."""

    def __init__(self):
        self.calls = []  # (user, argv, stdin)
        self.datasets = {}
        self.groups = {}
        self.users = {}
        self.cron = {}
        self.files = {}
        self.current = None
        self.previous = None
        self.next_id = 40
        self.update_exit = 0
        self.middleware_writes_keys = lambda line: line  # what user.create puts in authorized_keys

    # -- the runner interface ------------------------------------------------------
    def run(self, user, key, command, stdin):
        argv = shlex.split(command)
        self.calls.append((user, argv, stdin))
        if user == "monitoring":
            return self.gate(argv)
        assert user == "truenas_admin" and key == Path("/keys/admin")
        if argv[:len(SUDO)] == SUDO:
            assert stdin == PASSWORD.encode() + b"\n", "a sudo command gets the password and nothing else"
            return self.sudo(argv[len(SUDO):])
        assert PASSWORD.encode() not in stdin
        if argv[:2] == ["midclt", "call"]:
            return self.midclt(argv[2], [json.loads(arg) for arg in argv[3:]])
        if argv[:2] == ["mktemp", "-d"]:
            return ok(STAGING + "\n")
        if argv[:2] == ["sh", "-c"]:
            self.files[argv[4]] = stdin
            return ok()
        if argv[:2] == ["rm", "-rf"]:
            return ok()
        raise AssertionError(f"unexpected command {argv}")

    def sudo(self, argv):
        if argv[:2] == ["install", "-d"]:
            return ok()
        if argv[0] == "install":
            self.files[argv[-1]] = self.files[argv[-2]]
            return ok()
        if argv[0] == "readlink":
            return ok(f"releases/{self.current}\n") if self.current else tool.Reply(1, b"", b"")
        if argv[:2] == [tool.PYTHON, "-I"]:
            if self.update_exit == 0:
                action, sha = self.files[f"{ROOT}/data/update/incoming/activate"].decode().split()
                self.previous, self.current = self.current, sha
            return tool.Reply(self.update_exit, b"", b"")
        if argv[0] == "cat":
            if argv[1] == f"{ROOT}/data/update/state.json":
                return ok(json.dumps({"active": self.current, "previous": self.previous, "last_attempt": {"outcome": "x"}}))
            user = self.users.get("monitoring")
            if argv[1] == f"{ROOT}/home/.ssh/authorized_keys" and user:
                return ok(self.middleware_writes_keys(user["sshpubkey"]) + "\n")
            return tool.Reply(1, b"", b"cat: No such file or directory")
        raise AssertionError(f"unexpected sudo command {argv}")

    def midclt(self, method, args):
        tables = {"pool.dataset": (self.datasets, "id"), "group": (self.groups, "group"), "user": (self.users, "username"),
                  "cronjob": (self.cron, "description")}
        name, verb = method.rsplit(".", 1)
        table, key = tables[name]
        if verb == "query":
            (field, op, value), = args[0]
            return ok(json.dumps([row for row in table.values() if row.get(field) == value]))
        if verb == "create":
            row = dict(args[0], id=self.next_id)
            self.next_id += 1
            if name == "pool.dataset":
                row.update(id=row["name"], mountpoint="/mnt/" + row["name"], exec={"value": "ON"})
            if name == "group":
                row["group"] = row.pop("name")
            if name == "user":
                row["group"] = {"id": row["group"]}
            table[row[key]] = row
            return ok(json.dumps(row["id"]))
        row = next(row for row in table.values() if row["id"] == args[0])
        if verb == "update":
            row.update(args[1])
            if "group" in args[1]:
                row["group"] = {"id": args[1]["group"]}
            return ok("null")
        if verb == "delete":
            del table[row[key]]
            return ok("true")
        raise AssertionError(method)

    def gate(self, argv):
        assert argv == ["observe"]
        body = json.dumps({"scheduler": "cron", "scan": {"state": "idle"},
                           "agent": {"release_sha": self.current, "health": "ok"}}).encode()
        return tool.Reply(0, b"storage-viz-gate/1 ok %d\n" % len(body) + body, b"")

    # -- what the tests read -------------------------------------------------------
    def commands(self):
        return [argv for _user, argv, _stdin in self.calls]

    def methods(self):
        return [argv[2] for argv in self.commands() if argv[:2] == ["midclt", "call"]]


def ok(text=""):
    return tool.Reply(0, text.encode(), b"")


@unittest.skipUnless(shutil.which("ssh-keygen"), "ssh-keygen is required for signature checks")
class BootstrapTrueNasTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="sv-truenas-test."))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.key = fixtures.SigningKey(self.tmp)
        self.signers = self.key.write_allowed_signers(self.tmp / "allowed_signers")
        self.bundle_dir = self.write_bundle(self.tmp / "agent", SHA)
        self.public = self.tmp / "central.pub"
        self.public.write_text(CENTRAL_KEY + "\n")
        self.host = FakeNexus()
        self.output = []

    def write_bundle(self, directory, sha, signature=None):
        directory.mkdir(parents=True, exist_ok=True)
        data = fixtures.bundle(sha)
        (directory / tool.BUNDLE).write_bytes(data)
        (directory / tool.SIGNATURE).write_bytes(self.key.sign(data) if signature is None else signature)
        (directory / tool.METADATA).write_text(json.dumps({"source_sha": sha, "release_seq": 100}))
        return directory

    def main(self, *extra, environ=None, stdin=""):
        argv = ["--host", "192.168.0.200", "--admin-key", "/keys/admin", "--bundle-dir", str(self.bundle_dir),
                "--signers", str(self.signers), *extra]
        if "--revert" not in extra and "--upgrade" not in extra:
            argv += ["--monitoring-key", str(self.public)]
        environ = {tool.PASSWORD_ENV: PASSWORD} if environ is None else environ
        return tool.main(argv, runner=self.host, environ=environ, stdin=io.StringIO(stdin), say=self.output.append)

    def assert_no_leak(self):
        for user, argv, stdin in self.host.calls:
            self.assertNotIn(PASSWORD, " ".join(argv))
        self.assertNotIn(PASSWORD, "\n".join(self.output))

    def test_install_sends_exactly_these_commands(self):
        self.assertEqual(self.main(), 0, self.output)
        line = f'restrict,command="{tool.GATE_COMMAND}" {CENTRAL_KEY}'
        incoming = f"{ROOT}/data/update/incoming"
        upload = ["sh", "-c", 'umask 077 && cat > "$1"', "sh"]
        expected = [
            ["midclt", "call", "pool.dataset.query", '[["id", "=", "tank/storage-viz"]]'],
            ["midclt", "call", "pool.dataset.create",
             '{"aclmode": "DISCARD", "acltype": "POSIX", "exec": "ON", "name": "tank/storage-viz", "type": "FILESYSTEM"}'],
            ["midclt", "call", "pool.dataset.query", '[["id", "=", "tank/storage-viz"]]'],
            ["midclt", "call", "group.query", '[["group", "=", "storage-viz-collector"]]'],
            ["midclt", "call", "group.create", '{"name": "storage-viz-collector", "smb": false}'],
            ["midclt", "call", "group.query", '[["group", "=", "storage-viz-collector"]]'],
            SUDO + ["install", "-d", "-o", "root", "-g", "root", "-m", "0755", ROOT, f"{ROOT}/opt", f"{ROOT}/opt/releases",
                    f"{ROOT}/bootstrap", f"{ROOT}/home"],
            SUDO + ["install", "-d", "-o", "root", "-g", "storage-viz-collector", "-m", "2750", f"{ROOT}/data", f"{ROOT}/run",
                    f"{ROOT}/data/captures", f"{ROOT}/data/update", incoming],
            SUDO + ["install", "-d", "-o", "root", "-g", "storage-viz-collector", "-m", "2770", f"{ROOT}/data/requests"],
            ["mktemp", "-d", "/tmp/storage-viz-setup.XXXXXX"],
            upload + [f"{STAGING}/gate.py"],
            SUDO + ["install", "-o", "root", "-g", "root", "-m", "0644", f"{STAGING}/gate.py", f"{ROOT}/bootstrap/gate.py"],
            upload + [f"{STAGING}/update.py"],
            SUDO + ["install", "-o", "root", "-g", "root", "-m", "0644", f"{STAGING}/update.py", f"{ROOT}/bootstrap/update.py"],
            upload + [f"{STAGING}/allowed_signers"],
            SUDO + ["install", "-o", "root", "-g", "root", "-m", "0644", f"{STAGING}/allowed_signers",
                    f"{ROOT}/bootstrap/allowed_signers"],
            upload + [f"{STAGING}/agent.yaml"],
            SUDO + ["install", "-o", "root", "-g", "storage-viz-collector", "-m", "0640", f"{STAGING}/agent.yaml", f"{ROOT}/agent.yaml"],
            SUDO + ["readlink", f"{ROOT}/opt/current"],
            upload + [f"{STAGING}/{SHA}.tar.gz"],
            SUDO + ["install", "-o", "root", "-g", "storage-viz-collector", "-m", "0640", f"{STAGING}/{SHA}.tar.gz",
                    f"{incoming}/{SHA}.tar.gz"],
            upload + [f"{STAGING}/{SHA}.tar.gz.sig"],
            SUDO + ["install", "-o", "root", "-g", "storage-viz-collector", "-m", "0640", f"{STAGING}/{SHA}.tar.gz.sig",
                    f"{incoming}/{SHA}.tar.gz.sig"],
            upload + [f"{STAGING}/activate"],
            SUDO + ["install", "-o", "root", "-g", "storage-viz-collector", "-m", "0640", f"{STAGING}/activate", f"{incoming}/activate"],
            SUDO + ["/usr/bin/python3", "-I", f"{ROOT}/bootstrap/update.py", "--root", ROOT],
            SUDO + ["cat", f"{ROOT}/data/update/state.json"],
            ["rm", "-rf", "--", STAGING],
            ["midclt", "call", "user.query", '[["username", "=", "monitoring"]]'],
            ["midclt", "call", "user.create", json.dumps({
                "full_name": "storage-viz gate", "group": 41, "group_create": False, "home": f"{ROOT}/home",
                "home_create": False, "password_disabled": True, "shell": "/usr/bin/sh", "smb": False,
                "sshpubkey": line, "username": "monitoring"}, sort_keys=True)],
            SUDO + ["cat", f"{ROOT}/home/.ssh/authorized_keys"],
            ["midclt", "call", "cronjob.query", '[["description", "=", "storage-viz agent"]]'],
            ["midclt", "call", "cronjob.create", json.dumps({
                "command": f"{ROOT}/opt/current/bin/storage-viz-agent run --config {ROOT}/agent.yaml --scheduled",
                "description": "storage-viz agent", "enabled": True, "stderr": False, "stdout": True,
                "schedule": {"dom": "*", "dow": "*", "hour": "*", "minute": "*/5", "month": "*"}, "user": "root"}, sort_keys=True)],
            ["observe"],
        ]
        self.assertEqual(self.host.commands(), expected)
        self.assertEqual(self.host.calls[-1][0], "monitoring")
        self.assert_no_leak()

    def test_uploaded_files_are_the_repository_files_and_the_agent_config(self):
        self.assertEqual(self.main(), 0, self.output)
        files = self.host.files
        for name in ("gate.py", "update.py", "allowed_signers"):
            self.assertEqual(files[f"{ROOT}/bootstrap/{name}"], (APP_ROOT / "agent/bootstrap" / name).read_bytes())
        config = json.loads(files[f"{ROOT}/agent.yaml"])
        self.assertEqual({k: config[k] for k in ("server_id", "data_dir", "run_dir", "host_profile", "scanner_path")}, {
            "server_id": "nexus", "data_dir": f"{ROOT}/data", "run_dir": f"{ROOT}/run", "host_profile": "truenas",
            "scanner_path": f"{ROOT}/opt/current/bin/hstscan"})
        self.assertEqual(config["volume_aliases"], {"/mnt/tank/datasets": "nexus-datasets", "/mnt/tank/workspace": "nexus-workspace"})
        self.assertEqual(config["volume_media"], {"/mnt/tank/datasets": "ssd", "/mnt/tank/workspace": "ssd"})
        from agent.storage_viz_agent import config as agent_config
        path = self.tmp / "agent.yaml"
        path.write_bytes(files[f"{ROOT}/agent.yaml"])
        self.assertEqual(agent_config.load_config(path).host_profile, "truenas", "the agent accepts the config it is given")
        self.assertEqual(files[f"{ROOT}/data/update/incoming/{SHA}.tar.gz"], (self.bundle_dir / tool.BUNDLE).read_bytes())
        self.assertEqual(files[f"{ROOT}/data/update/incoming/activate"], f"activate {SHA}\n".encode())

    def test_gate_command_and_forced_key_line(self):
        self.assertEqual(tool.GATE_COMMAND, f"/usr/bin/python3 -I {ROOT}/bootstrap/gate.py --root {ROOT} --no-units")
        self.assertEqual(tool.key_line(self.public), f'restrict,command="{tool.GATE_COMMAND}" {CENTRAL_KEY}')
        for text in ("", CENTRAL_KEY + "\n" + CENTRAL_KEY, 'from="10.0.0.1" ' + CENTRAL_KEY, "not a key"):
            self.public.write_text(text)
            with self.assertRaises(tool.SetupError):
                tool.key_line(self.public)

    def test_a_second_install_changes_nothing(self):
        self.assertEqual(self.main(), 0, self.output)
        first = len(self.host.calls)
        self.assertEqual(self.main(), 0, self.output)
        again = self.host.commands()[first:]
        writes = [argv[2] for argv in again if argv[:2] == ["midclt", "call"] and not argv[2].endswith(".query")]
        self.assertEqual(writes, [], "a rerun creates and updates nothing in the middleware")
        self.assertNotIn(SUDO + ["/usr/bin/python3", "-I", f"{ROOT}/bootstrap/update.py", "--root", ROOT], again)
        self.assertIn(SUDO + ["cat", f"{ROOT}/home/.ssh/authorized_keys"], again, "the key line is checked every run")
        self.assertIn("[=] agent release %s is already current" % SHA, self.output)
        self.assert_no_leak()

    def test_a_changed_user_gets_only_the_changed_fields(self):
        self.main()
        self.host.users["monitoring"]["shell"] = "/usr/sbin/nologin"
        self.assertEqual(self.main(), 0, self.output)
        update = [argv for argv in self.host.commands() if argv[2:3] == ["user.update"]]
        self.assertEqual(update, [["midclt", "call", "user.update", "42", '{"shell": "/usr/bin/sh"}']])

    def test_middleware_that_rewrites_the_key_line_fails_clearly(self):
        self.host.middleware_writes_keys = lambda line: line.split(" ", 1)[1].split('" ', 1)[1]  # options dropped
        self.assertEqual(self.main(), 1)
        self.assertIn("does not hold exactly the forced-command line", self.output[-1])
        self.assertNotIn("cronjob.create", self.host.methods(), "no cron job before the gate path is proven")

    def test_upgrade_installs_the_newer_bundle_and_keeps_the_previous_release(self):
        self.main()
        self.write_bundle(self.bundle_dir, NEWER)
        before = len(self.host.calls)
        self.assertEqual(self.main("--upgrade"), 0, self.output)
        commands = self.host.commands()[before:]
        self.assertEqual(commands[0], ["midclt", "call", "pool.dataset.query", '[["id", "=", "tank/storage-viz"]]'])
        self.assertIn(SUDO + ["/usr/bin/python3", "-I", f"{ROOT}/bootstrap/update.py", "--root", ROOT], commands)
        self.assertFalse([c for c in commands if c[2:3] in (["user.update"], ["cronjob.update"], ["user.create"])])
        self.assertEqual((self.host.current, self.host.previous), (NEWER, SHA))
        self.assertTrue(any(f"previous {SHA}" in line for line in self.output))
        self.assertNotIn(["observe"], commands, "no monitoring key given: no observe")
        self.assert_no_leak()

    def test_upgrade_while_a_scan_runs_says_to_rerun(self):
        self.main()
        self.write_bundle(self.bundle_dir, NEWER)
        self.host.update_exit = 75
        self.assertEqual(self.main("--upgrade"), 1)
        self.assertIn("a scan holds the lock", self.output[-1])
        self.assertEqual(self.host.current, SHA)

    def test_a_bundle_that_fails_verification_never_reaches_the_host(self):
        self.write_bundle(self.bundle_dir, SHA, signature=fixtures.SigningKey(self.tmp, "other").sign(b"x"))
        self.assertEqual(self.main(), 1)
        self.assertIn("signature does not verify", self.output[-1])
        self.assertEqual(self.host.calls, [])

    def test_password_comes_from_the_environment_or_stdin_only(self):
        self.assertEqual(self.main(environ={}), 1)
        self.assertEqual(self.host.calls, [])
        self.assertIn(tool.PASSWORD_ENV, self.output[-1])
        self.assertEqual(self.main("--sudo-password-stdin", environ={}, stdin=PASSWORD + "\n"), 0, self.output)
        self.assert_no_leak()
        self.assertNotIn(PASSWORD, repr(tool.Secret(PASSWORD)) + str(tool.Secret(PASSWORD)))

    def test_a_failing_sudo_command_never_echoes_the_password(self):
        real = self.host.sudo

        def failing(argv):
            if argv[:2] == ["install", "-d"]:
                return tool.Reply(1, b"", f"sudo: echoed {PASSWORD} by a broken host".encode())
            return real(argv)
        self.host.sudo = failing
        self.assertEqual(self.main(), 1)
        self.assertIn("***", self.output[-1])
        self.assert_no_leak()

    def test_a_dataset_mounted_elsewhere_or_noexec_is_refused(self):
        self.host.datasets["tank/storage-viz"] = {"id": "tank/storage-viz", "mountpoint": "/mnt/elsewhere", "exec": {"value": "ON"}}
        self.assertEqual(self.main(), 1)
        self.assertIn("not mounted at", self.output[-1])
        self.host.datasets["tank/storage-viz"].update(mountpoint=ROOT, exec={"value": "OFF"})
        self.assertEqual(self.main(), 1)
        self.assertIn("exec=off", self.output[-1])

    def test_revert_removes_the_cron_job_and_the_user_and_purge_the_rest(self):
        self.main()
        before = len(self.host.calls)
        self.assertEqual(self.main("--revert", environ={}), 0, self.output)
        self.assertEqual(self.host.methods()[-4:], ["cronjob.query", "cronjob.delete", "user.query", "user.delete"])
        self.assertEqual((self.host.cron, self.host.users), ({}, {}))
        self.assertTrue(self.host.datasets and self.host.groups, "the dataset and its data stay")
        self.assertFalse([argv for _u, argv, _s in self.host.calls[before:] if argv[:1] == ["sudo"]], "revert needs no sudo")
        self.assertEqual(self.main("--revert", "--purge", environ={}), 0, self.output)
        self.assertEqual(self.host.methods()[-4:], ["group.query", "group.delete", "pool.dataset.query", "pool.dataset.delete"])
        self.assertEqual(self.host.commands()[-1], ["midclt", "call", "pool.dataset.delete", '"tank/storage-viz"', '{"recursive": true}'])
        self.assertEqual((self.host.datasets, self.host.groups), ({}, {}))
        self.assertEqual(self.main("--revert", "--purge", environ={}), 0, "revert converges")

    def test_observe_must_show_the_cron_gate_and_the_installed_release(self):
        real = self.host.gate
        self.host.gate = lambda argv: tool.Reply(0, b"storage-viz-gate/1 err USAGE\n", b"")
        self.assertEqual(self.main(), 1)
        self.assertIn("observe through the gate failed", self.output[-1])
        self.host.gate = real
        self.assertEqual(self.main(), 0, self.output)
        self.assertEqual(self.host.calls[-1][:2], ("monitoring", ["observe"]))


if __name__ == "__main__":
    unittest.main()
