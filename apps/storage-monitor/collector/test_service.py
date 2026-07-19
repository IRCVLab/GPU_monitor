import copy
import json
import pathlib
import tempfile
import threading
import time
import unittest

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


class FakeTransport:
    def __init__(self):
        self.status = {}
        self.downloads = {}
        self.status_errors = {}
        self.download_errors = {}
        self.rescans = []
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

    def seed(self, server, started=1719200000, **kwargs):
        payload = payload_for(server.id, started=started, digest=server.scanner_digest, **kwargs)
        st, data = status_data(payload)
        self.assertTrue(self.store.apply_download(server.id, st, data, __import__("collector.snapshot").snapshot.DesiredServer(server.id, server.scanner_digest)).accepted)
        return st, data

    def test_unchanged_generation_skips_snapshot_transfer_and_marks_fresh(self):
        s = make_server(); old_status, old_data = self.seed(s)
        self.tx.status[s.id] = old_status; self.tx.downloads[s.id] = (old_status, old_data)
        svc = self.service([s])
        result = svc.poll_once()
        self.assertEqual(len(self.tx.fetch_snapshot_calls), 1)
        result = svc.poll_once()
        self.assertEqual(len(self.tx.fetch_snapshot_calls), 1)
        self.assertEqual(result[s.id]["latest_pull_status"], "succeeded")
        state = self.store.load_state(s.id)
        self.assertEqual(state["freshness"], "fresh")
        self.assertEqual(state["snapshot_availability"], "available")

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
        from collector.service import PollService, DEFAULT_POLL_INTERVAL_SECONDS, LOCAL_SCAN_CADENCE_SECONDS
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
        self.assertEqual(svc.load_snapshot_for_api("alpha-1")["server_id"], "alpha-1")
        with self.assertRaises(ValueError):
            svc.load_state_for_api("../alpha-1")

    def test_scheduler_runs_sequential_polls_and_waits_remaining_interval(self):
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
        self.service([s]).poll_once()
        self.assertEqual(self.store.load_state(s.id)["freshness"], "fresh")
        self.clock.now = old_status["scan_finished_unix"] + 8 * 60 * 60
        self.service([s]).poll_once()
        self.assertEqual(self.store.load_state(s.id)["freshness"], "stale")
        self.tx.status_errors = {}
        self.tx.status[s.id] = dict(old_status, generation="alpha-1-1719210000-v1.json", status="failed", scan_finished_unix=1719210042)
        self.service([s]).poll_once()
        state = self.store.load_state(s.id)
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


if __name__ == "__main__":
    unittest.main()
