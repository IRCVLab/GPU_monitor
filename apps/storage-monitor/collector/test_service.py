import copy
import json
import pathlib
import tempfile
import threading
import time
import unittest

from collector.snapshot import DesiredServer, validate_download
from collector.inventory import Server
from collector.store import CentralStore
from collector.test_snapshot import base_payload, status_for


def make_server(server_id="alpha-1", order=1, enabled=True, digest="a" * 64):
    return Server(
        id=server_id,
        display_name=server_id,
        order=order,
        host=f"{server_id}.example.test",
        port=22,
        enabled=enabled,
        username="monitoring",
        identity_file=pathlib.PurePosixPath(f"/etc/storage-viz/{server_id}.key"),
        known_hosts_file=pathlib.PurePosixPath("/etc/storage-viz/known_hosts"),
        scanner={"server_id": server_id},
        scanner_digest=digest,
    )


def payload_for(server_id="alpha-1", started=1719200000, digest="a" * 64, partial=False, total_failed=False):
    generation = f"{server_id}-{started}-v1"
    payload = base_payload(server_id=server_id, generation=generation)
    payload["hostname"] = f"{server_id}.example.test"
    payload["scan_started_unix"] = started
    payload["scan_finished_unix"] = started + 42
    payload["config_digest"] = digest
    if partial:
        payload["selected_roots"].append({
            "mount_id": "archive", "major_minor": "8:2", "mount_source": "/dev/storage-viz/archive",
            "mount_root": "/", "mountpoint": "/archive", "scan_root": "/archive", "fstype": "xfs",
            "status": "failed", "scanned_bytes": 0, "scanned_files": 0, "scanned_dirs": 0,
            "blocked_count": 0, "error_count": 1, "error_code": "EIO",
        })
    if total_failed:
        payload["selected_roots"][0].update(status="failed", scanned_bytes=0, scanned_files=0, scanned_dirs=0, error_count=1, error_code="EIO")
        payload["mounts"] = []
    return payload


def status_data(payload, status_value=None):
    st, data = status_for(payload, config_digest=payload["config_digest"])
    if status_value is not None:
        st["status"] = status_value
    elif any(r["status"] == "failed" for r in payload["selected_roots"]):
        st["status"] = "partial"
    return st, data


class FakeClock:
    def __init__(self, now=1719200042 + 3600): self.now = now
    def time(self): return self.now
    def monotonic(self): return self.now


class FakeTransport:
    def __init__(self):
        self.status = {}
        self.downloads = {}
        self.status_errors = {}
        self.download_errors = {}
        self.rescans = []
        self.active_state = "inactive"
        self.active_state_errors = {}
        self.active_state_calls = []
        self.fetch_status_calls = []
        self.fetch_snapshot_calls = []
        self.block_fetch = None
        self.entered_fetch = None
        self.max_active = 0
        self.active = 0
        self.lock = threading.Lock()

    def fetch_status(self, server):
        self.fetch_status_calls.append(server.id)
        err = self.status_errors.get(server.id)
        if err: raise err
        return copy.deepcopy(self.status[server.id])

    def fetch_snapshot(self, server, expected_status=None):
        self.fetch_snapshot_calls.append((server.id, copy.deepcopy(expected_status)))
        with self.lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            if self.entered_fetch: self.entered_fetch.set()
            if self.block_fetch: self.block_fetch.wait(2)
            err = self.download_errors.get(server.id)
            if err: raise err
        finally:
            with self.lock:
                self.active -= 1
        st, data = self.downloads[server.id]
        return copy.deepcopy(st), data

    def scan_active_state(self, server):
        self.active_state_calls.append(server.id)
        err = self.active_state_errors.get(server.id)
        if err: raise err
        return self.active_state

    def start_rescan(self, server):
        self.rescans.append(server.id)
        err = self.download_errors.get((server.id, "rescan"))
        if err: raise err


class PollServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.store = CentralStore(pathlib.Path(self.tmp.name) / "state")
        self.clock = FakeClock()
        self.tx = FakeTransport()

    def service(self, servers):
        from collector.service import PollService
        return PollService(servers, self.store, self.tx, clock=self.clock)

    def observe_host_clock(self, service, server):
        from collector.transport import RemoteScanObservation
        self.tx.fetch_scan_status = lambda _: RemoteScanObservation({"state": "idle", "observed_unix": self.clock.time()})
        service._collect_scan_status(server, self.clock.time())

    def seed(self, server, started=1719200000, **kwargs):
        payload = payload_for(server.id, started=started, digest=server.scanner_digest, **kwargs)
        st, data = status_data(payload)
        valid = validate_download(st, data, DesiredServer(server.id, server.scanner_digest))
        self.assertTrue(self.store.commit_snapshot(valid).accepted)
        return st, data

    def test_unchanged_generation_skips_snapshot_transfer_and_marks_fresh(self):
        s = make_server(); old_status, old_data = self.seed(s)
        self.tx.status[s.id] = old_status; self.tx.downloads[s.id] = (old_status, old_data)
        svc = self.service([s])
        self.observe_host_clock(svc, s)
        result = svc.poll_once()
        self.assertEqual(len(self.tx.fetch_snapshot_calls), 1)
        result = svc.poll_once()
        self.assertEqual(len(self.tx.fetch_snapshot_calls), 1)
        self.assertEqual(result[s.id]["latest_pull_status"], "succeeded")
        state = self.store.load_state(s.id)
        self.assertEqual(svc.server_summaries()[0]["freshness"], "fresh")
        self.assertEqual(state["snapshot_availability"], "available")

    def test_slow_first_host_does_not_delay_healthy_host_background_poll(self):
        alpha, beta = make_server(), make_server("beta-2")
        for server in (alpha, beta):
            status, data = status_data(payload_for(server.id))
            self.tx.status[server.id] = status
            self.tx.downloads[server.id] = status, data

        entered_alpha = threading.Event()
        release_alpha = threading.Event()
        committed_alpha = threading.Event()
        committed_beta = threading.Event()
        fetch_status = self.tx.fetch_status
        commit_snapshot = self.store.commit_snapshot

        def fetch_slow_status(server):
            if server == alpha:
                entered_alpha.set()
                release_alpha.wait(5)
            return fetch_status(server)

        def record_commit(valid, **kwargs):
            result = commit_snapshot(valid, **kwargs)
            if valid.server_id == alpha.id:
                committed_alpha.set()
            else:
                committed_beta.set()
            return result

        self.tx.fetch_status = fetch_slow_status
        self.store.commit_snapshot = record_commit
        service = self.service([alpha, beta])
        service.poll_background_once()
        try:
            self.assertTrue(entered_alpha.wait(2))
            self.assertTrue(committed_beta.wait(2), "the healthy host should finish before the slow one is released")
            self.assertIsNone(self.store.load_snapshot(alpha.id))
        finally:
            release_alpha.set()
        self.assertTrue(committed_alpha.wait(5))
        self.assertEqual(self.store.load_state(beta.id)["latest_pull_status"], "succeeded")

    def test_unexpected_local_error_does_not_stop_regular_fleet_poll(self):
        alpha, beta = make_server(), make_server("beta-2")
        for server in (alpha, beta):
            status, data = status_data(payload_for(server.id))
            self.tx.status[server.id] = status
            self.tx.downloads[server.id] = status, data
        service = self.service([alpha, beta])
        load_state = self.store.load_state

        def fail_alpha_state(server_id):
            if server_id == alpha.id:
                raise ValueError("cannot read local state")
            return load_state(server_id)

        self.store.load_state = fail_alpha_state
        with self.assertLogs("collector.service", level="ERROR") as logs:
            results = service.poll_once()
        self.assertIn(alpha.id, logs.output[0])
        self.assertEqual(list(results), [alpha.id, beta.id])
        self.assertEqual(results[alpha.id]["last_error_code"], "POLL_FAILED")
        self.assertEqual(results[beta.id]["latest_pull_status"], "succeeded")
        self.assertIsNotNone(self.store.load_snapshot(beta.id))

    def test_event_refresh_shares_snapshot_transfer_bound_with_regular_poll(self):
        alpha, beta, refresh = make_server(), make_server("beta-2"), make_server("refresh-3")
        for server in (alpha, beta, refresh):
            status, data = status_data(payload_for(server.id))
            self.tx.status[server.id] = status
            self.tx.downloads[server.id] = status, data

        refresh_owns_host = threading.Event()
        refresh_observed_status = threading.Event()
        regular_transfers_started = threading.Barrier(3)
        release_regular_transfers = threading.Event()
        refresh_transfer_started = threading.Event()
        fetch_status = self.tx.fetch_status
        fetch_snapshot = self.tx.fetch_snapshot

        def observe_status(server):
            if server == refresh:
                refresh_owns_host.set()
                regular_transfers_started.wait(5)
                refresh_observed_status.set()
            return fetch_status(server)

        def track_transfer(server, **kwargs):
            if server == refresh:
                refresh_transfer_started.set()
            else:
                regular_transfers_started.wait(5)
                release_regular_transfers.wait(5)
            return fetch_snapshot(server, **kwargs)

        self.tx.fetch_status = observe_status
        self.tx.fetch_snapshot = track_transfer
        service = self.service([alpha, beta, refresh])
        event_result = {}
        event_worker = threading.Thread(target=lambda: event_result.update(service.poll_server(refresh.id)))

        event_worker.start()
        self.assertTrue(refresh_owns_host.wait(2))
        service.poll_background_once()
        try:
            self.assertTrue(refresh_observed_status.wait(2))
            self.assertFalse(refresh_transfer_started.wait(0.1))
        finally:
            release_regular_transfers.set()
            event_worker.join(6)
        self.assertFalse(event_worker.is_alive())
        self.assertTrue(refresh_transfer_started.is_set())
        self.assertEqual(event_result["latest_pull_status"], "succeeded")
        deadline = time.monotonic() + 5
        while not all(self.store.load_snapshot(server.id) for server in (alpha, beta)) and time.monotonic() < deadline:
            time.sleep(0.02)
        for server in (alpha, beta):
            self.assertEqual(self.store.load_state(server.id)["latest_pull_status"], "succeeded")

    def test_background_status_cycles_continue_while_snapshot_slots_are_busy(self):
        alpha, beta, gamma = make_server(), make_server("beta-2"), make_server("gamma-3")
        for server in (alpha, beta, gamma):
            status, data = status_data(payload_for(server.id))
            self.tx.status[server.id] = status
            self.tx.downloads[server.id] = status, data
        release_transfers = threading.Event()
        occupied = threading.Barrier(3)
        first_gamma_status = threading.Event()
        second_gamma_status = threading.Event()
        fetch_status = self.tx.fetch_status
        fetch_snapshot = self.tx.fetch_snapshot

        def record_status(server):
            result = fetch_status(server)
            if server == gamma:
                if first_gamma_status.is_set():
                    second_gamma_status.set()
                first_gamma_status.set()
            return result

        def block_first_transfers(server, **kwargs):
            if server != gamma:
                occupied.wait(5)
                release_transfers.wait(5)
            return fetch_snapshot(server, **kwargs)

        self.tx.fetch_status = record_status
        self.tx.fetch_snapshot = block_first_transfers
        service = self.service([alpha, beta, gamma])
        service.poll_background_once()
        try:
            occupied.wait(5)
            self.assertTrue(first_gamma_status.wait(2))
            self.assertEqual(len(self.tx.fetch_snapshot_calls), 0)
            deadline = time.monotonic() + 2
            while not second_gamma_status.wait(0.02) and time.monotonic() < deadline:
                service.poll_background_once()
            self.assertTrue(second_gamma_status.is_set(), "the next status cycle should not wait for snapshot transfers")
            self.assertEqual(self.tx.fetch_status_calls.count(alpha.id), 1)
            self.assertEqual(self.tx.fetch_status_calls.count(beta.id), 1)
        finally:
            release_transfers.set()

        deadline = time.monotonic() + 5
        while self.store.load_snapshot(gamma.id) is None and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertIsNotNone(self.store.load_snapshot(gamma.id))

    def test_queued_snapshot_is_skipped_after_another_path_observes_newer_scan(self):
        for scan_result in ("complete", "failed"):
            with self.subTest(scan_result=scan_result):
                server = make_server(scan_result)
                old, old_data = status_data(payload_for(server.id))
                new, new_data = status_data(payload_for(server.id, started=1719201000), scan_result)
                self.tx.status[server.id] = old
                self.tx.downloads[server.id] = old, old_data
                service = self.service([server])
                queued = threading.Event()
                release = threading.Event()
                finished = threading.Event()
                finish_deferred_pull = service._finish_deferred_pull

                def pause_queued_pull(target, status):
                    queued.set()
                    try:
                        release.wait(5)
                        finish_deferred_pull(target, status)
                    finally:
                        finished.set()

                service._finish_deferred_pull = pause_queued_pull
                service.poll_background_once()
                try:
                    self.assertTrue(queued.wait(2))
                    self.tx.status[server.id] = new
                    self.tx.downloads[server.id] = new, new_data
                    self.assertEqual(service.poll_server(server.id)["latest_scan_result"], scan_result)
                finally:
                    release.set()
                    self.assertTrue(finished.wait(5))

                transfers = [status for sid, status in self.tx.fetch_snapshot_calls if sid == server.id]
                result = self.store.load_snapshot(server.id)
                if scan_result == "complete":
                    self.assertEqual(transfers, [new])
                    self.assertEqual(result["scan_generation"], new["generation"].removesuffix(".json"))
                else:
                    self.assertEqual(transfers, [])
                    self.assertIsNone(result)
                    self.assertEqual(self.store.load_state(server.id)["latest_scan_result"], "failed")

    def test_contended_store_write_does_not_skip_other_hosts_in_poll_cycle(self):
        from collector import store

        alpha, beta = make_server(), make_server("beta-2")
        self.store.update_state(alpha.id, configuration_sync="unknown")
        for server in (alpha, beta):
            payload = payload_for(server.id, digest=server.scanner_digest)
            status, data = status_data(payload)
            self.tx.status[server.id] = status
            self.tx.downloads[server.id] = (status, data)
        svc = self.service([alpha, beta])

        with (self.store.root / alpha.id / "store.lock").open("rb") as lock:
            store.fcntl.flock(lock, store.fcntl.LOCK_EX | store.fcntl.LOCK_NB)
            results = svc.poll_once()

        self.assertEqual(results[alpha.id]["latest_pull_status"], "not_installed")
        self.assertEqual(results[beta.id]["latest_pull_status"], "succeeded")
        self.assertEqual(svc.poll_server(alpha.id)["latest_pull_status"], "succeeded")

    def test_new_complete_and_partial_snapshots_apply_and_failed_root_remains_visible(self):
        s = make_server(); self.seed(s)
        payload = payload_for(s.id, started=1719201000, digest=s.scanner_digest, partial=True)
        st, data = status_data(payload, "partial")
        self.tx.status[s.id] = st; self.tx.downloads[s.id] = (st, data)
        self.service([s]).poll_once()
        snap = self.store.load_snapshot(s.id)
        self.assertEqual(snap["scan_generation"], "alpha-1-1719201000-v1")
        self.assertEqual(self.store.load_state(s.id)["latest_scan_result"], "partial")
        self.assertEqual(snap["selected_roots"][1]["status"], "failed")
        self.assertEqual(snap["selected_roots"][1]["error_code"], "EIO")

    def test_remote_failed_and_total_failure_retain_previous_good_with_failed_scan_state(self):
        s = make_server(); self.seed(s)
        failed_payload = payload_for(s.id, started=1719201000, digest=s.scanner_digest)
        failed_status, _ = status_data(failed_payload, "complete")
        failed_status["status"] = "failed"
        self.tx.status[s.id] = failed_status
        self.service([s]).poll_once()
        self.assertEqual(self.store.load_snapshot(s.id)["scan_generation"], "alpha-1-1719200000-v1")
        state = self.store.load_state(s.id)
        self.assertEqual(state["latest_scan_result"], "failed")
        self.assertEqual(state["latest_pull_status"], "succeeded")
        payload = payload_for(s.id, started=1719202000, digest=s.scanner_digest, total_failed=True)
        st, data = status_data(payload, "failed")
        self.tx.status[s.id] = dict(st, status="partial")
        self.tx.downloads[s.id] = (dict(st, status="partial"), data)
        self.service([s]).poll_once()
        self.assertEqual(self.store.load_snapshot(s.id)["scan_generation"], "alpha-1-1719200000-v1")
        self.assertEqual(self.store.load_state(s.id)["latest_pull_status"], "invalid_snapshot")

    def test_unreachable_and_digest_race_retain_previous_good(self):
        from collector.transport import TransportError
        s = make_server(); self.seed(s)
        self.tx.status_errors[s.id] = TransportError("UNREACHABLE", "remote unreachable /secret")
        self.service([s]).poll_once()
        self.assertEqual(self.store.load_snapshot(s.id)["scan_generation"], "alpha-1-1719200000-v1")
        self.assertEqual(self.store.load_state(s.id)["latest_pull_status"], "unreachable")
        self.tx.status_errors = {}
        payload = payload_for(s.id, started=1719203000, digest=s.scanner_digest)
        st, data = status_data(payload)
        self.tx.status[s.id] = st
        self.tx.download_errors[s.id] = TransportError("RACE", "digest changed")
        self.service([s]).poll_once()
        self.assertEqual(self.store.load_snapshot(s.id)["scan_generation"], "alpha-1-1719200000-v1")
        self.assertEqual(self.store.load_state(s.id)["latest_pull_status"], "invalid_snapshot")

    def test_restart_recovery_uses_persisted_store_and_config_drift_independent(self):
        s = make_server(); self.seed(s)
        reloaded = CentralStore(pathlib.Path(self.tmp.name) / "state")
        drifted = make_server(digest="b" * 64)
        st, data = status_data(payload_for(s.id, started=1719200000, digest="a" * 64))
        self.tx.status[s.id] = st; self.tx.downloads[s.id] = (st, data)
        from collector.service import PollService
        svc = PollService([drifted], reloaded, self.tx, clock=self.clock)
        svc.poll_once()
        state = reloaded.load_state(s.id)
        self.assertEqual(state["latest_pull_status"], "succeeded")
        self.assertEqual(state["configuration_sync"], "drifted")
        self.assertEqual(len(self.tx.fetch_snapshot_calls), 1, "restart/cache miss must verify same generation once")
        svc.poll_once()
        self.assertEqual(len(self.tx.fetch_snapshot_calls), 1, "validated same tuple may skip later in-memory")

    def test_inventory_order_stable_disabled_skipped_and_default_interval_lightweight(self):
        from collector.service import PollService, DEFAULT_POLL_INTERVAL_SECONDS
        from collector.judge import LOCAL_SCAN_CADENCE_SECONDS
        a = make_server("alpha-1", order=20); b = make_server("beta-2", order=10); c = make_server("off-3", order=15, enabled=False)
        for s in (a, b):
            p = payload_for(s.id, digest=s.scanner_digest); st, data = status_data(p); self.tx.status[s.id] = st; self.tx.downloads[s.id] = (st, data)
        result = PollService([a, c, b], self.store, self.tx, clock=self.clock).poll_once()
        self.assertEqual(list(result), ["alpha-1", "beta-2"])
        self.assertEqual(self.tx.fetch_status_calls, ["alpha-1", "beta-2"])
        self.assertGreaterEqual(DEFAULT_POLL_INTERVAL_SECONDS, 600)
        self.assertLessEqual(DEFAULT_POLL_INTERVAL_SECONDS, 900)
        self.assertEqual(LOCAL_SCAN_CADENCE_SECONDS, 6 * 60 * 60)

    def test_manual_rescan_validates_id_runs_fixed_start_then_immediate_poll(self):
        s = make_server(); self.seed(s)
        payload = payload_for(s.id, started=1719205000, digest=s.scanner_digest)
        st, data = status_data(payload)
        self.tx.status[s.id] = st; self.tx.downloads[s.id] = (st, data)
        result = self.service([s]).manual_rescan("alpha-1")
        self.assertEqual(self.tx.rescans, ["alpha-1"])
        self.assertEqual(self.store.load_snapshot(s.id)["scan_generation"], "alpha-1-1719205000-v1")
        self.assertEqual(result["latest_pull_status"], "succeeded")
        with self.assertRaises(ValueError):
            self.service([s]).manual_rescan("../alpha-1")

    def test_invalid_snapshot_preserves_previous_complete_or_partial_scan_and_config_state(self):
        from collector.transport import TransportError
        for partial_prior, expected_scan in [(False, "complete"), (True, "partial")]:
            with self.subTest(partial_prior=partial_prior):
                self.setUp()
                s = make_server(); self.seed(s, partial=partial_prior)
                self.store.update_state(s.id, configuration_sync="in_sync")
                payload = payload_for(s.id, started=1719209000, digest=s.scanner_digest)
                st, data = status_data(payload)
                self.tx.status[s.id] = st
                self.tx.downloads[s.id] = (st, data[:-1] + b"x")
                self.service([s]).poll_once()
                state = self.store.load_state(s.id)
                self.assertEqual(state["latest_pull_status"], "invalid_snapshot")
                self.assertEqual(state["latest_scan_result"], expected_scan)
                self.assertEqual(state["configuration_sync"], "in_sync")
                self.assertEqual(self.store.load_snapshot(s.id)["scan_generation"], "alpha-1-1719200000-v1")

    def test_status_envelope_rejects_bad_fields_before_failed_or_unchanged_decisions(self):
        s = make_server(); old_status, _ = self.seed(s)
        bad_statuses = [
            dict(old_status, server_id="beta-2"),
            dict(old_status, status="queued"),
            dict(old_status, config_digest="nothex"),
            dict(old_status, scan_finished_unix=True),
            dict(old_status, generation="beta-2-1719200000-v1.json"),
        ]
        for st in bad_statuses:
            with self.subTest(st=st):
                self.tx.status[s.id] = st
                self.service([s]).poll_once()
                state = self.store.load_state(s.id)
                self.assertEqual(state["latest_pull_status"], "invalid_snapshot")
                self.assertEqual(state["latest_scan_result"], "complete")

    def test_same_generation_tuple_change_forces_fetch_not_blind_success(self):
        s = make_server(); old_status, _ = self.seed(s)
        changed = dict(old_status, sha256="b" * 64)
        self.tx.status[s.id] = changed
        self.tx.download_errors[s.id] = __import__("collector.transport").transport.TransportError("RACE", "digest changed")
        self.service([s]).poll_once()
        self.assertEqual(len(self.tx.fetch_snapshot_calls), 1)
        state = self.store.load_state(s.id)
        self.assertEqual(state["latest_pull_status"], "invalid_snapshot")
        self.assertEqual(state["latest_scan_result"], "complete")

    def test_status_transport_parse_errors_are_invalid_not_unreachable_and_preserve_scan_config(self):
        from collector.transport import TransportError
        for prior_partial, expected_scan in [(False, "complete"), (True, "partial")]:
            with self.subTest(prior_partial=prior_partial):
                self.setUp()
                s = make_server(); self.seed(s, partial=prior_partial)
                self.store.update_state(s.id, configuration_sync="in_sync")
                self.tx.status_errors[s.id] = TransportError("MALFORMED_STATUS", "bad json")
                self.service([s]).poll_once()
                state = self.store.load_state(s.id)
                self.assertEqual(state["latest_pull_status"], "invalid_snapshot")
                self.assertEqual(state["latest_scan_result"], expected_scan)
                self.assertEqual(state["configuration_sync"], "in_sync")

    def test_concurrent_poll_for_same_server_does_not_overlap_or_mutate_busy_state(self):
        s = make_server(); self.seed(s)
        payload = payload_for(s.id, started=1719208000, digest=s.scanner_digest)
        st, data = status_data(payload)
        self.tx.status[s.id] = st; self.tx.downloads[s.id] = (st, data)
        self.tx.block_fetch = threading.Event(); self.tx.entered_fetch = threading.Event()
        svc = self.service([s])
        first_result = {}
        t = threading.Thread(target=lambda: first_result.update(svc.poll_once()))
        t.start()
        self.assertTrue(self.tx.entered_fetch.wait(2))
        before = self.store.load_state(s.id)
        busy = svc.poll_server(s.id)
        self.assertEqual(busy, before)
        self.assertEqual(len(self.tx.fetch_snapshot_calls), 1)
        self.tx.block_fetch.set(); t.join(2)
        self.assertFalse(t.is_alive())
        self.assertEqual(self.tx.max_active, 1)
        self.assertEqual(self.store.load_snapshot(s.id)["scan_generation"], "alpha-1-1719208000-v1")


    def test_manual_rescan_waits_for_poll_lock_and_calls_fixed_rescan_once_before_success(self):
        s = make_server(); self.seed(s)
        payload = payload_for(s.id, started=1719208100, digest=s.scanner_digest)
        st, data = status_data(payload)
        self.tx.status[s.id] = st; self.tx.downloads[s.id] = (st, data)
        self.tx.block_fetch = threading.Event(); self.tx.entered_fetch = threading.Event()
        svc = self.service([s])
        t = threading.Thread(target=lambda: svc.poll_server(s.id))
        t.start()
        self.assertTrue(self.tx.entered_fetch.wait(2))
        result_box = {}
        manual = threading.Thread(target=lambda: result_box.update(svc.manual_rescan(s.id)))
        manual.start()
        time.sleep(0.05)
        self.assertEqual(self.tx.rescans, [], "rescan must wait rather than succeed from stale state")
        self.tx.block_fetch.set(); t.join(2); manual.join(2)
        self.assertFalse(manual.is_alive())
        self.assertEqual(self.tx.rescans, [s.id])
        self.assertEqual(result_box["latest_pull_status"], "succeeded")

    def test_api_helpers_preserve_inventory_order_and_unknown_server_boundary(self):
        a = make_server("alpha-1", order=20); b = make_server("beta-2", order=10)
        self.seed(a); self.seed(b)
        svc = self.service([a, b])
        summaries = svc.server_summaries()
        self.assertEqual([item["id"] for item in summaries], ["alpha-1", "beta-2"])
        self.assertEqual(summaries[0]["snapshot_availability"], "available")
        overview = summaries[0]["overview_snapshot"]
        self.assertEqual(set(overview), {"server_id", "selected_roots", "mounts", "users", "scan_generation", "scan_finished_unix", "scan_duration_sec", "estimate_duration_sec"})
        self.assertEqual(overview["server_id"], "alpha-1")
        self.assertEqual(set(overview["selected_roots"][0]), {
            "mount_id", "capacity_id", "major_minor", "mount_source", "fstype",
            "storage_media", "block_media", "storage_media_confidence", "block_media_confidence",
            "scan_finished_unix",
        })
        self.assertEqual(set(overview["mounts"][0]), {
            "mount_id", "path", "mountpoint", "df_total", "df_used", "df_avail", "df_use_pct",
            "storage_media", "block_media", "storage_media_confidence", "block_media_confidence",
        })
        self.assertNotIn("tree", overview["mounts"][0], "overview payload must omit the heavyweight directory tree")
        self.assertNotIn("top_files", overview, "overview payload must not include heavyweight detail data")
        self.assertEqual(svc.load_snapshot_for_api("alpha-1")["server_id"], "alpha-1")
        with self.assertRaises(ValueError):
            svc.load_state_for_api("../alpha-1")

    def test_server_summaries_include_only_lightweight_accepted_user_totals(self):
        server = make_server()
        payload = payload_for(server.id)
        payload["users"] = [
            {"uid": 1002, "name": "beta", "bytes": 20, "files": 1, "by_mount": {"/home": 20}},
            {"uid": 1001, "name": "alpha", "bytes": 10, "files": 1, "by_mount": {"/home": 10}},
        ]
        status, data = status_data(payload)
        self.tx.status[server.id] = status
        self.tx.downloads[server.id] = (status, data)
        service = self.service([server])
        service.poll_once()

        for _ in range(2):
            overview = service.server_summaries()[0]["overview_snapshot"]
            self.assertEqual(overview.get("users"), [
                {"uid": 1002, "name": "beta", "bytes": 20},
                {"uid": 1001, "name": "alpha", "bytes": 10},
            ])
        restarted = self.service([server])
        self.assertEqual(restarted.server_summaries()[0]["overview_snapshot"]["users"], overview["users"])
        self.assertEqual(self.store.load_snapshot(server.id)["users"], payload["users"])
        self.assertEqual(len(self.tx.fetch_snapshot_calls), 1)

    def test_server_summaries_keep_missing_snapshot_unavailable(self):
        summary = self.service([make_server()]).server_summaries()[0]

        self.assertEqual(summary["snapshot_availability"], "absent")
        self.assertIsNone(summary["overview_snapshot"])

    def test_overview_user_totals_exclude_only_explicit_remote_filesystems(self):
        from collector.service import _overview_snapshot
        from collector.snapshot import DesiredServer, validate_download

        payload = payload_for()
        shared_root = copy.deepcopy(payload["selected_roots"][0])
        shared_root.update(mount_id="shared", scan_root="/shared", mountpoint="/shared")
        shared_mount = copy.deepcopy(payload["mounts"][0])
        shared_mount.update(mount_id="shared", path="/shared", scan_root="/shared")
        payload["selected_roots"].append(shared_root)
        payload["mounts"].append(shared_mount)
        payload["users"] = [
            {"uid": 1001, "name": "alpha", "bytes": 30, "files": 2, "by_mount": {"/home": 10, "/shared": 20}},
            {"uid": 1002, "name": "beta", "bytes": 5, "files": 1, "by_mount": {"/shared": 5}},
        ]
        remote_types = (
            "nfs", "nfs4", "cifs", "smb3", "smbfs", "sshfs", "fuse.sshfs", "ceph",
            "fuse.ceph", "fuse.rclone", "fuse.davfs", "glusterfs", "lustre", "gpfs", "9p",
        )
        for fstype in (*remote_types, "NFS4", "ext4", "xfs", "unrecognized"):
            with self.subTest(fstype=fstype):
                shared_root["fstype"] = shared_mount["fstype"] = fstype
                status, data = status_data(payload)
                accepted = validate_download(status, data, DesiredServer("alpha-1", "a" * 64))

                overview = _overview_snapshot(accepted.payload)

                remote = fstype.lower() in remote_types
                self.assertEqual(overview.get("users"), [
                    {"uid": 1001, "name": "alpha", "bytes": 10 if remote else 30},
                    {"uid": 1002, "name": "beta", "bytes": 0 if remote else 5},
                ])
                self.assertEqual(accepted.payload["users"][0]["bytes"], 30)

    def test_retained_roots_age_independently_after_faster_scan(self):
        from collector.judge import freshness
        now = self.clock.now
        legacy = {"scan_finished_unix": now - 6 * 3600, "selected_roots": [{"storage_media": "ssd"}]}
        self.assertEqual(freshness(legacy, now, 900), "fresh")
        payload = {"scan_finished_unix": now, "selected_roots": [
            {"storage_media": "ssd", "scan_finished_unix": now - 4790},
            {"storage_media": "hdd", "scan_finished_unix": now - 4 * 3600},
        ]}
        self.assertEqual(freshness(payload, now, 900), "stale")
        payload["selected_roots"][1]["scan_finished_unix"] = now - 3 * 3600
        self.assertEqual(freshness(payload, now, 900), "fresh")
        payload["selected_roots"][0]["scan_finished_unix"] = now - 4861
        self.assertEqual(freshness(payload, now, 900), "stale")

    def test_overview_snapshot_omits_root_metadata_without_a_visible_mount(self):
        from collector.service import _overview_snapshot

        payload = payload_for()
        skipped_root = copy.deepcopy(payload["selected_roots"][0])
        skipped_root["mount_id"] = "virtual-skipped-root"
        payload["selected_roots"].append(skipped_root)

        overview = _overview_snapshot(payload)

        self.assertEqual(
            [root["mount_id"] for root in overview["selected_roots"]],
            ["home"],
            "overview should only include root metadata used by a displayed mount",
        )

    def test_server_summaries_reuse_overviews_warmed_at_service_start(self):
        server = make_server()
        self.seed(server)
        service = self.service([server])
        original_load_snapshot = self.store.load_snapshot
        repeated_loads = []

        def counted_load_snapshot(server_id):
            repeated_loads.append(server_id)
            return original_load_snapshot(server_id)

        self.store.load_snapshot = counted_load_snapshot

        self.assertEqual(service.server_summaries()[0]["mount_count"], 1)
        self.assertEqual(service.server_summaries()[0]["mount_count"], 1)
        self.assertEqual(repeated_loads, [], "overview requests must not reparse full snapshots")

    def test_successful_poll_refreshes_the_warmed_overview(self):
        server = make_server()
        self.seed(server)
        service = self.service([server])
        updated = payload_for(server.id, started=1719209000, digest=server.scanner_digest)
        updated["mounts"][0]["df_total"] = 200
        updated["users"] = [{"uid": 1001, "name": "alpha", "bytes": 30, "files": 2, "by_mount": {"/home": 30}}]
        status, data = status_data(updated)
        self.tx.status[server.id] = status
        self.tx.downloads[server.id] = (status, data)

        service.poll_once()
        self.store.load_snapshot = lambda _server_id: payload_for(server.id)

        overview = service.server_summaries()[0]["overview_snapshot"]
        self.assertEqual(overview["mounts"][0]["df_total"], 200)
        self.assertEqual(overview["users"], [{"uid": 1001, "name": "alpha", "bytes": 30}])

    def test_poll_validates_new_payload_once_and_reuses_metadata_for_unchanged_generation(self):
        from unittest.mock import patch
        from collector import snapshot

        server = make_server()
        service = self.service([server])
        status, data = status_data(payload_for(server.id))
        self.tx.status[server.id] = status
        self.tx.downloads[server.id] = (status, data)
        self.observe_host_clock(service, server)
        with (
            patch.object(snapshot, "validate_download", wraps=snapshot.validate_download) as validate,
            patch.object(self.store, "load_snapshot", wraps=self.store.load_snapshot) as read_tree,
        ):
            self.assertEqual(service.poll_server(server.id)["latest_pull_status"], "succeeded")
            self.clock.now += 24 * 3600
            service.poll_server(server.id)
            self.assertEqual(service.server_summaries()[0]["freshness"], "stale")
            self.assertEqual(len(self.tx.fetch_snapshot_calls), 1)
            self.assertEqual(validate.call_count, 1)
            self.assertEqual(read_tree.call_count, 0)

    def test_server_summaries_wait_for_poll_to_publish_snapshot_and_overview_together(self):
        server = make_server()
        self.seed(server)
        service = self.service([server])
        updated = payload_for(server.id, started=1719209100, digest=server.scanner_digest)
        updated["mounts"][0]["df_total"] = 200
        status, data = status_data(updated)
        self.tx.status[server.id] = status
        self.tx.downloads[server.id] = (status, data)
        original_commit_snapshot = self.store.commit_snapshot
        snapshot_committed = threading.Event()
        allow_cache_publish = threading.Event()

        def paused_commit_snapshot(*args, **kwargs):
            result = original_commit_snapshot(*args, **kwargs)
            snapshot_committed.set()
            allow_cache_publish.wait(2)
            return result

        self.store.commit_snapshot = paused_commit_snapshot
        poll = threading.Thread(target=service.poll_once)
        poll.start()
        self.assertTrue(snapshot_committed.wait(2))

        summary_result = {}
        summary_finished = threading.Event()

        def read_summary():
            summary_result.update(service.server_summaries()[0])
            summary_finished.set()

        summary = threading.Thread(target=read_summary)
        summary.start()
        summary_finished_early = summary_finished.wait(0.05)
        allow_cache_publish.set()
        poll.join(2)
        summary.join(2)

        self.assertFalse(
            summary_finished_early,
            "summary must not observe state between snapshot commit and overview publication",
        )
        self.assertFalse(poll.is_alive())
        self.assertFalse(summary.is_alive())
        self.assertEqual(summary_result["overview_snapshot"]["mounts"][0]["df_total"], 200)

    def test_server_summaries_do_not_wait_for_remote_snapshot_transfer(self):
        server = make_server()
        self.seed(server)
        service = self.service([server])
        updated = payload_for(server.id, started=1719209200, digest=server.scanner_digest)
        updated["mounts"][0]["df_total"] = 200
        status, data = status_data(updated)
        self.tx.status[server.id] = status
        self.tx.downloads[server.id] = (status, data)
        self.tx.block_fetch = threading.Event()
        self.tx.entered_fetch = threading.Event()

        poll = threading.Thread(target=service.poll_once)
        poll.start()
        self.assertTrue(self.tx.entered_fetch.wait(2))

        summary_result = {}
        summary_finished = threading.Event()

        def read_summary():
            summary_result.update(service.server_summaries()[0])
            summary_finished.set()

        summary = threading.Thread(target=read_summary)
        summary.start()
        summary_finished_during_transfer = summary_finished.wait(0.05)
        self.tx.block_fetch.set()
        poll.join(2)
        summary.join(2)

        self.assertTrue(
            summary_finished_during_transfer,
            "overview reads must remain available while a remote snapshot is downloading",
        )
        self.assertEqual(summary_result["overview_snapshot"]["mounts"][0]["df_total"], 100)

    def test_scheduler_waits_for_each_cycle_and_then_waits_remaining_interval(self):
        s = make_server(); self.seed(s)
        old_status = status_data(payload_for(s.id, started=1719200000, digest=s.scanner_digest))[0]
        self.tx.status[s.id] = old_status
        waits = []
        calls = {"stop": 0}
        svc = self.service([s])
        def stop():
            calls["stop"] += 1
            return calls["stop"] > 2
        def wait(seconds):
            waits.append(seconds)
            self.clock.now += seconds
        start = self.clock.now
        svc.run_forever(stop=stop, wait=wait)
        self.assertEqual(len(waits), 2)
        self.assertEqual(waits, [svc.poll_interval_seconds, svc.poll_interval_seconds])
        self.assertGreaterEqual(self.clock.now - start, 2 * svc.poll_interval_seconds)

    def test_zero_byte_status_is_invalid_snapshot_not_unreachable_and_preserves_prior_scan(self):
        s = make_server(); old_status, _ = self.seed(s, partial=True)
        self.tx.status[s.id] = dict(old_status, byte_size=0)
        self.service([s]).poll_once()
        state = self.store.load_state(s.id)
        self.assertEqual(state["latest_pull_status"], "invalid_snapshot")
        self.assertEqual(state["latest_scan_result"], "partial")

    def test_status_generation_prefix_collision_and_scan_started_equality(self):
        s = make_server("alpha")
        good_payload = payload_for("alpha", started=1719200000, digest=s.scanner_digest)
        good_status, good_data = status_data(good_payload)
        self.tx.status[s.id] = good_status; self.tx.downloads[s.id] = (good_status, good_data)
        self.service([s]).poll_once()
        self.assertEqual(self.store.load_state(s.id)["latest_pull_status"], "succeeded")

        collision = dict(good_status, generation="alpha-1-1719200000-v1.json", server_id="alpha")
        self.tx.status[s.id] = collision
        self.service([s]).poll_once()
        state = self.store.load_state(s.id)
        self.assertEqual(state["latest_pull_status"], "invalid_snapshot")
        self.assertEqual(state["latest_scan_result"], "complete")

        s2 = make_server("alpha-1")
        good_payload = payload_for("alpha-1", started=1719200000, digest=s2.scanner_digest)
        good_status, good_data = status_data(good_payload)
        self.tx.status[s2.id] = dict(good_status, scan_started_unix=1719200000)
        self.tx.downloads[s2.id] = (dict(good_status, scan_started_unix=1719200000), good_data)
        self.service([s2]).poll_once()
        self.assertEqual(self.store.load_state(s2.id)["latest_pull_status"], "succeeded")

        bad_started = dict(good_status, scan_started_unix=1719200001)
        self.tx.status[s2.id] = bad_started
        self.service([s2]).poll_once()
        self.assertEqual(self.store.load_state(s2.id)["latest_pull_status"], "invalid_snapshot")

    def test_freshness_is_derived_from_retained_snapshot_age_for_success_and_failures(self):
        from collector.transport import TransportError
        s = make_server(); old_status, _ = self.seed(s)
        self.clock.now = old_status["scan_finished_unix"] + 2 * 60 * 60
        self.tx.status_errors[s.id] = TransportError("UNREACHABLE", "down")
        service = self.service([s])
        self.observe_host_clock(service, s)
        service.poll_once()
        self.assertEqual(service.server_summaries()[0]["freshness"], "fresh")
        self.clock.now = old_status["scan_finished_unix"] + 8 * 60 * 60
        service.poll_once()
        self.assertEqual(service.server_summaries()[0]["freshness"], "stale")
        self.tx.status_errors = {}
        self.tx.status[s.id] = dict(old_status, generation="alpha-1-1719210000-v1.json", status="failed", scan_finished_unix=1719210042)
        service.poll_once()
        state = service.server_summaries()[0]
        self.assertEqual(state["latest_pull_status"], "succeeded")
        self.assertEqual(state["latest_scan_result"], "failed")
        self.assertEqual(state["freshness"], "stale")

    def test_manual_rescan_failure_maps_scan_and_pull_domains_independently(self):
        from collector.transport import TransportError
        s = make_server(); self.seed(s, partial=True)
        self.tx.download_errors[(s.id, "rescan")] = TransportError("RESCAN_FAILED", "systemctl failed")
        self.service([s]).manual_rescan(s.id)
        state = self.store.load_state(s.id)
        self.assertEqual(state["latest_scan_result"], "failed")
        self.assertEqual(state["latest_pull_status"], "succeeded")
        self.assertEqual(self.store.load_snapshot(s.id)["scan_generation"], "alpha-1-1719200000-v1")

        self.tx.download_errors[(s.id, "rescan")] = TransportError("TIMEOUT", "ssh timeout")
        self.store.update_state(s.id, latest_scan_result="partial", latest_pull_status="succeeded")
        self.service([s]).manual_rescan(s.id)
        state = self.store.load_state(s.id)
        self.assertEqual(state["latest_scan_result"], "partial")
        self.assertEqual(state["latest_pull_status"], "unreachable")


class ScanDurationHistoryTests(unittest.TestCase):
    @staticmethod
    def complete(finished, duration, plan="full", **extra):
        return {"state": "complete", "scan_plan": plan, "finished_unix": finished,
                "last_finished_unix": finished, "last_duration_sec": duration, **extra}

    def test_repeated_observations_of_one_completion_are_recorded_once(self):
        from collector.service import record_completed_duration
        history = record_completed_duration({}, self.complete(1000, 24))
        self.assertEqual(history, {"full": [[1000, 24]]})
        self.assertIsNone(record_completed_duration(history, self.complete(1000, 24)))
        legacy = {"state": "complete", "scan_plan": "full", "last_finished_unix": 1000, "last_duration_sec": 24}
        self.assertIsNone(record_completed_duration(history, legacy))
        self.assertEqual(record_completed_duration(history, self.complete(1000, 7, plan="ssd")),
                         {"full": [[1000, 24]], "ssd": [[1000, 7]]})

    def test_window_keeps_the_five_latest_completions_without_mutating_input(self):
        from collector.service import record_completed_duration
        history = {}
        for finished, duration in ((1000, 900), (2000, 24), (3000, 30), (4000, 40), (5000, 50)):
            history = record_completed_duration(history, self.complete(finished, duration))
        before = copy.deepcopy(history)
        updated = record_completed_duration(history, self.complete(6000, 60))
        self.assertEqual(updated["full"], [[2000, 24], [3000, 30], [4000, 40], [5000, 50], [6000, 60]])
        self.assertEqual(history, before)
        self.assertIsNone(record_completed_duration(updated, self.complete(1500, 15)),
                          "a completion older than the whole window changes nothing and is not rewritten")
        late = record_completed_duration(updated, self.complete(2500, 25))
        self.assertEqual(late["full"], [[2500, 25], [3000, 30], [4000, 40], [5000, 50], [6000, 60]])

    def test_only_successful_plan_scoped_completions_are_recorded(self):
        from collector.service import record_completed_duration
        ignored = [
            dict(self.complete(1000, 24), state=state) for state in ("running", "partial", "failed", "idle", "unknown")
        ] + [
            self.complete(1000, 24, plan=None),
            self.complete(1000, -1),
            self.complete(1000, True),
            self.complete(1000, 24.5),
            {"state": "complete", "scan_plan": "full", "last_duration_sec": 24},
        ]
        for status in ignored:
            with self.subTest(status=status):
                self.assertIsNone(record_completed_duration({}, status))

    def test_estimate_range_is_typical_and_longest_duration(self):
        from collector.judge import eta
        history = {"full": [[1000, 1396], [2000, 19], [3000, 21]], "ssd": [[1500, 18]]}
        self.assertEqual(eta(history, "full", 19), (19, 1396), "one cold run must not become the typical estimate")
        self.assertEqual(eta(history, "full", 1500), (21, 1500))
        self.assertEqual(eta(history, "ssd", None), (18, 18))
        self.assertEqual(eta({}, "full", 24), (24, 24))
        self.assertEqual(eta({}, "full", None), (None, None))
        self.assertEqual(eta({}, "full", True), (None, None))


if __name__ == "__main__":
    unittest.main()
