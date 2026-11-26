"""bootstrap_host.py against a temporary root that looks like an install-agent.sh host."""
from __future__ import annotations

import fcntl
import hashlib
import importlib.util
import json
import os
import shutil
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT))
from agent.bootstrap import fixtures  # noqa: E402

GATE_OPTS = 'restrict,command="/usr/local/lib/storage-viz/gate"'
SHA = "a" * 40
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


@unittest.skipUnless(shutil.which("ssh-keygen"), "ssh-keygen is required")
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
        self.key = fixtures.SigningKey(self.tmp)
        self.payload = self.make_payload()
        self.failing = set()
        self.host = bootstrap_host.Host(self.root, fake=True, fail=self.fail)

    def fail(self, argv):
        return any(" ".join(argv).startswith(prefix) or prefix in argv[0] for prefix in self.failing)

    def make_payload(self, bundle=None, signature=None):
        payload = self.tmp / "payload"
        shutil.copytree(APP_ROOT / "agent/bootstrap", payload / "bootstrap",
                        ignore=shutil.ignore_patterns("test_*", "__pycache__", "fixtures.py", "__init__.py"))
        self.key.write_allowed_signers(payload / "bootstrap/allowed_signers")
        bundle = bundle if bundle is not None else fixtures.bundle(SHA, release_seq=9, floor=0)
        (payload / "agent").mkdir()
        (payload / "agent/storage-viz-agent.tar.gz").write_bytes(bundle)
        (payload / "agent/storage-viz-agent.tar.gz.sig").write_bytes(signature or self.key.sign(bundle))
        (payload / "agent/storage-viz-agent.json").write_text(json.dumps(
            {"source_sha": SHA, "release_seq": 9, "sha256": hashlib.sha256(bundle).hexdigest()}))
        return payload

    def bootstrap(self):
        return bootstrap_host.Bootstrap(self.host, self.payload)

    def install(self, server_id=None, monitoring_key=None, allow_other_keys=False):
        self.bootstrap().install(server_id, monitoring_key, central_speaks_gate=True, allow_other_keys=allow_other_keys)

    def assert_untouched(self, before):
        self.assertEqual(snapshot(self.root, MANAGED_PREFIXES), before)
        self.assertFalse((self.root / "var/backups/storage-viz-bootstrap").exists())

    def commands(self, first):
        return [c for c in self.host.commands if c[0] == first]

    def test_install_gives_monitoring_only_the_gate(self):
        self.install()
        self.assertEqual((self.root / "home/monitoring/.ssh/authorized_keys").read_text(),
                         f'{GATE_OPTS},from="10.0.0.5" {CENTRAL_KEY}\n')
        for rel in ("home/monitoring", "home/monitoring/.ssh"):
            self.assertEqual(stat.S_IMODE(os.stat(self.root / rel).st_mode), 0o755, "root-owned, StrictModes-safe")

    def test_install_lays_down_the_bootstrap_tier(self):
        self.install()
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
        self.install()
        visudo = self.commands("visudo")
        self.assertEqual(len(visudo), 2, "the payload file in preflight, the staged copy at the swap")
        self.assertTrue(all(c[:2] == ["visudo", "-cf"] for c in visudo))
        verified = [Path(p).name for c in self.commands("systemd-analyze") for p in c[2:]]
        self.assertEqual(sorted(verified), sorted(["storage-viz-agent-update.service", "storage-viz-scan.service",
                                                   "storage-viz-scheduled-scan.service", "storage-viz-scan.timer"]))
        systemctl = [c[1:] for c in self.commands("systemctl")]
        self.assertLess(systemctl.index(["start", "storage-viz-agent-update.service"]),
                        systemctl.index(["enable", "--now", "storage-viz-scan.timer"]))
        incoming = self.root / "var/lib/storage-viz/update/incoming"
        self.assertEqual((incoming / "activate").read_text(), f"activate {'a' * 40}\n")
        self.assertEqual((incoming / f"{SHA}.tar.gz").read_bytes(), (self.payload / "agent/storage-viz-agent.tar.gz").read_bytes())

    def test_agent_config_keeps_server_id_and_prune_settings(self):
        self.install()
        config = json.loads((self.root / "etc/storage-viz/agent.yaml").read_text())
        self.assertEqual(config, {"server_id": "poseidon", "scanner_path": "/opt/storage-viz/current/bin/hstscan",
                                  "prune_home_mb": 7, "threads": 4})
        self.assertEqual((self.root / "etc/storage-viz/scanner.yaml").read_text(), LEGACY["etc/storage-viz/scanner.yaml"])

    def test_rerun_converges_and_keeps_the_first_backup(self):
        self.install()
        backup = snapshot(self.root, ["var/backups/storage-viz-bootstrap"])
        installed = snapshot(self.root, MANAGED_PREFIXES)
        self.install()
        self.assertEqual(snapshot(self.root, ["var/backups/storage-viz-bootstrap"]), backup)
        self.assertEqual(snapshot(self.root, MANAGED_PREFIXES), installed)

    def test_revert_restores_every_replaced_path(self):
        before = snapshot(self.root, MANAGED_PREFIXES)
        self.install()
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

    def test_rotated_central_key_left_active_refuses_the_install(self):
        """Review proof p2b: an old central key still held by central would be a shell as monitoring."""
        key_file = self.tmp / "central.pub"
        key_file.write_text("ssh-ed25519 AAAAnewkey new@central\n")
        before = snapshot(self.root, MANAGED_PREFIXES)
        with self.assertRaisesRegex(bootstrap_host.BootstrapError, "other active keys.*central@dashboard.*--allow-other-keys"):
            self.install(monitoring_key=key_file)
        self.assert_untouched(before)

    def test_allow_other_keys_keeps_them_and_warns(self):
        key_file = self.tmp / "central.pub"
        key_file.write_text("ssh-ed25519 AAAAnewkey new@central\n")
        with mock.patch("builtins.print") as printed:
            self.install(monitoring_key=key_file, allow_other_keys=True)
        self.assertEqual((self.root / "home/monitoring/.ssh/authorized_keys").read_text(),
                         f'from="10.0.0.5" {CENTRAL_KEY}\n{GATE_OPTS} ssh-ed25519 AAAAnewkey new@central\n')
        self.assertTrue(any("WARNING: --allow-other-keys" in str(call) for call in printed.call_args_list))

    def test_missing_central_key_stops_before_installing(self):
        (self.root / "home/monitoring/.ssh/authorized_keys").write_text("# ssh-ed25519 AAAAcommented old\n")
        before = snapshot(self.root, MANAGED_PREFIXES)
        with self.assertRaisesRegex(bootstrap_host.BootstrapError, "0 active keys"):
            self.install()
        self.assert_untouched(before)

    def test_running_scan_stops_the_bootstrap_before_any_change(self):
        lock = self.root / "run/storage-viz/scan.lock"
        lock.parent.mkdir(parents=True)
        lock.touch()
        before = snapshot(self.root, MANAGED_PREFIXES)
        fd = os.open(lock, os.O_RDWR)
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            with self.assertRaisesRegex(bootstrap_host.BootstrapError, "scan is running"):
                self.install()
        finally:
            os.close(fd)
        self.assertEqual(snapshot(self.root, MANAGED_PREFIXES), before)
        self.assertFalse((self.root / "var/backups/storage-viz-bootstrap").exists())

    def test_server_id_must_match_the_existing_config(self):
        before = snapshot(self.root, MANAGED_PREFIXES)
        with self.assertRaisesRegex(bootstrap_host.BootstrapError, "differs from scanner.yaml"):
            self.install("hinton")
        self.assert_untouched(before)

    def test_refuses_without_central_speaking_the_gate(self):
        """Review proof p4: today's central (SFTP, Python probe) gets only USAGE from the gate."""
        before = snapshot(self.root, MANAGED_PREFIXES)
        with self.assertRaisesRegex(bootstrap_host.BootstrapError, "central-speaks-gate"):
            self.bootstrap().install(None, None)
        self.assert_untouched(before)
        self.assertEqual(self.host.commands, [])

    def test_bad_signature_stops_before_any_change(self):
        stranger = fixtures.SigningKey(self.tmp, "stranger")
        bundle = fixtures.bundle(SHA, release_seq=9, floor=0)
        shutil.rmtree(self.payload)
        self.payload = self.make_payload(bundle, stranger.sign(bundle))
        before = snapshot(self.root, MANAGED_PREFIXES)
        with self.assertRaisesRegex(bootstrap_host.BootstrapError, "signature"):
            self.install()
        self.assert_untouched(before)

    def test_failed_selftest_stops_before_any_change(self):
        self.failing.add("storage-viz-agent")
        before = snapshot(self.root, MANAGED_PREFIXES)
        with self.assertRaisesRegex(bootstrap_host.BootstrapError, "self-test failed"):
            self.install()
        self.assert_untouched(before)
        self.assertFalse(any(c[0] == "systemctl" for c in self.host.commands), "the timer was never touched")

    def test_failure_after_a_disruptive_step_reverts_automatically(self):
        before = snapshot(self.root, MANAGED_PREFIXES)
        self.failing.add("systemctl enable --now storage-viz-scan.timer")
        with self.assertRaisesRegex(bootstrap_host.BootstrapError, "reverted"):
            self.install()
        self.assertEqual(snapshot(self.root, MANAGED_PREFIXES), before)
        self.assertFalse((self.root / "var/backups/storage-viz-bootstrap").exists())

    def test_failure_on_a_rerun_does_not_revert_a_working_bootstrap(self):
        self.install()
        installed = snapshot(self.root, MANAGED_PREFIXES)
        self.failing.add("systemctl enable --now storage-viz-scan.timer")
        with self.assertRaisesRegex(bootstrap_host.BootstrapError, "rerun stopped part way"):
            self.install()
        self.assertEqual(snapshot(self.root, MANAGED_PREFIXES), installed)
        self.assertTrue((self.root / "var/backups/storage-viz-bootstrap/manifest.json").exists())


class AuthorizedKeysTest(unittest.TestCase):
    """authorized_keys_text: central's line gets the forced command; nothing else changes."""

    def test_comments_are_not_revived_and_central_keeps_from(self):
        """Review proof p2."""
        original = ('from="10.0.0.5",command="/usr/bin/true" ssh-ed25519 AAAAC3central central-dashboard\n'
                    '# ssh-ed25519 AAAAC3revoked laptop-key-revoked-2025\n'
                    '#ssh-rsa AAAAB3oldrevoked old\n'
                    '\n')
        text, others = bootstrap_host.authorized_keys_text(original, None)
        self.assertEqual(text, f'{GATE_OPTS},from="10.0.0.5" ssh-ed25519 AAAAC3central central-dashboard\n'
                               '# ssh-ed25519 AAAAC3revoked laptop-key-revoked-2025\n'
                               '#ssh-rsa AAAAB3oldrevoked old\n'
                               '\n')
        self.assertEqual(others, [])

    def test_quoted_option_values_with_commas_and_spaces(self):
        original = 'from="10.0.0.5,10.0.0.6",environment="A=b c",no-pty ssh-ed25519 AAAAcentral c\n'
        text, _ = bootstrap_host.authorized_keys_text(original, None)
        self.assertEqual(text, f'{GATE_OPTS},from="10.0.0.5,10.0.0.6" ssh-ed25519 AAAAcentral c\n')

    def test_other_active_keys_are_refused_by_default(self):
        original = '# old laptop\n\nssh-ed25519 AAAAold old-central\nssh-ed25519 AAAAcentral central\n'
        with self.assertRaisesRegex(bootstrap_host.BootstrapError, "old-central"):
            bootstrap_host.authorized_keys_text(original, "ssh-ed25519 AAAAcentral central\n")

    def test_comments_and_blank_lines_alone_are_fine(self):
        original = '# rotated 2025\n\n# ssh-ed25519 AAAAold old\nssh-ed25519 AAAAcentral central\n'
        text, others = bootstrap_host.authorized_keys_text(original, "ssh-ed25519 AAAAcentral central\n")
        self.assertEqual(text, f'# rotated 2025\n\n# ssh-ed25519 AAAAold old\n{GATE_OPTS} ssh-ed25519 AAAAcentral central\n')
        self.assertEqual(others, [])

    def test_allowed_other_lines_are_kept_byte_for_byte(self):
        other = 'cert-authority,principals="ops" ssh-ed25519 AAAAca ops-ca  \n'
        original = other + 'ssh-ed25519 AAAAcentral central\n'
        text, others = bootstrap_host.authorized_keys_text(original, "ssh-ed25519 AAAAcentral central\n", allow_other_keys=True)
        self.assertEqual(text, other + f"{GATE_OPTS} ssh-ed25519 AAAAcentral central\n")
        self.assertEqual(others, ["ssh-ed25519 ops-ca"])

    def test_central_cert_authority_line_keeps_its_options(self):
        original = 'cert-authority,principals="central",from="10.0.0.0/24" ssh-ed25519 AAAAca central-ca\n'
        text, _ = bootstrap_host.authorized_keys_text(original, None)
        self.assertEqual(text, f'{GATE_OPTS},cert-authority,principals="central",from="10.0.0.0/24" ssh-ed25519 AAAAca central-ca\n')

    def test_two_active_keys_need_an_explicit_central_key(self):
        with self.assertRaisesRegex(bootstrap_host.BootstrapError, "2 active keys"):
            bootstrap_host.authorized_keys_text("ssh-ed25519 AAAAa a\nssh-rsa AAAAb b\n", None)

    def test_rerun_on_already_restricted_text_is_stable(self):
        once, _ = bootstrap_host.authorized_keys_text('from="10.0.0.5" ssh-ed25519 AAAAc c\n', None)
        twice, _ = bootstrap_host.authorized_keys_text(once, None)
        self.assertEqual(once, twice)


if __name__ == "__main__":
    unittest.main()
