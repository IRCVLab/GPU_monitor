import contextlib
import io
import json
import subprocess
import tempfile
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
