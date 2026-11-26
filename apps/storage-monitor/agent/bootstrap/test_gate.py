"""The monitoring key's forced command (target-design I12, I16, S7)."""
from __future__ import annotations

import fcntl
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from agent.bootstrap import gate

REQUEST = "0123456789abcdef0123456789abcdef"
SHA = "d" * 40
VOLUME = "v-0123456789abcdef"
CAPTURE = "e" * 64


class FakeHost(gate.Host):
    def __init__(self, start_ok=True):
        self.started = []
        self.start_ok = start_ok

    def start_unit(self, unit):
        self.started.append(unit)
        return self.start_ok

    def show(self, units, properties):
        return "\n\n".join(f"Id={unit}\nActiveState=inactive\nInvocationID=" for unit in units)

    def agent_health(self, paths):
        return "ok"


class GateTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="sv-gate-test."))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.paths = gate.Paths(self.root)
        for directory in (self.paths.data, self.paths.run, self.paths.requests, self.paths.incoming, self.paths.captures):
            directory.mkdir(parents=True, exist_ok=True)
        self.paths.scan_lock.touch()
        self.host = FakeHost()

    def serve(self, command, stdin=b""):
        out = io.BytesIO()
        code = gate.serve(command, self.paths, self.host, io.BytesIO(stdin), out)
        header, _, body = out.getvalue().partition(b"\n")
        return code, header.decode(), body

    def tree(self):
        return sorted(
            (str(p.relative_to(self.root)), p.read_bytes() if p.is_file() else None)
            for p in self.root.rglob("*")
        )

    def hold_scan_lock(self):
        fd = os.open(self.paths.scan_lock, os.O_RDWR)
        fcntl.flock(fd, fcntl.LOCK_EX)
        self.addCleanup(os.close, fd)

    # -- I16: only the verbs ------------------------------------------------------

    def test_anything_but_a_verb_exits_2_with_no_side_effect(self):
        hostile = [
            None, "", " ", "observe ", " observe", "observe  ", "observe x", "OBSERVE", "observe\n",
            "observe;id", "observe && id", "observe|id", "$(id)", "`id`", "sh", "bash -c id",
            "scp -t /tmp", "sftp", "internal-sftp", "/usr/lib/openssh/sftp-server", "rsync --server",
            "get-capture", f"get-capture {VOLUME}", f"get-capture {VOLUME} {CAPTURE} x",
            f"get-capture ../../etc {CAPTURE}", f"get-capture {VOLUME} ../../../../etc/shadow",
            f"get-capture {VOLUME} {CAPTURE.upper()}", f"get-capture {VOLUME}/.. {CAPTURE}",
            "rescan", "rescan 123", f"rescan {REQUEST}0", f"rescan {REQUEST} {REQUEST}", f"rescan {REQUEST.upper()}",
            f"stage-update {SHA}", f"stage-update {SHA} 0", f"stage-update {SHA} -1", f"stage-update {SHA} 1e3",
            f"stage-update {SHA} 9999999999", f"stage-update {SHA[:-1]} 10", "activate-update", "activate-update HEAD",
            f"activate-update {SHA} now", "rollback-update", "get-snapshot ../scan-status.json",
            "get-snapshot /etc/passwd", "get-snapshot a/b-1-v1.json", "get-status now", "get-manifest x",
            "observe\x00", "observe\t", "x" * 300,
        ]
        before = self.tree()
        for command in hostile:
            with self.subTest(command=command):
                code, header, body = self.serve(command, stdin=b"payload")
                self.assertEqual((code, header, body), (2, "storage-viz-gate/1 err USAGE", b""))
        self.assertEqual(self.tree(), before)
        self.assertEqual(self.host.started, [])

    def test_script_reads_ssh_original_command_and_rejects_shell_text(self):
        env = dict(os.environ, SSH_ORIGINAL_COMMAND="observe; rm -rf /")
        done = subprocess.run([sys.executable, gate.__file__], env=env, stdout=subprocess.PIPE, timeout=30, check=False)
        self.assertEqual((done.returncode, done.stdout), (2, b"storage-viz-gate/1 err USAGE\n"))
        env.pop("SSH_ORIGINAL_COMMAND")
        done = subprocess.run([sys.executable, gate.__file__], env=env, stdout=subprocess.PIPE, timeout=30, check=False)
        self.assertEqual(done.returncode, 2, "an interactive login gets no shell")

    # -- I12: host-computed scan state ---------------------------------------------

    def test_scan_state_from_lock_and_run_record(self):
        run = {"schema": "storage-viz.run/2", "run_id": "f" * 32}
        for held, has_run, state in ((False, False, "idle"), (False, True, "interrupted"),
                                     (True, False, "running"), (True, True, "running")):
            with self.subTest(held=held, has_run=has_run):
                run_path = self.paths.data / "run.json"
                run_path.unlink() if run_path.exists() else None
                if has_run:
                    run_path.write_text(json.dumps(run))
                fd = None
                if held:
                    fd = os.open(self.paths.scan_lock, os.O_RDWR)
                    fcntl.flock(fd, fcntl.LOCK_EX)
                try:
                    code, header, body = self.serve("observe")
                finally:
                    if fd is not None:
                        os.close(fd)
                report = json.loads(body)
                self.assertEqual(code, 0)
                self.assertEqual(header, f"storage-viz-gate/1 ok {len(body)}")
                self.assertEqual(report["scan"]["state"], state)
                self.assertEqual(report["scan"]["run"], run if held and has_run else None)

    def test_observe_does_not_take_the_scan_lock(self):
        self.serve("observe")
        fd = os.open(self.paths.scan_lock, os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)  # raises if the gate kept it
        finally:
            os.close(fd)

    def test_observe_reports_manifest_update_state_release_and_legacy_files(self):
        manifest = b'{"schema": "storage-viz.manifest/2", "seq": 7}'
        (self.paths.data / "manifest.json").write_bytes(manifest)
        (self.paths.update / "state.json").write_text('{"active": "%s"}' % SHA)
        (self.paths.data / "scan-status.json").write_text('{"generation": "h-1-v1.json"}')
        (self.paths.requests / REQUEST).write_text(REQUEST + "\n")
        (self.paths.opt / "releases" / SHA).mkdir(parents=True)
        (self.paths.opt / "current").symlink_to(f"releases/{SHA}")
        report = json.loads(self.serve("observe")[2])
        self.assertEqual(report["manifest"]["seq"], 7)
        self.assertEqual(len(report["manifest"]["sha256"]), 64)
        self.assertEqual(report["update"], {"active": SHA})
        self.assertEqual(report["agent"], {"release_sha": SHA, "health": "ok"})
        self.assertEqual(report["requests_pending"], [REQUEST])
        self.assertEqual(report["legacy"]["published"], {"generation": "h-1-v1.json"})
        self.assertEqual(set(report["units"]), set(gate.UNITS))
        self.assertLess(len(json.dumps(report)), 64 * 1024)

    def test_observe_degrades_per_field_instead_of_refusing(self):
        big = json.dumps({"pad": "x" * (gate.MAX_JSON_FILE - 100)})
        for rel in ("scan-lifecycle.json", "scan-status.json", "run.json"):
            (self.paths.data / rel).write_text(big)
        for rel in ("progress.json", "scan-progress.json"):
            (self.paths.run / rel).write_text(big)
        (self.paths.update / "state.json").write_text('{"active": "%s"}' % SHA)
        self.hold_scan_lock()
        code, header, body = self.serve("observe")
        report = json.loads(body)
        self.assertEqual(code, 0)
        self.assertLessEqual(len(body), 64 * 1024)
        self.assertEqual(report["scan"]["state"], "running")
        self.assertEqual(report["update"], {"active": SHA}, "update state is never dropped")
        truncated = [key for key in ("lifecycle", "published", "progress") if report["legacy"][key].get("truncated")]
        self.assertTrue(truncated)
        self.assertGreater(report["legacy"]["lifecycle"].get("bytes", 0) or report["legacy"]["published"].get("bytes", 0), 1000)

    def test_rescan_recognises_a_receipt_in_a_large_manifest(self):
        receipts = [{"request_id": f"{i:032x}", "pad": "x" * 200} for i in range(200)]
        receipts.append({"request_id": REQUEST})
        manifest = json.dumps({"requests": receipts})
        self.assertGreater(len(manifest), gate.MAX_JSON_FILE)
        self.assertLess(len(manifest), 64 * 1024)
        (self.paths.data / "manifest.json").write_text(manifest)
        self.assertEqual(self.serve(f"rescan {REQUEST}")[2], b"already_received\n")

    def test_oversized_manifest_does_not_break_observe(self):
        (self.paths.data / "manifest.json").write_bytes(b"x" * (64 * 1024 + 1))
        code, _, body = self.serve("observe")
        self.assertEqual(code, 0)
        self.assertIsNone(json.loads(body)["manifest"]["seq"])

    def test_oversized_host_file_is_not_echoed(self):
        (self.paths.data / "scan-lifecycle.json").write_bytes(b"{" + b" " * (gate.MAX_JSON_FILE + 10) + b"}")
        report = json.loads(self.serve("observe")[2])
        self.assertEqual(report["legacy"]["lifecycle"], {"invalid": True})

    def test_rollback_status_and_snapshot_served_byte_exact(self):
        generation = "host-a-200-v1.json"
        status_bytes = json.dumps({"schema_version": 1, "generation": generation}).encode()
        (self.paths.data / "scan-status.json").write_bytes(status_bytes)
        (self.paths.data / "snapshots").mkdir(exist_ok=True)
        (self.paths.data / "snapshots" / generation).write_bytes(b'{"schema_version":1}')
        code, header, body = self.serve("get-status")
        self.assertEqual((code, header, body), (0, f"storage-viz-gate/1 ok {len(status_bytes)}", status_bytes))
        generation = json.loads(status_bytes)["generation"]
        snapshot = (self.paths.data / "snapshots" / generation).read_bytes()
        code, header, body = self.serve(f"get-snapshot {generation}")
        self.assertEqual((code, header, body), (0, f"storage-viz-gate/1 ok {len(snapshot)}", snapshot))
        self.assertEqual(self.serve("get-snapshot host-a-1-v1.json")[:2], (1, "storage-viz-gate/1 err SNAPSHOT_GONE"))

    def test_status_missing_and_too_large(self):
        self.assertEqual(self.serve("get-status")[:2], (1, "storage-viz-gate/1 err STATUS_MISSING"))
        (self.paths.data / "scan-status.json").write_bytes(b"x" * (64 * 1024 + 1))
        self.assertEqual(self.serve("get-status"), (1, "storage-viz-gate/1 err TOO_LARGE", b""))

    # -- captures and manifest ------------------------------------------------------

    def test_get_capture_serves_by_digest_and_refuses_links(self):
        self.assertEqual(self.serve(f"get-capture {VOLUME} {CAPTURE}")[:2], (1, "storage-viz-gate/1 err CAPTURE_GONE"))
        directory = self.paths.captures / VOLUME
        directory.mkdir()
        (directory / f"{CAPTURE}.json.gz").write_bytes(b"\x1f\x8bcapture")
        self.assertEqual(self.serve(f"get-capture {VOLUME} {CAPTURE}"), (0, "storage-viz-gate/1 ok 9", b"\x1f\x8bcapture"))
        other = "f" * 64
        (directory / f"{other}.json.gz").symlink_to("/etc/passwd")
        self.assertEqual(self.serve(f"get-capture {VOLUME} {other}")[:2], (1, "storage-viz-gate/1 err CAPTURE_GONE"))

    def test_get_manifest(self):
        self.assertEqual(self.serve("get-manifest")[:2], (1, "storage-viz-gate/1 err MANIFEST_MISSING"))
        (self.paths.data / "manifest.json").write_bytes(b'{"seq": 1}')
        self.assertEqual(self.serve("get-manifest"), (0, "storage-viz-gate/1 ok 10", b'{"seq": 1}'))

    # -- rescan -----------------------------------------------------------------------

    def test_rescan_records_the_request_and_starts_the_scan_without_blocking(self):
        self.assertEqual(self.serve(f"rescan {REQUEST}"), (0, "storage-viz-gate/1 ok 9", b"received\n"))
        self.assertEqual((self.paths.requests / REQUEST).read_text(), REQUEST + "\n")
        self.assertEqual(self.host.started, ["storage-viz-scan.service"])
        self.assertEqual(self.serve(f"rescan {REQUEST}")[2], b"already_received\n")

    def test_rescan_during_a_run_leaves_the_request_for_that_run(self):
        self.hold_scan_lock()
        self.assertEqual(self.serve(f"rescan {REQUEST}")[2], b"received\n")
        self.assertEqual(self.host.started, [])
        self.assertTrue((self.paths.requests / REQUEST).exists())

    def test_rescan_already_recorded_by_a_receipt(self):
        (self.paths.data / "manifest.json").write_text(json.dumps({"requests": [{"request_id": REQUEST}]}))
        self.assertEqual(self.serve(f"rescan {REQUEST}")[2], b"already_received\n")
        self.assertFalse((self.paths.requests / REQUEST).exists())

    def test_rescan_reports_a_failed_start(self):
        self.host.start_ok = False
        self.assertEqual(self.serve(f"rescan {REQUEST}")[2], b"rejected START_FAILED\n")

    def test_rescan_bounds_pending_requests_and_drops_stale_ones(self):
        for index in range(gate.MAX_PENDING_REQUESTS):
            path = self.paths.requests / f"{index:032x}"
            path.write_text("x")
            os.utime(path, (0, 0) if index == 0 else None)
        self.assertEqual(self.serve(f"rescan {REQUEST}")[2], b"received\n")
        self.assertFalse((self.paths.requests / f"{0:032x}").exists(), "a day-old request is dropped")
        self.assertEqual(self.serve("rescan " + "1" * 32)[2], b"rejected TOO_MANY_PENDING\n")

    # -- update channel ---------------------------------------------------------------

    def test_stage_update_stores_exactly_size_bytes_then_the_signature(self):
        bundle, signature = b"B" * 1000, b"-----BEGIN SSH SIGNATURE-----\nsig\n"
        self.assertEqual(self.serve(f"stage-update {SHA} 1000", bundle + signature)[2], b"staged\n")
        self.assertEqual((self.paths.incoming / f"{SHA}.tar.gz").read_bytes(), bundle)
        self.assertEqual((self.paths.incoming / f"{SHA}.tar.gz.sig").read_bytes(), signature)
        other = "1" * 40
        self.serve(f"stage-update {other} 3", b"xyzsig")
        self.assertEqual(sorted(p.name for p in self.paths.incoming.iterdir()), [f"{other}.tar.gz", f"{other}.tar.gz.sig"])

    def test_stage_update_refuses_short_input_and_oversized_signature(self):
        self.assertEqual(self.serve(f"stage-update {SHA} 1000", b"short")[:2], (1, "storage-viz-gate/1 err SHORT_READ"))
        self.assertEqual(self.serve(f"stage-update {SHA} 4", b"abcd")[:2], (1, "storage-viz-gate/1 err BAD_SIGNATURE_SIZE"))
        self.assertEqual(self.serve(f"stage-update {SHA} 4", b"abcd" + b"s" * 4097)[:2], (1, "storage-viz-gate/1 err BAD_SIGNATURE_SIZE"))
        self.assertEqual(self.serve(f"stage-update {SHA} {64 * 1024 * 1024 + 1}")[:2], (1, "storage-viz-gate/1 err TOO_LARGE"))
        self.assertEqual(list(self.paths.incoming.iterdir()), [])

    def test_activate_and_rollback_write_the_request_and_start_the_updater(self):
        self.assertEqual(self.serve(f"activate-update {SHA}")[2], b"requested\n")
        self.assertEqual((self.paths.incoming / "activate").read_text(), f"activate {SHA}\n")
        self.assertEqual(self.serve(f"rollback-update {SHA}")[2], b"requested\n")
        self.assertEqual((self.paths.incoming / "activate").read_text(), f"rollback {SHA}\n")
        self.assertEqual(self.host.started, ["storage-viz-agent-update.service"] * 2)
        self.host.start_ok = False
        self.assertEqual(self.serve(f"activate-update {SHA}")[:2], (1, "storage-viz-gate/1 err START_FAILED"))


class SudoersTest(unittest.TestCase):
    def test_sudoers_has_exactly_the_two_no_block_rules(self):
        path = Path(gate.__file__).resolve().parent / "sudoers/storage-viz"
        rules = [line for line in path.read_text().splitlines() if line and not line.startswith("#")]
        self.assertEqual(rules, [
            "monitoring ALL=(root) NOPASSWD: /usr/bin/systemctl start --no-block storage-viz-scan.service",
            "monitoring ALL=(root) NOPASSWD: /usr/bin/systemctl start --no-block storage-viz-agent-update.service",
        ])
        argv = gate.Host.start_unit.__code__.co_consts
        self.assertIn("--no-block", argv)

    @unittest.skipUnless(shutil.which("visudo"), "visudo is not installed")
    def test_sudoers_parses(self):
        path = Path(gate.__file__).resolve().parent / "sudoers/storage-viz"
        done = subprocess.run(["visudo", "-cf", str(path)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
        self.assertEqual(done.returncode, 0, done.stdout)


if __name__ == "__main__":
    unittest.main()
