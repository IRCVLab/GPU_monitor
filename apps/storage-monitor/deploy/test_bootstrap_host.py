"""bootstrap_host.py against a temporary root that looks like an install-agent.sh host."""
from __future__ import annotations

import fcntl
import importlib.util
import json
import os
import shutil
import stat
import tempfile
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("bootstrap_host", APP_ROOT / "deploy/bootstrap_host.py")
bootstrap_host = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bootstrap_host)

CENTRAL_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOmFakeCentralKeyForTestsOnly000000000000000 central@dashboard"
LEGACY = {
    "etc/storage-viz/scanner.yaml": '{"server_id": "poseidon", "scanner_path": "/opt/storage-viz/scanner/hstscan", "prune_home_mb": 7, "threads": 4}',
    "etc/sudoers.d/storage-viz-monitoring": "monitoring ALL=(root) NOPASSWD: /usr/bin/systemctl start storage-viz-scan.service\n",
    "etc/systemd/system/storage-viz-scan.service": "[Service]\nExecStart=/usr/bin/python3 -m agent.scan_runner\n",
    "etc/systemd/system/storage-viz-scheduled-scan.service": "[Service]\nExecStart=/usr/bin/python3 -m agent.scan_runner --scheduled\n",
    "etc/systemd/system/storage-viz-scan.timer": "[Timer]\nOnCalendar=hourly\n",
    "opt/storage-viz/agent/scan_runner.py": "# legacy agent\n",
    "opt/storage-viz/scanner/hstscan": "legacy binary",
    "opt/storage-viz/storage_fstypes.py": "# legacy\n",
    "home/monitoring/.ssh/authorized_keys": f'from="10.0.0.5" {CENTRAL_KEY}\n',
    "var/lib/storage-viz/scan-status.json": '{"generation": "poseidon-1-v1.json"}',
}


def snapshot(root: Path, prefixes):
    """{path: (mode, content or link target)} for every path under the prefixes."""
    result = {}
    for prefix in prefixes:
        base = root / prefix
        if not os.path.lexists(base):
            continue
        for path in [base, *sorted(base.rglob("*"))] if base.is_dir() else [base]:
            st = os.lstat(path)
            if stat.S_ISLNK(st.st_mode):
                content = os.readlink(path)
            elif stat.S_ISREG(st.st_mode):
                content = path.read_bytes()
            else:
                content = None
            result[str(path.relative_to(root))] = (stat.S_IMODE(st.st_mode), content)
    return result


MANAGED_PREFIXES = ("etc/sudoers.d", "etc/systemd/system", "etc/storage-viz", "usr/local/lib/storage-viz",
                    "opt/storage-viz", "home/monitoring/.ssh", "var/lib/storage-viz")


class BootstrapHostTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="sv-bootstrap."))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = self.tmp / "root"
        for rel, text in LEGACY.items():
            (self.root / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.root / rel).write_text(text)
        os.chmod(self.root / "etc/sudoers.d/storage-viz-monitoring", 0o440)
        os.chmod(self.root / "opt/storage-viz/scanner/hstscan", 0o755)
        self.payload = self.make_payload()
        self.host = bootstrap_host.Host(self.root, fake=True)

    def make_payload(self):
        payload = self.tmp / "payload"
        shutil.copytree(APP_ROOT / "agent/bootstrap", payload / "bootstrap",
                        ignore=shutil.ignore_patterns("test_*", "__pycache__", "fixtures.py", "__init__.py"))
        (payload / "agent").mkdir()
        (payload / "agent/storage-viz-agent.tar.gz").write_bytes(b"bundle")
        (payload / "agent/storage-viz-agent.tar.gz.sig").write_bytes(b"sig")
        (payload / "agent/storage-viz-agent.json").write_text(json.dumps({"source_sha": "a" * 40, "release_seq": 9}))
        return payload

    def bootstrap(self):
        return bootstrap_host.Bootstrap(self.host, self.payload)

    def commands(self, first):
        return [c for c in self.host.commands if c[0] == first]

    def test_install_gives_monitoring_only_the_gate(self):
        self.bootstrap().install(None, None)
        self.assertEqual((self.root / "home/monitoring/.ssh/authorized_keys").read_text(),
                         f'restrict,command="/usr/local/lib/storage-viz/gate" {CENTRAL_KEY}\n')

    def test_install_lays_down_the_bootstrap_tier(self):
        self.bootstrap().install(None, None)
        lib = self.root / "usr/local/lib/storage-viz"
        self.assertEqual((lib / "gate").read_bytes(), (APP_ROOT / "agent/bootstrap/gate.py").read_bytes())
        self.assertEqual((lib / "storage-viz-update").read_bytes(), (APP_ROOT / "agent/bootstrap/update.py").read_bytes())
        self.assertEqual(stat.S_IMODE(os.stat(lib / "gate").st_mode), 0o755)
        sudoers = self.root / "etc/sudoers.d"
        self.assertEqual(sorted(p.name for p in sudoers.iterdir()), ["storage-viz"])
        self.assertEqual(stat.S_IMODE(os.stat(sudoers / "storage-viz").st_mode), 0o440)
        for unit in ("storage-viz-scan.service", "storage-viz-scheduled-scan.service", "storage-viz-scan.timer",
                     "storage-viz-agent-update.service"):
            self.assertEqual((self.root / "etc/systemd/system" / unit).read_bytes(),
                             (APP_ROOT / "agent/bootstrap/systemd" / unit).read_bytes())
        modes = {rel: stat.S_IMODE(os.stat(self.root / rel).st_mode) for rel in (
            "var/lib/storage-viz/requests", "var/lib/storage-viz/update/incoming", "var/lib/storage-viz/update")}
        self.assertEqual(modes, {"var/lib/storage-viz/requests": 0o2770, "var/lib/storage-viz/update/incoming": 0o2770,
                                 "var/lib/storage-viz/update": 0o750})
        self.assertEqual(sorted(p.name for p in (self.root / "opt/storage-viz").iterdir()), ["releases"])
        self.assertEqual((self.root / "var/lib/storage-viz/scan-status.json").read_text(), LEGACY["var/lib/storage-viz/scan-status.json"])

    def test_install_runs_the_mandatory_checks_and_activates_through_the_updater(self):
        self.bootstrap().install(None, None)
        visudo = self.commands("visudo")
        self.assertEqual(len(visudo), 1)
        self.assertEqual(visudo[0][:2], ["visudo", "-cf"])
        verified = [Path(p).name for c in self.commands("systemd-analyze") for p in c[2:]]
        self.assertEqual(sorted(verified), sorted(["storage-viz-agent-update.service", "storage-viz-scan.service",
                                                   "storage-viz-scheduled-scan.service", "storage-viz-scan.timer"]))
        systemctl = [c[1:] for c in self.commands("systemctl")]
        self.assertLess(systemctl.index(["start", "storage-viz-agent-update.service"]),
                        systemctl.index(["enable", "--now", "storage-viz-scan.timer"]))
        incoming = self.root / "var/lib/storage-viz/update/incoming"
        self.assertEqual((incoming / "activate").read_text(), f"activate {'a' * 40}\n")
        self.assertEqual((incoming / f"{'a' * 40}.tar.gz").read_bytes(), b"bundle")

    def test_agent_config_keeps_server_id_and_prune_settings(self):
        self.bootstrap().install(None, None)
        config = json.loads((self.root / "etc/storage-viz/agent.yaml").read_text())
        self.assertEqual(config, {"server_id": "poseidon", "scanner_path": "/opt/storage-viz/current/bin/hstscan",
                                  "prune_home_mb": 7, "threads": 4})
        self.assertEqual((self.root / "etc/storage-viz/scanner.yaml").read_text(), LEGACY["etc/storage-viz/scanner.yaml"])

    def test_rerun_converges_and_keeps_the_first_backup(self):
        self.bootstrap().install(None, None)
        backup = snapshot(self.root, ["var/backups/storage-viz-bootstrap"])
        installed = snapshot(self.root, MANAGED_PREFIXES)
        self.bootstrap().install(None, None)
        self.assertEqual(snapshot(self.root, ["var/backups/storage-viz-bootstrap"]), backup)
        self.assertEqual(snapshot(self.root, MANAGED_PREFIXES), installed)

    def test_revert_restores_every_replaced_path(self):
        before = snapshot(self.root, MANAGED_PREFIXES)
        self.bootstrap().install(None, None)
        self.assertNotEqual(snapshot(self.root, MANAGED_PREFIXES), before)
        self.bootstrap().revert()
        self.assertEqual(snapshot(self.root, MANAGED_PREFIXES), before)
        self.assertFalse((self.root / "var/backups/storage-viz-bootstrap").exists())
        self.assertEqual(len(list((self.root / "var/backups").glob("storage-viz-bootstrap.reverted-*"))), 1)

    def test_revert_without_a_backup_changes_nothing(self):
        before = snapshot(self.root, MANAGED_PREFIXES)
        with self.assertRaisesRegex(bootstrap_host.BootstrapError, "no complete backup"):
            self.bootstrap().revert()
        self.assertEqual(snapshot(self.root, MANAGED_PREFIXES), before)

    def test_explicit_monitoring_key_replaces_the_old_keys(self):
        key_file = self.tmp / "central.pub"
        key_file.write_text("ssh-ed25519 AAAAnewkey new@central\n")
        self.bootstrap().install(None, key_file)
        self.assertEqual((self.root / "home/monitoring/.ssh/authorized_keys").read_text(),
                         'restrict,command="/usr/local/lib/storage-viz/gate" ssh-ed25519 AAAAnewkey new@central\n')

    def test_missing_central_key_stops_before_installing(self):
        (self.root / "home/monitoring/.ssh/authorized_keys").write_text("# no keys\n")
        with self.assertRaisesRegex(bootstrap_host.BootstrapError, "no central key"):
            self.bootstrap().install(None, None)
        self.assertFalse((self.root / "usr/local/lib/storage-viz/gate").exists())

    def test_running_scan_stops_the_bootstrap_before_any_change(self):
        lock = self.root / "run/storage-viz/scan.lock"
        lock.parent.mkdir(parents=True)
        lock.touch()
        before = snapshot(self.root, MANAGED_PREFIXES)
        fd = os.open(lock, os.O_RDWR)
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            with self.assertRaisesRegex(bootstrap_host.BootstrapError, "scan is running"):
                self.bootstrap().install(None, None)
        finally:
            os.close(fd)
        self.assertEqual(snapshot(self.root, MANAGED_PREFIXES), before)
        self.assertFalse((self.root / "var/backups/storage-viz-bootstrap").exists())

    def test_server_id_must_match_the_existing_config(self):
        with self.assertRaisesRegex(bootstrap_host.BootstrapError, "differs from scanner.yaml"):
            self.bootstrap().install("hinton", None)


if __name__ == "__main__":
    unittest.main()
