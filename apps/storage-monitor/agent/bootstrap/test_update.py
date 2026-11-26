"""storage-viz-update behaviour (target-design I15): what a host activates, refuses and rolls back."""
from __future__ import annotations

import fcntl
import json
import os
import shutil
import tarfile
import io
import tempfile
import unittest
from pathlib import Path

from agent.bootstrap import fixtures, update

SHA_A = "a" * 40
SHA_B = "b" * 40
SHA_C = "c" * 40
UNIT_DIR = Path(__file__).resolve().parent / "systemd"


@unittest.skipUnless(shutil.which("ssh-keygen"), "ssh-keygen is required for signature checks")
class UpdaterTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="sv-update-test."))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.paths = update.Paths(self.tmp / "root")
        self.key = fixtures.SigningKey(self.tmp)
        self.key.write_allowed_signers(self.paths.signers)
        self.selftests = []
        self.selftest_ok = True

    def selftest(self, release, config):
        self.selftests.append(sorted(str(p.relative_to(release)) for p in release.rglob("*") if p.is_file()))
        return self.selftest_ok, "" if self.selftest_ok else "selftest: totals differ"

    def updater(self):
        return update.Updater(self.paths, selftest=self.selftest, clock=lambda: 1000)

    def stage(self, sha, data=None, signature=None, action="activate"):
        self.paths.incoming.mkdir(parents=True, exist_ok=True)
        if data is not None:
            (self.paths.incoming / f"{sha}.tar.gz").write_bytes(data)
            (self.paths.incoming / f"{sha}.tar.gz.sig").write_bytes(self.key.sign(data) if signature is None else signature)
        (self.paths.incoming / "activate").write_text(f"{action} {sha}\n")

    def activate(self, sha, **bundle_args):
        self.stage(sha, fixtures.bundle(sha, **bundle_args))
        return self.updater().run()

    def state(self):
        return json.loads(self.paths.state.read_text())

    def current(self):
        return os.readlink(self.paths.current) if self.paths.current.is_symlink() else None

    def test_signed_bundle_activates_and_records_state(self):
        self.assertEqual(self.activate(SHA_A), 0)
        self.assertEqual(self.current(), f"releases/{SHA_A}")
        self.assertEqual((self.paths.releases / SHA_A / "agent/storage_viz_agent/runner.py").read_text(), "# stand-in\n")
        self.assertEqual(os.stat(self.paths.releases / SHA_A / "bin/hstscan").st_mode & 0o7777, 0o755)
        state = self.state()
        self.assertEqual((state["active"], state["previous"], state["bad"]), (SHA_A, None, []))
        self.assertEqual(state["last_attempt"]["outcome"], "activated")
        self.assertFalse((self.paths.incoming / "activate").exists())
        self.assertFalse((self.paths.incoming / f"{SHA_A}.tar.gz").exists())
        self.assertEqual(len(self.selftests), 1)

    def test_tampered_bundle_is_refused(self):
        self.activate(SHA_A)
        data = bytearray(fixtures.bundle(SHA_B))
        signature = self.key.sign(bytes(data))
        data[len(data) // 2] ^= 0x01
        self.stage(SHA_B, bytes(data), signature)
        self.assertEqual(self.updater().run(), 1)
        self.assertEqual(self.state()["last_attempt"]["code"], "SIGNATURE")
        self.assertEqual(self.current(), f"releases/{SHA_A}")
        self.assertFalse((self.paths.releases / SHA_B).exists())

    def test_bad_signature_is_refused(self):
        stranger = fixtures.SigningKey(self.tmp, "stranger")
        data = fixtures.bundle(SHA_A)
        for signature in (stranger.sign(data), self.key.sign(data, namespace="other-namespace"), b"garbage"):
            with self.subTest(signature=signature[:20]):
                self.stage(SHA_A, data, signature)
                self.assertEqual(self.updater().run(), 1)
                self.assertEqual(self.state()["last_attempt"]["code"], "SIGNATURE")
                self.assertIsNone(self.current())
        self.assertEqual(self.selftests, [])

    def test_signed_bundle_with_wrong_digest_is_refused(self):
        def corrupt(manifest):
            manifest["files"]["agent/storage_viz_agent/runner.py"] = "0" * 64
        self.assertEqual(self.activate(SHA_A, edit_manifest=corrupt), 1)
        self.assertEqual(self.state()["last_attempt"]["code"], "DIGEST")
        self.assertIsNone(self.current())

    def test_manifest_naming_another_sha_is_refused(self):
        self.stage(SHA_A, fixtures.bundle(SHA_B))
        self.assertEqual(self.updater().run(), 1)
        self.assertEqual(self.state()["last_attempt"]["code"], "MANIFEST")

    def test_unsafe_archive_members_are_refused(self):
        def member(name, **attrs):
            def add(tar):
                info = tarfile.TarInfo(name)
                for key, value in attrs.items():
                    setattr(info, key, value)
                tar.addfile(info, io.BytesIO(b"x" * info.size) if info.isreg() else None)
            return add
        cases = {
            "absolute": member("/etc/cron.d/x", size=1, mode=0o644),
            "dotdot": member("bin/../../x", size=1, mode=0o644),
            "symlink": member("bin/link", type=tarfile.SYMTYPE, linkname="/etc/shadow"),
            "hardlink": member("bin/hard", type=tarfile.LNKTYPE, linkname="bin/hstscan"),
            "device": member("bin/dev", type=tarfile.CHRTYPE, devmajor=1, devminor=3),
            "setuid": member("bin/suid", size=1, mode=0o4755),
            "directory": member("bin/sub", type=tarfile.DIRTYPE, mode=0o755),
        }
        for index, (label, add) in enumerate(cases.items()):
            with self.subTest(label):
                sha = f"{index:x}" * 40
                self.stage(sha, fixtures.bundle(sha, extra_members=add))
                self.assertEqual(self.updater().run(), 1)
                self.assertEqual(self.state()["last_attempt"]["code"], "UNSAFE_ARCHIVE")
                self.assertIn(sha, [entry["sha"] for entry in self.state()["bad"]], "CI signed it broken")
                self.assertIsNone(self.current())
                self.assertEqual(list(self.paths.releases.iterdir()), [])

    def test_release_below_the_active_floor_is_refused(self):
        self.activate(SHA_A, release_seq=100, floor=90)
        self.assertEqual(self.activate(SHA_B, release_seq=89, floor=70), 1)
        self.assertEqual(self.state()["last_attempt"], {
            "action": "activate", "sha": SHA_B, "outcome": "refused", "code": "FLOOR", "finished_unix": 1000})
        self.assertEqual(self.current(), f"releases/{SHA_A}")
        self.assertEqual(self.activate(SHA_C, release_seq=90, floor=70), 0)

    def test_release_needing_a_newer_bootstrap_is_refused(self):
        self.assertEqual(self.activate(SHA_A, bootstrap_version=update.BOOTSTRAP_VERSION + 1), 1)
        self.assertEqual(self.state()["last_attempt"]["code"], "BOOTSTRAP_TOO_OLD")
        self.assertIsNone(self.current())

    def test_selftest_failure_leaves_previous_release_active_and_backs_off(self):
        self.activate(SHA_A)
        self.selftest_ok = False
        self.assertEqual(self.activate(SHA_B), 1)
        state = self.state()
        self.assertEqual(state["last_attempt"]["code"], "SELFTEST_FAILED")
        self.assertEqual(self.current(), f"releases/{SHA_A}")
        self.assertEqual(state["bad"], [], "a self-test can fail for a transient reason")
        self.assertEqual(state["selftest_backoff"][SHA_B], {"failures": 1, "retry_after": 1000 + 6 * 3600})
        self.assertFalse((self.paths.releases / SHA_B).exists())
        self.assertFalse((self.paths.releases / f"{SHA_B}.tmp").exists())

    def test_selftest_backoff_doubles_to_seven_days_then_a_pass_clears_it(self):
        self.selftest_ok = False
        now = [1000]
        expected = []
        for failure in range(1, 8):
            self.stage(SHA_B, fixtures.bundle(SHA_B))
            self.assertEqual(update.Updater(self.paths, selftest=self.selftest, clock=lambda: now[0]).run(), 1)
            entry = self.state()["selftest_backoff"][SHA_B]
            expected.append(entry["retry_after"] - now[0])
            self.stage(SHA_B)  # retry before the deadline: refused without a self-test
            runs = len(self.selftests)
            self.assertEqual(update.Updater(self.paths, selftest=self.selftest, clock=lambda: now[0]).run(), 1)
            self.assertEqual(self.state()["last_attempt"]["code"], "SELFTEST_BACKOFF")
            self.assertEqual(len(self.selftests), runs)
            now[0] = entry["retry_after"]
        self.assertEqual(expected, [6 * 3600 * 2 ** i for i in range(5)] + [7 * 24 * 3600] * 2)
        self.selftest_ok = True
        self.stage(SHA_B, fixtures.bundle(SHA_B))
        self.assertEqual(update.Updater(self.paths, selftest=self.selftest, clock=lambda: now[0]).run(), 0)
        self.assertEqual(self.current(), f"releases/{SHA_B}")
        self.assertEqual(self.state()["selftest_backoff"], {})

    def test_floor_never_ratchets_down_one_signed_release_at_a_time(self):
        """Review proof p1: each step at the active release's floor walked the host down."""
        self.assertEqual(self.activate("e" * 40, release_seq=100, floor=80), 0)
        self.assertEqual(self.activate("1" * 40, release_seq=50, floor=30), 1)
        self.assertEqual(self.state()["last_attempt"]["code"], "FLOOR")
        self.assertEqual(self.activate("2" * 40, release_seq=80, floor=60), 0, "at the floor is allowed")
        self.assertEqual(self.state()["floor_high_water"], 80)
        for sha, seq, floor in (("3" * 40, 60, 40), ("4" * 40, 40, 20), ("5" * 40, 50, 30)):
            with self.subTest(seq=seq):
                self.assertEqual(self.activate(sha, release_seq=seq, floor=floor), 1)
                self.assertEqual(self.state()["last_attempt"]["code"], "FLOOR")
                self.assertEqual(self.current(), f"releases/{'2' * 40}")
        self.stage("e" * 40)  # the installed seq-100 release is still above the high water
        self.assertEqual(self.updater().run(), 0)

    def test_high_water_survives_a_lost_state_file(self):
        self.activate(SHA_A, release_seq=100, floor=90)
        self.paths.state.unlink()
        self.assertEqual(self.activate(SHA_B, release_seq=89, floor=70), 1)
        self.assertEqual(self.state()["last_attempt"]["code"], "FLOOR")

    def test_rollback_to_previous_is_allowed_below_the_floor_the_failed_release_raised(self):
        self.activate(SHA_A, release_seq=100, floor=80)
        self.activate(SHA_B, release_seq=120, floor=110)  # a schema boundary
        self.stage(SHA_A, action="rollback")
        self.assertEqual(self.updater().run(), 0)
        self.assertEqual(self.current(), f"releases/{SHA_A}")
        self.assertEqual(self.state()["floor_high_water"], 110, "the floor does not drop with the rollback")
        self.assertEqual(self.activate(SHA_C, release_seq=105, floor=90), 1)
        self.assertEqual(self.state()["last_attempt"]["code"], "FLOOR")

    def test_request_written_during_a_run_is_processed_not_deleted(self):
        """Review proof p5: rollback-update arriving mid-run was unlinked unread."""
        self.activate(SHA_A)

        def selftest(release, config):
            (self.paths.incoming / "activate").write_text(f"rollback {SHA_A}\n")
            return True, ""

        self.stage(SHA_B, fixtures.bundle(SHA_B))
        self.assertEqual(update.Updater(self.paths, selftest=selftest, clock=lambda: 1000).run(), 0)
        state = self.state()
        self.assertEqual(state["last_attempt"]["outcome"], "rolled_back")
        self.assertEqual(self.current(), f"releases/{SHA_A}")
        self.assertEqual([entry["sha"] for entry in state["bad"]], [SHA_B])
        self.assertFalse((self.paths.incoming / "activate").exists())
        self.assertFalse((self.paths.incoming / "activate.processing").exists())

    def test_claimed_request_left_by_a_crash_is_resumed(self):
        self.stage(SHA_A, fixtures.bundle(SHA_A))
        os.replace(self.paths.incoming / "activate", self.paths.incoming / "activate.processing")
        self.assertEqual(self.updater().run(), 0)
        self.assertEqual(self.current(), f"releases/{SHA_A}")

    def test_os_error_during_install_is_recorded(self):
        files = fixtures.release_files()
        files["agent"] = (b"a file where a directory must go", 0o644)
        self.assertEqual(self.activate(SHA_A, files=files), 1)
        self.assertEqual(self.state()["last_attempt"]["code"], "IO_ERROR")
        self.assertIsNone(self.current())
        self.assertEqual(list(self.paths.releases.iterdir()), [], "the half-extracted release is removed")

    def test_garbage_signature_does_not_poison_the_sha(self):
        data = fixtures.bundle(SHA_A)
        self.stage(SHA_A, data, b"garbage")
        self.assertEqual(self.updater().run(), 1)
        self.assertEqual(self.state()["bad"], [])
        self.stage(SHA_A, data)
        self.assertEqual(self.updater().run(), 0)

    def test_busy_scan_lock_defers_without_touching_the_release(self):
        self.activate(SHA_A)
        self.stage(SHA_B, fixtures.bundle(SHA_B))
        held = os.open(self.paths.scan_lock, os.O_RDWR)
        fcntl.flock(held, fcntl.LOCK_EX)
        try:
            self.assertEqual(self.updater().run(), update.EXIT_BUSY)
        finally:
            os.close(held)
        self.assertEqual(self.state()["last_attempt"], {
            "action": "activate", "sha": SHA_B, "outcome": "busy", "code": "BUSY", "finished_unix": 1000})
        self.assertEqual(self.current(), f"releases/{SHA_A}")
        self.assertTrue((self.paths.incoming / "activate").exists(), "the request waits for the next start")
        self.assertEqual(self.updater().run(), 0)
        self.assertEqual(self.current(), f"releases/{SHA_B}")

    def test_rollback_restores_previous_and_marks_the_failed_release_bad(self):
        self.activate(SHA_A)
        self.activate(SHA_B)
        self.assertEqual((self.state()["active"], self.state()["previous"]), (SHA_B, SHA_A))
        self.stage(SHA_A, action="rollback")
        self.assertEqual(self.updater().run(), 0)
        state = self.state()
        self.assertEqual(self.current(), f"releases/{SHA_A}")
        self.assertEqual((state["active"], state["previous"], state["last_attempt"]["outcome"]), (SHA_A, None, "rolled_back"))
        self.assertEqual([entry["sha"] for entry in state["bad"]], [SHA_B])
        self.assertFalse((self.paths.releases / SHA_B).exists(), "a bad release is pruned")
        self.assertEqual(self.activate(SHA_B), 1)
        self.assertEqual(self.state()["last_attempt"]["code"], "BAD_RELEASE")

    def test_rollback_to_anything_but_previous_is_refused(self):
        self.activate(SHA_A)
        self.activate(SHA_B)
        self.stage(SHA_C, action="rollback")
        self.assertEqual(self.updater().run(), 1)
        self.assertEqual(self.state()["last_attempt"]["code"], "ROLLBACK_TARGET")
        self.assertEqual(self.current(), f"releases/{SHA_B}")

    def test_dashboard_rollback_reactivates_an_installed_release_without_marking_it_bad(self):
        self.activate(SHA_A)
        self.activate(SHA_B)
        self.stage(SHA_A)  # plain activate, no bundle staged
        self.assertEqual(self.updater().run(), 0)
        self.assertEqual(self.current(), f"releases/{SHA_A}")
        self.assertEqual(self.state()["bad"], [])
        self.assertEqual(len(self.selftests), 2, "an installed release was verified at install")

    def test_rerun_converges(self):
        self.activate(SHA_A)
        before = self.state()
        self.assertEqual(self.updater().run(), 0, "no request: nothing to do")
        self.assertEqual(self.state(), before)
        self.stage(SHA_A)
        self.assertEqual(self.updater().run(), 0)
        self.assertEqual(self.state()["last_attempt"]["outcome"], "already_active")
        self.assertEqual(self.current(), f"releases/{SHA_A}")
        self.assertEqual(len(self.selftests), 1)

    def test_crash_between_extract_and_rename_leaves_previous_active(self):
        self.activate(SHA_A)
        data = fixtures.bundle(SHA_B)
        self.stage(SHA_B, data)
        crashing = self.updater()
        crashing.selftest = lambda release, config: (_ for _ in ()).throw(KeyboardInterrupt())
        with self.assertRaises(KeyboardInterrupt):
            crashing.run()
        self.assertEqual(self.current(), f"releases/{SHA_A}")
        self.assertTrue((self.paths.releases / f"{SHA_B}.tmp").is_dir())
        self.assertEqual(self.updater().run(), 0)
        self.assertEqual(self.current(), f"releases/{SHA_B}")
        self.assertFalse((self.paths.releases / f"{SHA_B}.tmp").exists())

    def test_crash_after_rename_before_switch_resumes_from_the_installed_release(self):
        self.activate(SHA_A)
        self.stage(SHA_B, fixtures.bundle(SHA_B))
        crashing = self.updater()
        crashing._switch = lambda sha: (_ for _ in ()).throw(KeyboardInterrupt())
        with self.assertRaises(KeyboardInterrupt):
            crashing.run()
        self.assertEqual(self.current(), f"releases/{SHA_A}")
        self.assertEqual(self.updater().run(), 0)
        self.assertEqual(self.current(), f"releases/{SHA_B}")
        self.assertEqual(self.state()["previous"], SHA_A)

    def test_tampered_installed_release_is_not_reactivated(self):
        self.activate(SHA_A)
        self.activate(SHA_B)
        (self.paths.releases / SHA_A / "agent/storage_viz_agent/runner.py").write_text("import os; os.system('id')\n")
        self.stage(SHA_A)
        self.assertEqual(self.updater().run(), 1)
        self.assertEqual(self.state()["last_attempt"]["code"], "INSTALLED_CORRUPT")
        self.assertEqual(self.current(), f"releases/{SHA_B}")

    def test_keeps_at_most_three_releases(self):
        for index, sha in enumerate(("1" * 40, "2" * 40, "3" * 40, "4" * 40, "5" * 40)):
            self.activate(sha, release_seq=100 + index)
        kept = sorted(p.name for p in self.paths.releases.iterdir())
        self.assertEqual(len(kept), 3)
        self.assertIn("5" * 40, kept)
        self.assertIn("4" * 40, kept)

    def test_malformed_request_is_refused(self):
        self.paths.incoming.mkdir(parents=True)
        (self.paths.incoming / "activate").write_text("activate ../../etc\n")
        self.assertEqual(self.updater().run(), 1)
        self.assertEqual(self.state()["last_attempt"]["code"], "REQUEST_INVALID")


class SandboxContractTest(unittest.TestCase):
    def unit_properties(self, name):
        lines = (UNIT_DIR / name).read_text().splitlines()
        return [line for line in lines if "=" in line and not line.startswith("#")]

    def test_selftest_sandbox_matches_both_scan_units(self):
        for unit in ("storage-viz-scan.service", "storage-viz-scheduled-scan.service"):
            with self.subTest(unit):
                properties = self.unit_properties(unit)
                for prop in update.SCAN_SANDBOX:
                    self.assertIn(prop, properties)
                sandbox_keys = {p.split("=", 1)[0] for p in update.SCAN_SANDBOX}
                hardening = {"NoNewPrivileges", "ProtectSystem", "ProtectHome", "ReadWritePaths", "PrivateTmp",
                             "RestrictAddressFamilies", "CapabilityBoundingSet", "AmbientCapabilities",
                             "MemoryDenyWriteExecute", "SystemCallArchitectures", "User", "Group"}
                self.assertTrue(hardening <= sandbox_keys)

    def test_update_unit_is_confined_as_designed(self):
        properties = self.unit_properties("storage-viz-agent-update.service")
        for prop in ("ProtectSystem=strict", "PrivateNetwork=yes", "RestrictAddressFamilies=AF_UNIX",
                     "NoNewPrivileges=yes", "CapabilityBoundingSet=CAP_CHOWN CAP_FOWNER",
                     "ReadWritePaths=/opt/storage-viz /var/lib/storage-viz/update /run/storage-viz",
                     "SuccessExitStatus=75", "Group=storage-viz-collector", "RuntimeDirectory=storage-viz"):
            self.assertIn(prop, properties)

    def test_scan_units_use_cpu_weight_1_for_systemd_245(self):
        for unit in ("storage-viz-scan.service", "storage-viz-scheduled-scan.service"):
            properties = self.unit_properties(unit)
            self.assertIn("CPUWeight=1", properties)
            self.assertNotIn("CPUWeight=idle", properties)


if __name__ == "__main__":
    unittest.main()
