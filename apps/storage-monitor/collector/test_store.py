import json
import pathlib
import tempfile
import unittest
from unittest import mock

from collector import snapshot, store
from collector.test_snapshot import base_payload, status_for


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name) / "state"
        self.store = store.CentralStore(self.root)
        self.desired = snapshot.DesiredServer(server_id="alpha-1", config_digest="a" * 64)

    def valid_download(self, payload=None):
        payload = payload or base_payload()
        status, data = status_for(payload)
        return status, data

    def test_store_reloads_snapshot_and_state_after_restart(self):
        status, data = self.valid_download()
        result = self.store.apply_download("alpha-1", status, data, self.desired)
        self.assertTrue(result.accepted)
        self.store.update_state("alpha-1", freshness="fresh", latest_pull_status="succeeded", active_job={"id": "job-1", "server_id": "alpha-1", "kind": "rescan", "state": "running", "actor": "operator-1", "requested_unix": 1719200000, "started_unix": 1719200001, "finished_unix": None, "result_code": None})
        reloaded = store.CentralStore(self.root)
        self.assertEqual(reloaded.load_snapshot("alpha-1")["scan_generation"], "alpha-1-1719200000-v1")
        state = reloaded.load_state("alpha-1")
        self.assertEqual(state["snapshot_availability"], "available")
        self.assertEqual(state["freshness"], "fresh")
        self.assertEqual(state["latest_pull_status"], "succeeded")
        self.assertEqual(state["active_job"]["kind"], "rescan")

    def test_total_failure_does_not_replace_previous_good_snapshot(self):
        status, data = self.valid_download()
        self.assertTrue(self.store.apply_download("alpha-1", status, data, self.desired).accepted)
        failed_payload = base_payload(generation="alpha-1-1719200100-v1")
        failed_payload["scan_started_unix"] = 1719200100
        failed_payload["scan_finished_unix"] = 1719200142
        failed_payload["selected_roots"][0]["status"] = "failed"
        failed_payload["selected_roots"][0]["scanned_bytes"] = 0
        failed_payload["selected_roots"][0]["scanned_files"] = 0
        failed_payload["selected_roots"][0]["scanned_dirs"] = 0
        failed_payload["selected_roots"][0]["error_count"] = 1
        failed_payload["selected_roots"][0]["error_code"] = "EIO"
        failed_payload["mounts"] = []
        bad_status, bad_data = self.valid_download(failed_payload)
        result = self.store.apply_download("alpha-1", bad_status, bad_data, self.desired)
        self.assertFalse(result.accepted)
        self.assertEqual(self.store.load_snapshot("alpha-1")["scan_generation"], "alpha-1-1719200000-v1")
        state = self.store.load_state("alpha-1")
        self.assertEqual(state["snapshot_availability"], "available")
        self.assertEqual(state["latest_pull_status"], "invalid_snapshot")
        self.assertEqual(state["latest_scan_result"], "failed")

    def test_write_failure_keeps_previous_good_and_coherent_state(self):
        status, data = self.valid_download()
        self.assertTrue(self.store.apply_download("alpha-1", status, data, self.desired).accepted)
        next_payload = base_payload(generation="alpha-1-1719200100-v1")
        next_payload["scan_started_unix"] = 1719200100
        next_payload["scan_finished_unix"] = 1719200142
        next_status, next_data = self.valid_download(next_payload)
        with mock.patch("collector.store.os.replace", side_effect=OSError("/very/secret/path traceback details")):
            result = self.store.apply_download("alpha-1", next_status, next_data, self.desired)
        self.assertFalse(result.accepted)
        self.assertEqual(self.store.load_snapshot("alpha-1")["scan_generation"], "alpha-1-1719200000-v1")
        state = self.store.load_state("alpha-1")
        self.assertEqual(state["latest_pull_status"], "succeeded")
        self.assertNotIn("/very/secret", json.dumps(state))

    def test_rejects_unsafe_state_root_server_id_symlink_and_bad_enums(self):
        with self.assertRaisesRegex(ValueError, "absolute"):
            store.CentralStore("relative")
        with self.assertRaisesRegex(ValueError, "server_id"):
            self.store.load_state("../evil")
        self.root.mkdir(parents=True, exist_ok=True)
        symlink = self.root / "evil"
        symlink.symlink_to(self.root)
        with self.assertRaisesRegex(ValueError, "symlink"):
            self.store.update_state("evil", freshness="fresh")
        with self.assertRaisesRegex(ValueError, "freshness"):
            self.store.update_state("alpha-1", freshness="yesterday")
        with self.assertRaisesRegex(ValueError, "active_job"):
            self.store.update_state("alpha-1", active_job={"id": "job-1", "server_id": "alpha-1", "kind": "rescan", "state": "running", "actor": "operator-1", "requested_unix": True, "started_unix": 2, "finished_unix": None, "result_code": None})


if __name__ == "__main__":
    unittest.main()

class StoreReviewGapTests(unittest.TestCase):
    DIGEST = "a" * 64

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name) / "state"
        self.store = store.CentralStore(self.root)
        self.desired = snapshot.DesiredServer(server_id="alpha-1", config_digest=self.DIGEST)

    def valid_download(self, generation="alpha-1-1719200000-v1"):
        payload = base_payload(generation=generation)
        payload["config_digest"] = self.DIGEST
        status, data = status_for(payload, config_digest=self.DIGEST)
        status["status"] = "complete"
        return status, data

    def test_exact_state_enums_and_active_job_contract(self):
        self.assertEqual(self.store.load_state("alpha-1")["snapshot_availability"], "absent")
        self.store.update_state("alpha-1", snapshot_availability="available", freshness="fresh", latest_pull_status="succeeded", latest_scan_result="complete", configuration_sync="in_sync", active_job={"id": "job-1", "server_id": "alpha-1", "kind": "rescan", "state": "running", "actor": "operator-1", "requested_unix": 1719200000, "started_unix": 1719200001, "finished_unix": None, "result_code": None})
        state = self.store.load_state("alpha-1")
        self.assertEqual(state["latest_pull_status"], "succeeded")
        with self.assertRaisesRegex(ValueError, "latest_pull_status"):
            self.store.update_state("alpha-1", latest_pull_status="success")
        with self.assertRaisesRegex(ValueError, "configuration_sync"):
            self.store.update_state("alpha-1", configuration_sync="drift")
        with self.assertRaisesRegex(ValueError, "active_job"):
            self.store.update_state("alpha-1", active_job={"id": "../bad", "server_id": "alpha-1", "kind": "rescan", "state": "running", "actor": "operator-1", "requested_unix": 1, "started_unix": 2, "finished_unix": None, "result_code": None})


    def test_state_write_failure_keeps_previous_good_pair_current(self):
        status, data = self.valid_download()
        self.assertTrue(self.store.apply_download("alpha-1", status, data, self.desired).accepted)
        next_status, next_data = self.valid_download("alpha-1-1719200100-v1")
        next_payload = json.loads(next_data.decode())
        next_payload["scan_started_unix"] = 1719200100
        next_payload["scan_finished_unix"] = 1719200142
        next_data = json.dumps(next_payload, sort_keys=True, separators=(",", ":")).encode()
        next_status.update({"byte_size": len(next_data), "sha256": __import__("hashlib").sha256(next_data).hexdigest(), "scan_finished_unix": 1719200142})

        def fail_state_write(path, data):
            if path.name.startswith("state-"):
                raise OSError("state write failed")
            return original_write(path, data)
        original_write = store._write_json_atomic
        with mock.patch("collector.store._write_json_atomic", side_effect=fail_state_write):
            result = self.store.apply_download("alpha-1", next_status, next_data, self.desired)
        self.assertFalse(result.accepted)
        self.assertEqual(self.store.load_snapshot("alpha-1")["scan_generation"], "alpha-1-1719200000-v1")
        self.assertEqual(self.store.load_state("alpha-1")["latest_pull_status"], "succeeded")


    def test_active_job_v1_contract_rejects_arbitrary_kind_and_malformed_fields(self):
        valid = {
            "id": "job-1",
            "server_id": "alpha-1",
            "kind": "rescan",
            "state": "running",
            "actor": "operator-1",
            "requested_unix": 1719200000,
            "started_unix": 1719200001,
            "finished_unix": None,
            "result_code": None,
        }
        self.store.update_state("alpha-1", active_job=valid)
        cases = []
        bad = dict(valid); bad["kind"] = "fetch"; cases.append((bad, "kind"))
        bad = dict(valid); bad["kind"] = "config_sync"; cases.append((bad, "kind"))
        bad = dict(valid); bad["state"] = "queued"; cases.append((bad, "state"))
        bad = dict(valid); bad["id"] = "../evil"; cases.append((bad, "id"))
        bad = dict(valid); bad["server_id"] = "beta-2"; cases.append((bad, "server_id"))
        bad = dict(valid); bad["actor"] = "bad;actor"; cases.append((bad, "actor"))
        bad = dict(valid); bad["requested_unix"] = True; cases.append((bad, "timestamp"))
        bad = dict(valid); bad["started_unix"] = 1719199999; cases.append((bad, "started"))
        bad = dict(valid); bad["finished_unix"] = 1719200000; cases.append((bad, "finished"))
        bad = dict(valid); bad["result_code"] = "ERR;DROP"; cases.append((bad, "result_code"))
        bad = dict(valid); bad["extra"] = "nope"; cases.append((bad, "unknown"))
        for job, message in cases:
            with self.subTest(message=message, job=job), self.assertRaisesRegex(ValueError, message):
                self.store.update_state("alpha-1", active_job=job)

    def test_active_job_terminal_result_code_rules(self):
        base = {
            "id": "job-1",
            "server_id": "alpha-1",
            "kind": "rescan",
            "actor": "operator-1",
            "requested_unix": 1719200000,
            "started_unix": 1719200001,
            "finished_unix": 1719200042,
        }
        succeeded = dict(base, state="succeeded", result_code="OK")
        self.store.update_state("alpha-1", active_job=succeeded)
        failed = dict(base, state="failed", result_code="EIO")
        self.store.update_state("alpha-1", active_job=failed)
        running_done = dict(base, state="running", result_code=None)
        with self.assertRaisesRegex(ValueError, "finished"):
            self.store.update_state("alpha-1", active_job=running_done)
        failed_no_code = dict(base, state="failed", result_code=None)
        with self.assertRaisesRegex(ValueError, "result_code"):
            self.store.update_state("alpha-1", active_job=failed_no_code)
        requested_with_start = dict(base, state="requested", started_unix=1719200001, finished_unix=None, result_code=None)
        with self.assertRaisesRegex(ValueError, "started"):
            self.store.update_state("alpha-1", active_job=requested_with_start)

    def test_rejects_symlinked_final_snapshot_and_state_files(self):
        status, data = self.valid_download()
        self.assertTrue(self.store.apply_download("alpha-1", status, data, self.desired).accepted)
        server_dir = self.root / "alpha-1"
        current = json.loads((server_dir / "current.json").read_text())
        (server_dir / current["snapshot"]).unlink()
        (server_dir / current["snapshot"]).symlink_to("/tmp/not-storage-viz-snapshot")
        with self.assertRaisesRegex(ValueError, "symlink"):
            self.store.load_snapshot("alpha-1")
        (server_dir / current["state"]).unlink()
        (server_dir / current["state"]).symlink_to("/tmp/not-storage-viz-state")
        with self.assertRaisesRegex(ValueError, "symlink"):
            self.store.load_state("alpha-1")

    def test_atomic_failures_keep_previous_good_and_no_temp_leakage(self):
        status, data = self.valid_download()
        self.assertTrue(self.store.apply_download("alpha-1", status, data, self.desired).accepted)
        next_status, next_data = self.valid_download("alpha-1-1719200100-v1")
        next_payload = json.loads(next_data.decode())
        next_payload["scan_started_unix"] = 1719200100
        next_payload["scan_finished_unix"] = 1719200142
        next_data = json.dumps(next_payload, sort_keys=True, separators=(",", ":")).encode()
        next_status.update({"byte_size": len(next_data), "sha256": __import__("hashlib").sha256(next_data).hexdigest(), "scan_finished_unix": 1719200142})
        for target in ("mkstemp", "replace", "fsync"):
            with self.subTest(target=target):
                patch_target = f"collector.store.tempfile.mkstemp" if target == "mkstemp" else f"collector.store.os.{target}"
                with mock.patch(patch_target, side_effect=OSError("/secret/path failed")):
                    result = self.store.apply_download("alpha-1", next_status, next_data, self.desired)
                self.assertFalse(result.accepted)
                self.assertEqual(self.store.load_snapshot("alpha-1")["scan_generation"], "alpha-1-1719200000-v1")
                state = self.store.load_state("alpha-1")
                self.assertIn(state["latest_pull_status"], {"succeeded", "invalid_snapshot"})
                self.assertFalse(list((self.root / "alpha-1").glob("*.tmp")))

class StoreQualityReviewTests(unittest.TestCase):
    DIGEST = "a" * 64

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name) / "state"
        self.store = store.CentralStore(self.root)
        self.desired = snapshot.DesiredServer(server_id="alpha-1", config_digest=self.DIGEST)

    def valid_download(self, generation="alpha-1-1719200000-v1"):
        payload = base_payload(generation=generation)
        payload["config_digest"] = self.DIGEST
        status, data = status_for(payload, config_digest=self.DIGEST)
        status["status"] = "complete"
        return status, data

    def test_store_uses_manifest_pointer_and_rejects_corrupt_or_symlink_references(self):
        status, data = self.valid_download()
        self.assertTrue(self.store.apply_download("alpha-1", status, data, self.desired).accepted)
        server_dir = self.root / "alpha-1"
        self.assertTrue((server_dir / "current.json").exists())
        self.assertEqual(oct(server_dir.stat().st_mode & 0o777), "0o700")
        for path in server_dir.glob("*.json"):
            self.assertEqual(oct(path.stat().st_mode & 0o777), "0o600")
        current = json.loads((server_dir / "current.json").read_text())
        self.assertNotEqual(current["snapshot"], "snapshot.json")
        self.assertEqual(self.store.load_snapshot("alpha-1")["scan_generation"], "alpha-1-1719200000-v1")
        (server_dir / current["snapshot"]).unlink()
        (server_dir / current["snapshot"]).symlink_to("/tmp/evil")
        with self.assertRaisesRegex(ValueError, "symlink"):
            self.store.load_snapshot("alpha-1")
        (server_dir / "current.json").write_text(json.dumps({"snapshot":"../evil","state":current["state"]}))
        with self.assertRaisesRegex(ValueError, "STORE_INCOHERENT|manifest"):
            self.store.load_state("alpha-1")


    def test_manifest_rejects_swapped_snapshot_state_basenames_and_arbitrary_names(self):
        status, data = self.valid_download()
        self.assertTrue(self.store.apply_download("alpha-1", status, data, self.desired).accepted)
        server_dir = self.root / "alpha-1"
        current_path = server_dir / "current.json"
        current = json.loads(current_path.read_text())
        # Valid pair still loads.
        self.assertTrue(current["snapshot"].startswith("snapshot-"))
        self.assertTrue(current["state"].startswith("state-"))
        self.assertEqual(self.store.load_snapshot("alpha-1")["server_id"], "alpha-1")
        self.assertEqual(self.store.load_state("alpha-1")["snapshot_availability"], "available")
        swapped = {"snapshot": current["state"], "state": current["snapshot"]}
        current_path.write_text(json.dumps(swapped))
        with self.assertRaisesRegex(ValueError, "snapshot"):
            self.store.load_snapshot("alpha-1")
        current_path.write_text(json.dumps({"snapshot": "state-arbitrary.json", "state": current["state"]}))
        (server_dir / "state-arbitrary.json").write_text("{}")
        with self.assertRaisesRegex(ValueError, "snapshot"):
            self.store.load_snapshot("alpha-1")
        current_path.write_text(json.dumps({"snapshot": current["snapshot"], "state": "snapshot-arbitrary.json"}))
        (server_dir / "snapshot-arbitrary.json").write_text("{}")
        with self.assertRaisesRegex(ValueError, "state"):
            self.store.load_state("alpha-1")
        current_path.write_text(json.dumps({"snapshot": "../snapshot-evil.json", "state": current["state"]}))
        with self.assertRaisesRegex(ValueError, "snapshot"):
            self.store.load_snapshot("alpha-1")


    def test_post_replace_current_manifest_fsync_failure_reports_committed_visible_success(self):
        status, data = self.valid_download()
        self.assertTrue(self.store.apply_download("alpha-1", status, data, self.desired).accepted)
        next_status, next_data = self.valid_download("alpha-1-1719200100-v1")
        p = json.loads(next_data.decode())
        p["scan_started_unix"] = 1719200100
        p["scan_finished_unix"] = 1719200142
        next_data = json.dumps(p, sort_keys=True, separators=(",", ":")).encode()
        next_status.update({"byte_size": len(next_data), "sha256": __import__("hashlib").sha256(next_data).hexdigest(), "scan_finished_unix": 1719200142})

        original_write = store._write_json_atomic
        def fail_current_after_replace(path, data):
            original_write(path, data)
            if pathlib.Path(path).name == "current.json":
                raise store.AtomicWriteDurabilityUncertain("post replace current manifest fsync failed")
        with mock.patch("collector.store._write_json_atomic", side_effect=fail_current_after_replace):
            result = self.store.apply_download("alpha-1", next_status, next_data, self.desired)
        self.assertTrue(result.accepted)
        self.assertEqual(result.error_code, "DURABILITY_UNCERTAIN")
        self.assertEqual(self.store.load_snapshot("alpha-1")["scan_generation"], "alpha-1-1719200100-v1")
        self.assertEqual(self.store.load_state("alpha-1")["latest_pull_status"], "succeeded")

        with mock.patch("collector.store._write_json_atomic", side_effect=fail_current_after_replace):
            state = self.store.update_state("alpha-1", freshness="fresh")
        self.assertEqual(state["freshness"], "fresh")
        self.assertEqual(self.store.load_state("alpha-1")["freshness"], "fresh")

    def test_pre_replace_current_manifest_failure_still_reports_write_error_and_keeps_old_pair(self):
        status, data = self.valid_download()
        self.assertTrue(self.store.apply_download("alpha-1", status, data, self.desired).accepted)
        next_status, next_data = self.valid_download("alpha-1-1719200100-v1")
        p = json.loads(next_data.decode())
        p["scan_started_unix"] = 1719200100
        p["scan_finished_unix"] = 1719200142
        next_data = json.dumps(p, sort_keys=True, separators=(",", ":")).encode()
        next_status.update({"byte_size": len(next_data), "sha256": __import__("hashlib").sha256(next_data).hexdigest(), "scan_finished_unix": 1719200142})
        original_replace = store.os.replace
        def fail_current_replace(src, dst):
            if str(dst).endswith("current.json"):
                raise OSError("pre replace current manifest failed")
            return original_replace(src, dst)
        with mock.patch("collector.store.os.replace", side_effect=fail_current_replace):
            result = self.store.apply_download("alpha-1", next_status, next_data, self.desired)
        self.assertFalse(result.accepted)
        self.assertEqual(result.error_code, "WRITE_ERROR")
        self.assertEqual(self.store.load_snapshot("alpha-1")["scan_generation"], "alpha-1-1719200000-v1")

    def test_manifest_commit_failure_keeps_previous_pair_current_and_no_temp_leakage(self):
        status, data = self.valid_download()
        self.assertTrue(self.store.apply_download("alpha-1", status, data, self.desired).accepted)
        next_status, next_data = self.valid_download("alpha-1-1719200100-v1")
        p = json.loads(next_data.decode()); p["scan_started_unix"] = 1719200100; p["scan_finished_unix"] = 1719200142
        next_data = json.dumps(p, sort_keys=True, separators=(",", ":")).encode()
        next_status.update({"byte_size": len(next_data), "sha256": __import__("hashlib").sha256(next_data).hexdigest(), "scan_finished_unix": 1719200142})
        real_replace = store.os.replace
        def fail_current(src, dst):
            if str(dst).endswith("current.json"):
                raise OSError("manifest replace failed")
            return real_replace(src, dst)
        with mock.patch("collector.store.os.replace", side_effect=fail_current):
            result = self.store.apply_download("alpha-1", next_status, next_data, self.desired)
        self.assertFalse(result.accepted)
        self.assertEqual(self.store.load_snapshot("alpha-1")["scan_generation"], "alpha-1-1719200000-v1")
        self.assertFalse(list((self.root / "alpha-1").glob("*.tmp")))

    def test_store_lock_serializes_writers_and_scan_result_ignores_skipped(self):
        status, data = self.valid_download()
        self.assertTrue(self.store.apply_download("alpha-1", status, data, self.desired).accepted)
        lock_path = self.root / "alpha-1" / "store.lock"
        fd = store.os.open(lock_path, store.os.O_RDWR)
        try:
            store.fcntl.flock(fd, store.fcntl.LOCK_EX | store.fcntl.LOCK_NB)
            with self.assertRaisesRegex(BlockingIOError, "locked"):
                self.store.update_state("alpha-1", freshness="fresh")
        finally:
            store.fcntl.flock(fd, store.fcntl.LOCK_UN)
            store.os.close(fd)
        payload = base_payload(); payload["selected_roots"].append({"mount_id":"skip","major_minor":"8:2","mount_source":"/dev/storage-viz/s","mount_root":"/","mountpoint":"/skip","scan_root":"/skip","fstype":"xfs","status":"skipped","scanned_bytes":0,"scanned_files":0,"scanned_dirs":0,"blocked_count":0,"error_count":0,"error_code":"POLICY"})
        self.assertEqual(store._scan_result(payload), "complete")

    def test_state_error_fields_are_consistent(self):
        with self.assertRaisesRegex(ValueError, "last_error"):
            self.store.update_state("alpha-1", last_error_code="EIO", last_error_message=None)
        with self.assertRaisesRegex(ValueError, "last_error"):
            self.store.update_state("alpha-1", last_error_code=None, last_error_message="bad", last_error_unix=1)
        with self.assertRaisesRegex(ValueError, "last_error_unix"):
            self.store.update_state("alpha-1", last_error_code="EIO", last_error_message="bad", last_error_unix=True)
