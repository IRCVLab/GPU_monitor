import pathlib
import tempfile
import threading
import unittest
from collector.test_service import FakeClock, FakeTransport, make_server
from collector.store import CentralStore
from collector.service import PollService
from collector import transport

class TelemetryTests(unittest.TestCase):
    def poll_status(self, service):
        service.poll_scan_status_once()
        for worker in service._telemetry_threads.values():
            worker.join(3)
            self.assertFalse(worker.is_alive())

    def test_overview_and_detail_reads_never_start_a_remote_observation(self):
        with tempfile.TemporaryDirectory() as td:
            tx = FakeTransport()
            svc = PollService([make_server()], CentralStore(td), tx)
            for _ in range(10):
                self.assertEqual(svc.server_summaries()[0]["scan_status"]["state"], "unknown")
                self.assertEqual(svc.scan_status_for_api("alpha-1")["state"], "unknown")
            self.assertEqual(tx.active_state_calls, [])
            self.assertEqual(tx.fetch_status_calls, [])

    def test_active_service_overrides_stale_terminal_and_progress(self):
        from collector.scan_telemetry import merge_status
        result = merge_status({'state':'complete', 'started_unix':100, 'finished_unix':120}, {'updated_unix':119,'scanned_files':900}, 'activating', {}, 200)
        self.assertEqual(result['state'], 'running')
        self.assertIsNone(result['started_unix'])
        self.assertFalse(result['progress_available'])
        self.assertIsNone(result['scanned_files'])

    def test_validation_and_timer_absolute_time(self):
        from collector.scan_telemetry import validate_lifecycle, parse_timer, merge_status
        with self.assertRaises(transport.TransportError):
            validate_lifecycle({'state':'running','scanned_files':True})
        with self.assertRaises(transport.TransportError):
            validate_lifecycle({'state':'running','current_root':'x'*5000})
        timer = parse_timer('ActiveState=active\nNextElapseUSecRealtime=Mon 2026-11-16 12:00:00 UTC\n')
        self.assertEqual(timer['next_run_unix'],1794830400)
        self.assertIsNone(parse_timer('ActiveState=inactive\nNextElapseUSecRealtime=\n')['next_run_unix'])
        result = merge_status({'state':'running','phase':'scanning','started_unix':100}, {'updated_unix':110,'scanned_files':5}, 'inactive', {}, 120)
        self.assertEqual(result['state'], 'unknown')
        self.assertEqual(result['error_code'], 'SCAN_INTERRUPTED')

    def test_status_available_while_snapshot_lock_held_and_ttl_bounded(self):
        with tempfile.TemporaryDirectory() as td:
            t = FakeTransport(); clock = FakeClock(200)
            t.fetch_scan_status = lambda server: transport.RemoteScanObservation({'state':'running','phase':'scanning','started_unix':100,'progress_available':True,'scanned_files':9})
            svc = PollService([make_server()], CentralStore(td), t, clock=clock)
            ready = threading.Event(); release = threading.Event()
            def hold():
                with svc._locks['alpha-1']: ready.set(); release.wait(3)
            th = threading.Thread(target=hold); th.start(); ready.wait(1)
            try:
                self.poll_status(svc)
                result = svc.scan_status_for_api('alpha-1')
                self.assertEqual(result['state'],'running')
                self.assertEqual(result['scanned_files'],9)
                t.fetch_scan_status = lambda server: (_ for _ in ()).throw(transport.TransportError('TIMEOUT','timeout'))
                self.poll_status(svc)
                self.assertEqual(svc.scan_status_for_api('alpha-1')['state'],'running')
                clock.now += 6
                self.poll_status(svc)
                failed = svc.scan_status_for_api('alpha-1')
                self.assertEqual(failed['state'],'unknown')
                self.assertEqual(failed['error_code'],'TIMEOUT')
            finally: release.set(); th.join()

    def test_legacy_agent_explicit_unavailable(self):
        with tempfile.TemporaryDirectory() as td:
            svc = PollService([make_server()],CentralStore(td),FakeTransport())
            self.poll_status(svc)
            result = svc.scan_status_for_api('alpha-1')
            self.assertEqual(result['state'],'idle')
            self.assertFalse(result['progress_available'])
            self.assertIsNone(result['next_run_unix'])

    def test_overview_retains_observation_while_refresh_runs(self):
        with tempfile.TemporaryDirectory() as td:
            t = FakeTransport(); clock = FakeClock(200)
            t.fetch_scan_status = lambda server: transport.RemoteScanObservation({'state':'running','scanned_files':9})
            svc = PollService([make_server()],CentralStore(td),t,clock=clock)
            self.poll_status(svc)
            clock.now += 30
            entered = threading.Event(); release = threading.Event()
            def slow(server):
                entered.set(); release.wait(3)
                return transport.RemoteScanObservation({'state':'running','scanned_files':10})
            t.fetch_scan_status = slow
            try:
                svc.poll_scan_status_once()
                self.assertTrue(entered.wait(1))
                result = svc.server_summaries()[0]['scan_status']
                self.assertEqual(result['state'],'running')
                self.assertEqual(result['observed_unix'],200)
                self.assertEqual(result['scanned_files'],9)
                busy = svc.scan_status_for_api('alpha-1')
                self.assertEqual(busy['state'], 'running')
                self.assertEqual(busy['observed_unix'], 200)
            finally:
                release.set()
                for worker in svc._telemetry_threads.values(): worker.join(3)

    def test_idle_and_manual_scan_use_different_observation_intervals(self):
        with tempfile.TemporaryDirectory() as td:
            tx = FakeTransport()
            clock = FakeClock(200)
            store = CentralStore(td)
            svc = PollService([make_server()], store, tx, clock=clock)
            self.poll_status(svc)
            clock.now += 59
            self.poll_status(svc)
            self.assertEqual(tx.active_state_calls, ["alpha-1"])
            clock.now += 1
            self.poll_status(svc)
            self.assertEqual(tx.active_state_calls, ["alpha-1"] * 2)

            store.update_state("alpha-1", active_job={
                "id": "job-1", "server_id": "alpha-1", "kind": "rescan",
                "state": "running", "actor": "operator", "requested_unix": clock.now,
                "started_unix": clock.now, "finished_unix": None, "result_code": None,
            })
            tx.active_state = "active"
            clock.now += 5
            self.poll_status(svc)
            self.assertEqual(svc.scan_status_for_api("alpha-1")["state"], "running")
            self.assertEqual(tx.active_state_calls, ["alpha-1"] * 3)
            clock.now += 5
            self.poll_status(svc)
            self.assertEqual(tx.active_state_calls, ["alpha-1"] * 4)

    def test_slow_host_does_not_delay_other_hosts_or_spawn_duplicate_worker(self):
        with tempfile.TemporaryDirectory() as td:
            tx = FakeTransport()
            calls = []
            entered = threading.Event()
            complete = threading.Event()
            release = threading.Event()

            def observe(server):
                calls.append(server.id)
                if server.id == "alpha-1":
                    entered.set()
                    release.wait(3)
                else:
                    complete.set()
                return transport.RemoteScanObservation({"state": "idle"})

            tx.fetch_scan_status = observe
            svc = PollService([make_server(), make_server("beta-2")], CentralStore(td), tx)
            try:
                svc.poll_scan_status_once()
                self.assertTrue(entered.wait(1))
                self.assertTrue(complete.wait(1))
                svc.poll_scan_status_once()
            finally:
                release.set()
                for worker in svc._telemetry_threads.values():
                    worker.join(3)
            self.assertCountEqual(calls, ["alpha-1", "beta-2"])
            self.assertEqual(svc.scan_status_for_api("beta-2")["state"], "idle")

    def test_stale_heartbeat_and_mismatched_run_are_not_live_progress(self):
        from collector.scan_telemetry import merge_status
        life = {'state':'running','started_unix':100,'run_id':'current','updated_unix':100}
        stale = merge_status(life,{'run_id':'current','updated_unix':101,'scanned_files':4},'active',{},200)
        self.assertEqual(stale['state'],'running')
        self.assertFalse(stale['progress_available'])
        self.assertEqual(stale['error_code'],'STALE_PROGRESS')
        mismatch = merge_status(life,{'run_id':'old','updated_unix':200,'scanned_files':900},'active',{},110)
        self.assertIsNone(mismatch['scanned_files'])
        self.assertFalse(mismatch['progress_available'])

    def test_monotonic_timer_uses_remote_clock_and_disabled_timer_is_unknown(self):
        from collector.scan_telemetry import parse_timer
        result = parse_timer('ActiveState=active\nNextElapseUSecMonotonic=1h 2min 3s\n',remote_unix=10000,remote_monotonic=3600)
        self.assertEqual(result['next_run_unix'],10123)
        scheduled = parse_timer('ActiveState=active\nUnit=storage-viz-scheduled-scan.service\n')
        self.assertEqual(scheduled["schedule_mode"], "due-check")
        result = parse_timer('ActiveState=inactive\nNextElapseUSecMonotonic=1h 2min 3s\n',remote_unix=10000,remote_monotonic=3600)
        self.assertIsNone(result['next_run_unix'])

    def test_fixed_transport_command_and_malformed_observation(self):
        import json
        from collector.test_transport import FakeRunner, server
        runner = FakeRunner()
        obs = {'lifecycle':{'server_id':'alpha-1','state':'running','started_unix':100,'run_id':'x'},'progress':{'run_id':'x','updated_unix':110,'scanned_files':5},'service':'ActiveState=active\n','timer':'ActiveState=active\nNextElapseUSecMonotonic=3min\n','remote_unix':120,'remote_monotonic':100}
        runner.ssh_stdout_next = json.dumps(obs).encode()
        tx = transport.OpenSshTransport(runner=runner)
        result = tx.fetch_scan_status(server()).status
        self.assertEqual(result['state'],'running')
        self.assertEqual(result['next_run_unix'],200)
        self.assertEqual(result['scanned_files'],5)
        self.assertIn('/var/lib/storage-viz/scan-lifecycle.json',runner.calls[0]['argv'][-1])
        self.assertFalse(runner.calls[0]['shell'])
        self.assertLessEqual(runner.calls[0]['timeout'],10)
        for bad in (b'{}', b'x'*50000, b'[]'):
            runner.ssh_stdout_next = bad
            with self.assertRaises(transport.TransportError): tx.fetch_scan_status(server())

    def test_terminal_observation_refreshes_automatic_scan_snapshot(self):
        from collector.test_service import payload_for, status_data
        with tempfile.TemporaryDirectory() as td:
            tx = FakeTransport(); payload = payload_for(); status, data = status_data(payload)
            tx.status['alpha-1'] = status; tx.downloads['alpha-1'] = (status,data)
            tx.fetch_scan_status = lambda server: transport.RemoteScanObservation({'state':'complete','generation':payload['scan_generation']})
            svc = PollService([make_server()],CentralStore(td),tx)
            self.poll_status(svc)
            svc._refresh_threads['alpha-1'].join(3)
            self.assertEqual(svc.load_snapshot_for_api('alpha-1')['scan_generation'],payload['scan_generation'])
            self.assertEqual(svc.scan_status_for_api('alpha-1')['snapshot_generation'],payload['scan_generation'])
            self.assertEqual(tx.fetch_status_calls, ['alpha-1'])

    def test_refresh_reuses_probe_publication_and_still_checks_after_transfer(self):
        import json
        from collector.test_service import payload_for, status_data
        from collector.test_transport import FakeRunner

        payload = payload_for()
        published, data = status_data(payload)
        for changed in (False, True):
            with self.subTest(changed=changed), tempfile.TemporaryDirectory() as td:
                runner = FakeRunner()
                newer, _ = status_data(payload_for(started=1719201000))
                post_transfer = dict(newer, status="failed") if changed else published
                runner.statuses = [post_transfer, post_transfer]
                runner.snapshots[published["generation"]] = data
                runner.ssh_stdout_next = json.dumps({
                    "lifecycle": {}, "progress": {}, "published": published,
                    "service": "ActiveState=inactive", "timer": "",
                    "remote_unix": 1719200120, "remote_monotonic": 100,
                }).encode()
                svc = PollService([make_server()], CentralStore(td), transport.OpenSshTransport(runner=runner))
                self.poll_status(svc)
                svc._refresh_threads["alpha-1"].join(3)
                self.assertFalse(svc._refresh_threads["alpha-1"].is_alive())
                transfers = [call["input"].split()[1] for call in runner.calls if call["input"] is not None]
                expected_transfers = [
                    (transport.SNAPSHOT_DIR + "/" + published["generation"]).encode(),
                    transport.STATUS_PATH.encode(),
                ]
                if changed:
                    expected_transfers.append(transport.STATUS_PATH.encode())
                self.assertEqual(transfers, expected_transfers)
                public_status = svc.scan_status_for_api("alpha-1")
                self.assertNotIn("publication", public_status)
                for key in ("sha256", "byte_size", "config_digest"):
                    self.assertNotIn(key, public_status)
                if changed:
                    self.assertIsNone(svc.load_snapshot_for_api("alpha-1"))
                    self.assertEqual(svc.load_state_for_api("alpha-1")["last_error_code"], "REMOTE_SCAN_FAILED")
                else:
                    self.assertEqual(public_status["snapshot_generation"], payload["scan_generation"])

    def test_probe_cannot_reuse_publication_older_than_another_committed_pull(self):
        import json
        from collector.test_service import payload_for, status_data
        from collector.test_transport import FakeRunner

        worker_server = make_server()
        older, _ = status_data(payload_for())
        newer_payload = payload_for(started=1719201000)
        newer, data = status_data(newer_payload)
        runner = FakeRunner()
        runner.statuses = [newer, newer, newer]
        runner.snapshots[newer["generation"]] = data
        runner.ssh_stdout_next = json.dumps({
            "lifecycle": {}, "progress": {}, "published": older,
            "service": "ActiveState=inactive", "timer": "",
            "remote_unix": 1719201120, "remote_monotonic": 100,
        }).encode()
        with tempfile.TemporaryDirectory() as td:
            svc = PollService([worker_server], CentralStore(td), transport.OpenSshTransport(runner=runner))
            self.assertEqual(svc.poll_server(worker_server.id)["latest_pull_status"], "succeeded")
            runner.calls.clear()
            self.poll_status(svc)
            svc._refresh_threads[worker_server.id].join(3)
            transfers = [call["input"].split()[1] for call in runner.calls if call["input"] is not None]
            self.assertEqual(transfers, [transport.STATUS_PATH.encode()])
            self.assertEqual(svc.load_state_for_api(worker_server.id)["latest_pull_status"], "succeeded")
            self.assertEqual(svc.scan_status_for_api(worker_server.id)["snapshot_generation"], newer_payload["scan_generation"])

    def test_legacy_scan_start_and_published_generation_are_independent(self):
        import json
        from collector.test_service import payload_for, status_data
        from collector.test_transport import FakeRunner, server
        payload = payload_for()
        published, _ = status_data(payload)
        runner = FakeRunner()
        runner.ssh_stdout_next = json.dumps({
            "lifecycle": {}, "progress": {}, "published": published,
            "services": ["ActiveState=inactive\n", "ActiveState=activating\nExecMainStartTimestampMonotonic=90000000\nInactiveExitTimestampMonotonic=89000000\n"],
            "timer": "", "remote_unix": 1719200120, "remote_monotonic": 100,
        }).encode()
        result = transport.OpenSshTransport(runner=runner).fetch_scan_status(server()).status
        self.assertEqual(result["state"], "running")
        self.assertEqual(result["started_unix"], 1719200110)
        self.assertFalse(result["progress_available"])
        self.assertEqual(result["published_generation"], payload["scan_generation"])
        self.assertEqual(result["published_finished_unix"], payload["scan_finished_unix"])

    def test_legacy_idle_publication_refreshes_without_new_rescan(self):
        from collector.test_service import payload_for, status_data
        with tempfile.TemporaryDirectory() as td:
            payload = payload_for()
            tx = FakeTransport()
            tx.status["alpha-1"], data = status_data(payload)
            tx.downloads["alpha-1"] = (tx.status["alpha-1"], data)
            tx.fetch_scan_status = lambda _: transport.RemoteScanObservation({
                "state": "idle", "published_generation": payload["scan_generation"],
                "published_finished_unix": payload["scan_finished_unix"],
            })
            svc = PollService([make_server()], CentralStore(td), tx)
            self.poll_status(svc)
            svc._refresh_threads["alpha-1"].join(3)
            status = svc.scan_status_for_api("alpha-1")
            self.assertEqual(status["snapshot_generation"], payload["scan_generation"])
            self.assertEqual(status["snapshot_finished_unix"], payload["scan_finished_unix"])
            self.assertEqual(tx.rescans, [])

    def test_scheduled_progress_sums_subprocess_and_preserves_scan_scope(self):
        from collector.scan_telemetry import merge_status
        lifecycle = {"state": "running", "started_unix": 100, "updated_unix": 110,
                     "run_id": "overall", "progress_run_id": "second", "scan_plan": "full",
                     "phase": "scanning", "roots_completed": 2, "roots_total": 4, "scanned_files": 500}
        progress = {"run_id": "second", "updated_unix": 113, "phase": "publishing",
                    "roots_completed": 1, "roots_total": 2, "scanned_files": 90}
        status = merge_status(lifecycle, progress, "activating", {}, 114)
        self.assertEqual(status["roots_completed"], 3)
        self.assertEqual(status["roots_total"], 4)
        self.assertEqual(status["scanned_files"], 590)
        self.assertEqual(status["phase"], "scanning")
        stale = merge_status(lifecycle, dict(progress, run_id="first"), "activating", {}, 114)
        self.assertFalse(stale["progress_available"])
        self.assertEqual(stale["roots_completed"], 2)
        replacement = merge_status(lifecycle, progress, "activating", {}, 200, service_started=190)
        self.assertEqual(replacement["started_unix"], 190)
        self.assertFalse(replacement["progress_available"])
        self.assertIsNone(replacement["scan_plan"])

    def test_partial_duration_is_not_successful_eta_baseline(self):
        from collector.test_service import payload_for
        from collector.service import _overview_snapshot
        complete = payload_for(); complete['scan_duration_sec'] = 42
        partial = payload_for(partial=True); partial['scan_duration_sec'] = 12
        self.assertEqual(_overview_snapshot(complete)['estimate_duration_sec'],42)
        self.assertIsNone(_overview_snapshot(partial)['estimate_duration_sec'])
        with tempfile.TemporaryDirectory() as td:
            svc = PollService([make_server()],CentralStore(td),FakeTransport())
            svc._overview_cache['alpha-1'] = _overview_snapshot(complete)
            result = svc._status_with_history('alpha-1',{'last_finished_unix':999,'last_duration_sec':12,'estimate_duration_sec':None})
            self.assertEqual(result['last_duration_sec'],12)
            self.assertEqual(result['estimate_duration_sec'], 42)
            scoped = svc._status_with_history('alpha-1', {'scan_plan': 'ssd', 'estimate_duration_sec': None})
            self.assertIsNone(scoped["estimate_duration_sec"])
            complete["selected_roots"][0]["scan_finished_unix"] = complete["scan_finished_unix"]
            self.assertIsNone(_overview_snapshot(complete)["estimate_duration_sec"])

    def test_estimate_is_longest_recent_completion_and_survives_restart(self):
        def complete(started, duration):
            finished = started + duration
            return {"state": "complete", "scan_plan": "full", "started_unix": started, "finished_unix": finished,
                    "last_finished_unix": finished, "last_duration_sec": duration, "estimate_duration_sec": duration}
        running = {"state": "running", "phase": "scanning", "scan_plan": "full", "started_unix": 1795683862,
                   "last_finished_unix": 1795604525, "last_duration_sec": 24, "estimate_duration_sec": 24}
        with tempfile.TemporaryDirectory() as td:
            store = CentralStore(td)
            clock = FakeClock(1795600000)
            tx = FakeTransport()
            svc = PollService([make_server("hinton")], store, tx, clock=clock)
            # A cold full run, then a warm one: the agent's own estimate drops to the warm 24 s.
            for observed in (complete(1795500000, 900), complete(1795500000, 900), complete(1795604501, 24), running):
                tx.fetch_scan_status = lambda server, observed=observed: transport.RemoteScanObservation(dict(observed))
                clock.now += 60
                self.poll_status(svc)
            self.assertEqual(svc.scan_status_for_api("hinton")["estimate_duration_sec"], 900)

            restarted = PollService([make_server("hinton")], store, tx, clock=clock)
            clock.now += 60
            self.poll_status(restarted)
            status = restarted.scan_status_for_api("hinton")
            self.assertEqual(status["state"], "running")
            self.assertEqual(status["estimate_duration_sec"], 900)
            self.assertNotIn("scan_duration_history", store.load_state("hinton"),
                             "history stays outside the validated state so an older collector can still load it")

    def test_history_write_failure_is_retried_without_breaking_telemetry(self):
        with tempfile.TemporaryDirectory() as td:
            store = CentralStore(td)
            tx = FakeTransport()
            tx.fetch_scan_status = lambda server: transport.RemoteScanObservation({
                "state": "complete", "scan_plan": "full", "finished_unix": 1795500900,
                "last_finished_unix": 1795500900, "last_duration_sec": 900,
            })
            clock = FakeClock(1795600000)
            svc = PollService([make_server()], store, tx, clock=clock)
            history_dir = pathlib.Path(td) / "scan-duration-history"
            history_dir.write_text("not a directory")
            clock.now += 3600
            self.poll_status(svc)
            status = svc.scan_status_for_api("alpha-1")
            self.assertEqual(status["state"], "complete")
            history_dir.unlink()
            tx.fetch_scan_status = lambda server: transport.RemoteScanObservation({"state": "running", "scan_plan": "full"})
            clock.now += 3600
            self.poll_status(svc)
            self.assertIsNone(svc.scan_status_for_api("alpha-1")["estimate_duration_sec"])
            tx.fetch_scan_status = lambda server: transport.RemoteScanObservation({
                "state": "complete", "scan_plan": "full", "finished_unix": 1795500900,
                "last_finished_unix": 1795500900, "last_duration_sec": 900,
            })
            clock.now += 3600
            self.poll_status(svc)
            restarted = PollService([make_server()], store, tx, clock=clock)
            tx.fetch_scan_status = lambda server: transport.RemoteScanObservation({"state": "running", "scan_plan": "full"})
            clock.now += 3600
            self.poll_status(restarted)
            self.assertEqual(restarted.scan_status_for_api("alpha-1")["estimate_duration_sec"], 900,
                             "a completion whose write failed is retried and kept across a restart")

    def test_missing_run_identity_does_not_validate_uncorrelated_progress(self):
        from collector.scan_telemetry import merge_status
        result = merge_status({'state':'running','started_unix':100},{'updated_unix':110,'scanned_files':900},'active',{},120)
        self.assertFalse(result['progress_available'])
        self.assertIsNone(result['scanned_files'])

    def test_remote_probe_reads_exact_monotonic_deadline_for_long_uptime(self):
        import contextlib
        import io
        import json
        import subprocess
        from unittest.mock import patch
        from collector.scan_telemetry import REMOTE_OBSERVATION_SCRIPT, parse_timer
        calls = []
        bus_output = 't 5356923000000\n'
        def run(argv, **kwargs):
            calls.append(argv)
            if argv[0] == '/usr/bin/busctl':
                if bus_output is None: raise FileNotFoundError('busctl unavailable')
                return subprocess.CompletedProcess(argv,0,stdout=bus_output)
            if 'storage-viz-scan.timer' in argv:
                return subprocess.CompletedProcess(argv,0,stdout='ActiveState=active\nNextElapseUSecMonotonic=2month 1d 3min 3s\n')
            return subprocess.CompletedProcess(argv,0,stdout='ActiveState=inactive\n')
        with tempfile.TemporaryDirectory() as td:
            script = REMOTE_OBSERVATION_SCRIPT.replace('/var/lib/storage-viz/scan-lifecycle.json',td+'/life').replace('/run/storage-viz/scan-progress.json',td+'/progress').replace('/var/lib/storage-viz/scan-status.json',td+'/status')
            output = io.StringIO()
            with patch('subprocess.run',side_effect=run), contextlib.redirect_stdout(output):
                exec(script,{})
        obs = json.loads(output.getvalue())
        result = parse_timer(obs['timer'],remote_unix=10000,remote_monotonic=5356800)
        self.assertEqual(result['next_run_unix'],10123)
        self.assertTrue(any('/org/freedesktop/systemd1/unit/storage_2dviz_2dscan_2etimer' in call for call in calls))
        for bus_output in ('t 18446744073709551615', 't 0', 's invalid', None):
            with self.subTest(bus_output=bus_output):
                output = io.StringIO()
                with patch('subprocess.run',side_effect=run), contextlib.redirect_stdout(output):
                    exec(script,{})
                result = parse_timer(json.loads(output.getvalue())['timer'],remote_unix=10000,remote_monotonic=5356800)
                self.assertIsNone(result['next_run_unix'])
