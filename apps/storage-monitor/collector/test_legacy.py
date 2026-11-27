import contextlib
import io
import json
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

from collector import legacy
from collector.legacy import LegacyHost, merge_status, parse_probe, parse_timer, validate_lifecycle
from collector.test_bug_harness import payload_for, status_data
from collector.test_transport import FakeRunner, server
from collector.transport import OpenSshTransport, TransportError

INVOCATION = "0123456789abcdef0123456789abcdef"
RUN = "fedcba9876543210fedcba9876543210"


def probe(**fields):
    base = {"lifecycle": {}, "progress": {}, "published": {}, "services": ["ActiveState=inactive\n"],
            "timer": "", "remote_unix": 1719200120, "remote_monotonic": 100}
    return json.dumps({**base, **fields}).encode()


def manual_unit(state="active", invocation=INVOCATION):
    return f"Id={legacy.MANUAL_UNIT}\nActiveState={state}\nInvocationID={invocation}\n"


def scheduled_unit(state="active", invocation="1" * 32):
    return f"Id={legacy.SCHEDULED_UNIT}\nActiveState={state}\nInvocationID={invocation}\n"


class MergeStatusTests(unittest.TestCase):
    def test_active_service_overrides_stale_terminal_and_progress(self):
        result = merge_status({'state': 'complete', 'started_unix': 100, 'finished_unix': 120}, {'updated_unix': 119, 'scanned_files': 900}, 'activating', {}, 200)
        self.assertEqual((result['state'], result['started_unix'], result['progress_available'], result['scanned_files']), ('running', None, False, None))

    def test_validation_and_timer_absolute_time(self):
        for bad in ({'state': 'running', 'scanned_files': True}, {'state': 'running', 'current_root': 'x' * 5000}):
            with self.assertRaises(TransportError):
                validate_lifecycle(bad)
        self.assertEqual(parse_timer('ActiveState=active\nNextElapseUSecRealtime=Mon 2026-11-16 12:00:00 UTC\n')['next_run_unix'], 1794830400)
        self.assertIsNone(parse_timer('ActiveState=inactive\nNextElapseUSecRealtime=\n')['next_run_unix'])
        result = merge_status({'state': 'running', 'phase': 'scanning', 'started_unix': 100}, {'updated_unix': 110, 'scanned_files': 5}, 'inactive', {}, 120)
        self.assertEqual((result['state'], result['error_code']), ('unknown', 'SCAN_INTERRUPTED'))

    def test_stale_heartbeat_mismatched_or_missing_run_is_not_live_progress(self):
        life = {'state': 'running', 'started_unix': 100, 'run_id': 'current', 'updated_unix': 100}
        stale = merge_status(life, {'run_id': 'current', 'updated_unix': 101, 'scanned_files': 4}, 'active', {}, 200)
        self.assertEqual((stale['state'], stale['progress_available'], stale['error_code']), ('running', False, 'STALE_PROGRESS'))
        for lifecycle, progress in ((life, {'run_id': 'old', 'updated_unix': 200, 'scanned_files': 900}),
                                    ({'state': 'running', 'started_unix': 100}, {'updated_unix': 110, 'scanned_files': 900})):
            result = merge_status(lifecycle, progress, 'active', {}, 120)
            self.assertIsNone(result['scanned_files'])
            self.assertFalse(result['progress_available'])

    def test_scheduled_progress_sums_subprocess_and_preserves_scan_scope(self):
        lifecycle = {"state": "running", "started_unix": 100, "updated_unix": 110, "run_id": "overall", "progress_run_id": "second",
                     "scan_plan": "full", "phase": "scanning", "roots_completed": 2, "roots_total": 4, "scanned_files": 500}
        progress = {"run_id": "second", "updated_unix": 113, "phase": "publishing", "roots_completed": 1, "roots_total": 2, "scanned_files": 90}
        status = merge_status(lifecycle, progress, "activating", {}, 114)
        self.assertEqual((status["roots_completed"], status["roots_total"], status["scanned_files"], status["phase"]), (3, 4, 590, "scanning"))
        stale = merge_status(lifecycle, dict(progress, run_id="first"), "activating", {}, 114)
        self.assertEqual((stale["progress_available"], stale["roots_completed"]), (False, 2))
        replacement = merge_status(lifecycle, progress, "activating", {}, 200, service_started=190)
        self.assertEqual((replacement["started_unix"], replacement["progress_available"], replacement["scan_plan"]), (190, False, None))

    def test_monotonic_timer_uses_remote_clock_and_disabled_timer_is_unknown(self):
        self.assertEqual(parse_timer('ActiveState=active\nNextElapseUSecMonotonic=1h 2min 3s\n', remote_unix=10000, remote_monotonic=3600)['next_run_unix'], 10123)
        self.assertEqual(parse_timer('ActiveState=active\nUnit=storage-viz-scheduled-scan.service\n')["schedule_mode"], "due-check")
        self.assertIsNone(parse_timer('ActiveState=inactive\nNextElapseUSecMonotonic=1h 2min 3s\n', remote_unix=10000, remote_monotonic=3600)['next_run_unix'])


class ProbeTests(unittest.TestCase):
    def test_observe_runs_the_fixed_probe_and_parses_status_and_publication(self):
        payload = payload_for()
        published, _ = status_data(payload)
        runner = FakeRunner()
        runner.ssh_stdout_next = probe(
            lifecycle={'server_id': 'alpha-1', 'state': 'running', 'started_unix': 100, 'run_id': 'x'},
            progress={'run_id': 'x', 'updated_unix': 110, 'scanned_files': 5},
            services=['ActiveState=active\n'], timer='ActiveState=active\nNextElapseUSecMonotonic=3min\n',
            published=published, remote_unix=120)
        observation = LegacyHost(OpenSshTransport(runner=runner)).observe(server())
        self.assertEqual((observation.status['state'], observation.status['next_run_unix'], observation.status['scanned_files']), ('running', 200, 5))
        self.assertEqual(observation.status["published_generation"], payload["scan_generation"])
        self.assertEqual((observation.host_now, observation.publication["sha256"], observation.publication_error), (120, published["sha256"], None))
        self.assertIn('/var/lib/storage-viz/scan-lifecycle.json', runner.calls[0]['argv'][-1])
        self.assertFalse(runner.calls[0]['shell'])
        self.assertLessEqual(runner.calls[0]['timeout'], 10)
        for bad in (b'{}', b'x' * 50000, b'[]'):
            runner.ssh_stdout_next = bad
            with self.assertRaises(TransportError):
                LegacyHost(OpenSshTransport(runner=runner)).observe(server())

    def test_legacy_service_start_and_published_generation_are_independent(self):
        payload = payload_for()
        published, _ = status_data(payload)
        raw = probe(published=published, services=["ActiveState=inactive\n", "ActiveState=activating\nExecMainStartTimestampMonotonic=90000000\nInactiveExitTimestampMonotonic=89000000\n"])
        result = parse_probe(raw, server()).status
        self.assertEqual((result["state"], result["started_unix"], result["progress_available"]), ("running", 1719200110, False))
        self.assertEqual((result["published_generation"], result["published_finished_unix"]), (payload["scan_generation"], payload["scan_finished_unix"]))

    def test_scan_facts_tie_the_manual_unit_invocation_and_the_agent_run(self):
        lifecycle = {'server_id': 'alpha-1', 'state': 'running', 'started_unix': 100, 'run_id': RUN, 'scan_plan': 'ssd'}
        manual = parse_probe(probe(lifecycle=lifecycle, services=[manual_unit(), scheduled_unit("inactive", "")]), server()).scan
        self.assertEqual((manual.invocation, manual.full, manual.run_id, manual.run_state), (INVOCATION, True, RUN, "running"))
        scheduled = parse_probe(probe(lifecycle=lifecycle, services=[manual_unit("inactive", ""), scheduled_unit()]), server()).scan
        self.assertEqual((scheduled.invocation, scheduled.full), ("1" * 32, False))
        full = parse_probe(probe(lifecycle=dict(lifecycle, scan_plan="full"), services=[scheduled_unit()]), server()).scan
        self.assertTrue(full.full)
        done = dict(lifecycle, state="complete", generation="alpha-1-1719200000-v1", scan_plan="full", finished_unix=150)
        idle = parse_probe(probe(lifecycle=done, services=[manual_unit("inactive", ""), scheduled_unit("inactive", "")]), server()).scan
        self.assertEqual((idle.invocation, idle.run_state, idle.run_full, idle.generation), (None, "complete", True, "alpha-1-1719200000-v1"))

    def test_identity_and_publication_problems_are_typed(self):
        with self.assertRaises(TransportError) as raised:
            parse_probe(probe(lifecycle={'server_id': 'beta-2', 'state': 'idle'}), server())
        self.assertEqual(raised.exception.code, "IDENTITY_MISMATCH")
        self.assertEqual(parse_probe(probe(published={'invalid': True}), server()).publication_error, "BAD_STATUS")
        published, _ = status_data(payload_for("beta-2"))
        observation = parse_probe(probe(published=published), server())
        self.assertIsNone(observation.publication)
        self.assertIsNotNone(observation.publication_error)

    def test_remote_probe_reads_exact_monotonic_deadline_for_long_uptime(self):
        calls = []
        bus_output = 't 5356923000000\n'

        def run(argv, **kwargs):
            calls.append(argv)
            if argv[0] == '/usr/bin/busctl':
                if bus_output is None:
                    raise FileNotFoundError('busctl unavailable')
                return subprocess.CompletedProcess(argv, 0, stdout=bus_output)
            if 'storage-viz-scan.timer' in argv:
                return subprocess.CompletedProcess(argv, 0, stdout='ActiveState=active\nNextElapseUSecMonotonic=2month 1d 3min 3s\n')
            return subprocess.CompletedProcess(argv, 0, stdout='ActiveState=inactive\n')

        with tempfile.TemporaryDirectory() as td:
            script = legacy.REMOTE_OBSERVATION_SCRIPT.replace('/var/lib/storage-viz/scan-lifecycle.json', td + '/life').replace(
                '/run/storage-viz/scan-progress.json', td + '/progress').replace('/var/lib/storage-viz/scan-status.json', td + '/status')
            for bus_output, expected in (('t 5356923000000\n', 10123), ('t 18446744073709551615', None), ('t 0', None), ('s invalid', None), (None, None)):
                output = io.StringIO()
                with patch('subprocess.run', side_effect=run), contextlib.redirect_stdout(output):
                    exec(script, {})
                obs = json.loads(output.getvalue())
                self.assertEqual(parse_timer(obs['timer'], remote_unix=10000, remote_monotonic=5356800)['next_run_unix'], expected)
        self.assertTrue(any('/org/freedesktop/systemd1/unit/storage_2dviz_2dscan_2etimer' in call for call in calls))
        self.assertTrue(any('--property=Id,ActiveState,InvocationID' in " ".join(call) for call in calls))


class V2ProbeTests(unittest.TestCase):
    """The fixed probe on a v2 host: it reports the manifest's sha and seq, run.json and the mailbox."""

    def run_probe(self, files, unit_state="inactive"):
        import hashlib, pathlib
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            (root / "requests").mkdir()
            for name, data in files.items():
                (root / name).write_bytes(data)
            script = legacy.REMOTE_OBSERVATION_SCRIPT
            for remote, local in (("/var/lib/storage-viz/manifest.json", "manifest.json"), ("/var/lib/storage-viz/run.json", "run.json"),
                                  ("/var/lib/storage-viz/requests", "requests"), ("/run/storage-viz/progress.json", "progress.json"),
                                  ("/var/lib/storage-viz/scan-lifecycle.json", "life"), ("/run/storage-viz/scan-progress.json", "progress"),
                                  ("/var/lib/storage-viz/scan-status.json", "status")):
                script = script.replace(remote, str(root / local))

            def run(argv, **kwargs):
                if "storage-viz-scan.timer" in argv:
                    return subprocess.CompletedProcess(argv, 0, stdout="ActiveState=active\n")
                return subprocess.CompletedProcess(argv, 0, stdout=f"Id={legacy.MANUAL_UNIT}\nActiveState={unit_state}\n")

            output = io.StringIO()
            with patch("subprocess.run", side_effect=run), contextlib.redirect_stdout(output):
                exec(script, {})
            manifest = files.get("manifest.json")
            return parse_probe(output.getvalue(), server()), hashlib.sha256(manifest).hexdigest() if manifest else None

    def test_v2_host_reports_manifest_pointer_run_and_pending_requests(self):
        manifest = json.dumps({"schema": "storage-viz.manifest/2", "seq": 1842}).encode()
        run = json.dumps({"run_id": RUN, "request_ids": ["a" * 32]}).encode()
        progress = json.dumps({"run_id": RUN, "updated_unix": int(time.time()), "bytes": 7, "files": 3, "dirs": 1,
                               "roots_completed": 1, "roots_total": 2}).encode()
        obs, digest = self.run_probe({"manifest.json": manifest, "run.json": run, "progress.json": progress,
                                      "requests/" + "b" * 32: b"x"}, unit_state="active")
        self.assertEqual((obs.manifest.sha256, obs.manifest.seq), (digest, 1842))
        self.assertEqual((obs.run.scanning, obs.run.run_id, obs.run.request_ids, obs.run.pending),
                         (True, RUN, frozenset({"a" * 32}), frozenset({"b" * 32})))
        self.assertEqual((obs.status["state"], obs.status["scanned_bytes"], obs.status["progress_available"]), ("running", 7, True))

    def test_v2_run_left_behind_without_a_unit_is_interrupted(self):
        obs, _ = self.run_probe({"manifest.json": b'{"seq": 1}', "run.json": json.dumps({"run_id": RUN, "request_ids": []}).encode()})
        self.assertEqual((obs.run.present, obs.run.scanning), (True, False))
        self.assertEqual((obs.status["state"], obs.status["error_code"]), ("unknown", "SCAN_INTERRUPTED"))

    def test_v1_files_left_on_a_v2_host_do_not_claim_a_newer_scan(self):
        status, _ = status_data(payload_for(started=1719201000))
        obs, _ = self.run_probe({"manifest.json": b'{"seq": 1}', "status": json.dumps(status).encode()})
        self.assertIsNone(obs.status["published_generation"])
        self.assertEqual(obs.status["state"], "idle")

    def test_v1_host_has_no_manifest_pointer(self):
        obs, _ = self.run_probe({})
        self.assertIsNone(obs.manifest)

    def test_malformed_v2_facts_are_bad_telemetry(self):
        for v2 in ({"manifest": {"invalid": True}, "run": {}, "requests_pending": []},
                   {"manifest": {"sha256": "x", "seq": 1}, "run": {}, "requests_pending": []},
                   {"manifest": {"sha256": "a" * 64, "seq": 1}, "run": {}, "requests_pending": ["../x"]}):
            with self.assertRaises(TransportError) as info:
                parse_probe(probe(v2=v2), server())
            self.assertEqual(info.exception.code, "BAD_TELEMETRY")


class LegacyVolumeTest(unittest.TestCase):
    """SHIM(M2b..M5c) S3: the volumes a v1 snapshot shows on the v2 fleet."""

    def test_only_scanned_or_failed_roots_become_volumes(self):
        from collector.test_snapshot import base_payload
        payload = base_payload()
        root = payload["selected_roots"][0]
        payload["selected_roots"] += [
            {**root, "mount_id": "archive", "major_minor": "8:2", "mountpoint": "/archive", "scan_root": "/archive",
             "status": "failed", "scanned_bytes": 0, "scanned_files": 0, "scanned_dirs": 0, "error_code": "EIO"},
            # The v1 agent lists every mount its policy declined as a skipped root.
            {**root, "mount_id": "proc", "major_minor": "0:22", "mount_source": "proc", "mountpoint": "/proc",
             "scan_root": "/proc", "fstype": "proc", "status": "skipped", "scanned_bytes": 0, "scanned_files": 0,
             "scanned_dirs": 0},
        ]
        manifest, captures, _ = legacy.legacy_volume(payload, "c" * 64)
        self.assertEqual([volume.mountpoint for volume in manifest.volumes], ["/", "/archive"])
        self.assertEqual([volume.capacity.total if volume.capacity else None for volume in manifest.volumes], [100, None])
        self.assertEqual(len(captures), 1)

    def test_roots_on_one_filesystem_are_one_volume(self):
        # Real v1 snapshots (turing, lecun, ace, neo) scan /home and /data on the root
        # filesystem. They are one volume with two roots: one capacity, counted once.
        import copy
        from collector.test_snapshot import base_payload
        payload = base_payload()
        root, mount = payload["selected_roots"][0], payload["mounts"][0]
        root["capacity_id"] = "dev-8-1"
        data_mount = copy.deepcopy(mount)
        data_mount.update(path="/data", mount_id="data", scan_root="/data")
        payload["mounts"].append(data_mount)
        payload["selected_roots"].append({**root, "mount_id": "data", "scan_root": "/data"})
        for user in payload["users"]:
            user["by_mount"] = {**user["by_mount"], "/data": 0}
        manifest, captures, _ = legacy.legacy_volume(payload, "c" * 64)
        self.assertEqual([volume.mountpoint for volume in manifest.volumes], ["/"])
        volume = manifest.volumes[0]
        self.assertEqual([r.scan_root for r in volume.roots], ["/home", "/data"])
        self.assertEqual(volume.capacity.total, 100)
        sha, capture = captures[volume.volume_id]
        self.assertEqual(sorted(r.scan_root for r in capture.roots), ["/data", "/home"])
