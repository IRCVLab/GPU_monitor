import copy
import hashlib
import json
import unittest

from collector import snapshot


def base_payload(server_id="alpha-1", generation="alpha-1-1719200000-v1"):
    return {
        "schema_version": 1,
        "hostname": "alpha.example.test",
        "server_id": server_id,
        "scanner_version": "test",
        "scan_started_unix": 1719200000,
        "scan_finished_unix": 1719200042,
        "scan_duration_sec": 42,
        "scan_generation": generation,
        "run_as_root": False,
        "selected_roots": [
            {
                "mount_id": "home",
                "major_minor": "8:1",
                "mount_source": "/dev/storage-viz/home",
                "mount_root": "/",
                "mountpoint": "/",
                "scan_root": "/home",
                "fstype": "ext4",
                "status": "complete",
                "scanned_bytes": 30,
                "scanned_files": 2,
                "scanned_dirs": 1,
                "blocked_count": 0,
                "error_count": 0,
                "error_code": None,
            }
        ],
        "mounts": [
            {
                "path": "/home",
                "mount_id": "home",
                "scan_root": "/home",
                "fstype": "ext4",
                "df_total": 100,
                "df_used": 70,
                "df_avail": 30,
                "df_use_pct": 70,
                "scanned_bytes": 30,
                "scanned_files": 2,
                "scanned_dirs": 1,
                "errors": 0,
                "tree": {
                    "name": "/home",
                    "kind": "directory",
                    "bytes": 30,
                    "files": 2,
                    "uid": 0,
                    "mtime": 1719200042,
                    "other_bytes": 10,
                    "children": [
                        {"name": "a", "kind": "file", "bytes": 20, "files": 1, "uid": 1000, "mtime": 1719200000}
                    ],
                },
            }
        ],
        "users": [],
        "top_files": [],
        "stale": [],
        "blocked": [],
        "config_digest": "a" * 64,
    }


def status_for(payload, config_digest="a" * 64):
    generation = payload["scan_generation"] + ".json"
    data = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {
        "generation": generation,
        "byte_size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "scan_finished_unix": payload["scan_finished_unix"],
        "server_id": payload["server_id"],
        "config_digest": config_digest,
        "status": "complete",
    }, data


class SnapshotValidationTests(unittest.TestCase):
    def desired(self, digest="a" * 64):
        return snapshot.DesiredServer(server_id="alpha-1", config_digest=digest)

    def validate(self, payload=None, *, status=None, desired=None):
        payload = base_payload() if payload is None else payload
        if status is None:
            status, data = status_for(payload)
        else:
            data = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return snapshot.validate_download(status, data, desired or self.desired())

    def test_validates_exact_generation_size_sha_and_identity(self):
        result = self.validate()
        self.assertEqual(result.payload["server_id"], "alpha-1")
        self.assertEqual(result.generation, "alpha-1-1719200000-v1.json")
        bad_status, data = status_for(base_payload())
        bad_status["sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "sha256"):
            snapshot.validate_download(bad_status, data, self.desired())
        bad_status, data = status_for(base_payload())
        bad_status["byte_size"] += 1
        with self.assertRaisesRegex(ValueError, "byte_size"):
            snapshot.validate_download(bad_status, data, self.desired())
        bad_status, data = status_for(base_payload())
        bad_status["generation"] = "../evil.json"
        with self.assertRaisesRegex(ValueError, "generation"):
            snapshot.validate_download(bad_status, data, self.desired())

    def test_config_drift_is_independent_state_not_measurement_corruption(self):
        result = self.validate(desired=self.desired("b" * 64))
        self.assertEqual(result.config_sync, "drifted")
        self.assertEqual(result.payload["scan_generation"], "alpha-1-1719200000-v1")

    def test_partial_snapshot_is_valid_with_completed_root(self):
        payload = base_payload()
        payload["selected_roots"].append({
            "mount_id": "archive",
            "major_minor": "8:2",
            "mount_source": "/dev/storage-viz/archive",
            "mount_root": "/",
            "mountpoint": "/archive",
            "scan_root": "/archive",
            "fstype": "xfs",
            "status": "failed",
            "scanned_bytes": 0,
            "scanned_files": 0,
            "scanned_dirs": 0,
            "blocked_count": 0,
            "error_count": 1,
            "error_code": "EIO",
        })
        status, data = status_for(payload)
        status["status"] = "partial"
        result = snapshot.validate_download(status, data, self.desired())
        self.assertEqual(result.snapshot_availability, "available")

    def test_total_failure_without_completed_root_is_invalid(self):
        payload = base_payload()
        payload["selected_roots"][0]["status"] = "failed"
        payload["selected_roots"][0]["scanned_bytes"] = 0
        payload["selected_roots"][0]["scanned_files"] = 0
        payload["selected_roots"][0]["scanned_dirs"] = 0
        payload["selected_roots"][0]["error_count"] = 1
        payload["selected_roots"][0]["error_code"] = "EIO"
        payload["mounts"] = []
        status, data = status_for(payload)
        with self.assertRaisesRegex(ValueError, "completed root"):
            snapshot.validate_download(status, data, self.desired())

    def test_rejects_unknown_major_bool_timestamps_duplicate_roots_bad_tree(self):
        cases = []
        p = base_payload(); p["schema_version"] = 2; cases.append((p, "schema"))
        p = base_payload(); p["run_as_root"] = 1; cases.append((p, "run_as_root"))
        p = base_payload(); p["scan_finished_unix"] = p["scan_started_unix"] - 1; cases.append((p, "scan_finished"))
        p = base_payload(); p["selected_roots"].append(copy.deepcopy(p["selected_roots"][0])); cases.append((p, "unique"))
        p = base_payload(); p["mounts"][0]["mount_id"] = "absent"; cases.append((p, "selected"))
        p = base_payload(); p["mounts"][0]["tree"]["kind"] = "socket"; cases.append((p, "kind"))
        p = base_payload(); p["mounts"][0]["tree"]["children"][0]["bytes"] = 1; cases.append((p, "tree"))
        for payload, message in cases:
            with self.subTest(message=message):
                status, data = status_for(payload)
                with self.assertRaisesRegex(ValueError, message):
                    snapshot.validate_download(status, data, self.desired())


if __name__ == "__main__":
    unittest.main()

class SnapshotReviewGapTests(unittest.TestCase):
    GOOD_DIGEST = "a" * 64

    def desired(self, digest=None):
        return snapshot.DesiredServer(server_id="alpha-1", config_digest=digest or self.GOOD_DIGEST)

    def payload_with_digest(self):
        payload = base_payload()
        payload["config_digest"] = self.GOOD_DIGEST
        return payload

    def status_data(self, payload, *, status_value="complete", digest=None):
        status, data = status_for(payload, config_digest=digest or self.GOOD_DIGEST)
        status["status"] = status_value
        return status, data

    def assert_invalid(self, payload, message, *, status_value="complete", mutate_status=None, desired=None):
        status, data = self.status_data(payload, status_value=status_value)
        if mutate_status:
            mutate_status(status)
        with self.assertRaisesRegex(ValueError, message):
            snapshot.validate_download(status, data, desired or self.desired())

    def test_rejects_noncanonical_paths_in_roots_mounts_rows_and_user_by_mount(self):
        cases = []
        for field in ("mount_root", "mountpoint", "scan_root"):
            p = self.payload_with_digest(); p["selected_roots"][0][field] = "/home/../etc"; cases.append((p, "path"))
        p = self.payload_with_digest(); p["mounts"][0]["path"] = "/home//alpha"; cases.append((p, "path"))
        p = self.payload_with_digest(); p["top_files"] = [{"path": "/home/./x", "kind": "file", "bytes": 1, "uid": 1, "owner": "u", "mtime": 1}]; cases.append((p, "path"))
        p = self.payload_with_digest(); p["users"] = [{"uid": 1, "name": "u", "bytes": 1, "files": 1, "by_mount": {"/bad\npath": 1}}]; cases.append((p, "path"))
        for payload, message in cases:
            with self.subTest(payload=payload):
                self.assert_invalid(payload, message)

    def test_status_status_must_match_derived_complete_or_partial(self):
        p = self.payload_with_digest()
        self.assert_invalid(p, "status", status_value="failed")
        self.assert_invalid(p, "status", status_value="partial")
        p = self.payload_with_digest()
        p["selected_roots"].append({
            "mount_id": "skip", "major_minor": "8:2", "mount_source": "/dev/storage-viz/skip", "mount_root": "/",
            "mountpoint": "/skip", "scan_root": "/skip", "fstype": "xfs", "status": "skipped",
            "scanned_bytes": 0, "scanned_files": 0, "scanned_dirs": 0, "blocked_count": 0, "error_count": 0, "error_code": "POLICY",
        })
        status, data = self.status_data(p, status_value="complete")
        result = snapshot.validate_download(status, data, self.desired())
        self.assertEqual(result.config_sync, "in_sync")
        p = self.payload_with_digest()
        partial_root = dict(p["selected_roots"][0])
        partial_root.update({"mount_id": "data", "scan_root": "/data", "mountpoint": "/data", "status": "partial", "scanned_bytes": 5, "scanned_files": 1, "scanned_dirs": 1, "error_count": 2, "error_code": "EACCES", "fstype": "xfs"})
        p["selected_roots"].append(partial_root)
        mount = json.loads(json.dumps(p["mounts"][0]))
        mount.update({"path": "/data", "mount_id": "data", "scan_root": "/data", "fstype": "xfs", "scanned_bytes": 5, "scanned_files": 1, "scanned_dirs": 1, "errors": 2})
        mount["tree"] = {"name": "/data", "kind": "directory", "bytes": 5, "files": 1, "uid": 0, "mtime": 1719200042}
        p["mounts"].append(mount)
        self.assert_invalid(p, "status", status_value="complete")
        status, data = self.status_data(p, status_value="partial")
        self.assertEqual(snapshot.validate_download(status, data, self.desired()).payload["selected_roots"][1]["scanned_bytes"], 5)

    def test_root_mount_aggregates_and_relationships_are_exact(self):
        for key, value in [("scanned_bytes", 31), ("scanned_files", 3), ("scanned_dirs", 2), ("error_count", 9), ("fstype", "xfs")]:
            p = self.payload_with_digest(); p["selected_roots"][0][key] = value
            with self.subTest(key=key):
                self.assert_invalid(p, key if key != "error_count" else "errors")
        p = self.payload_with_digest(); p["mounts"][0]["path"] = "/other"
        self.assert_invalid(p, "path")


    def test_config_digest_is_mandatory_in_desired_status_and_payload(self):
        p = self.payload_with_digest()
        status, data = self.status_data(p)
        with self.assertRaisesRegex(ValueError, "desired config_digest"):
            snapshot.validate_download(status, data, snapshot.DesiredServer(server_id="alpha-1", config_digest=None))
        self.assert_invalid(p, "status config_digest", mutate_status=lambda s: s.pop("config_digest"))
        self.assert_invalid(p, "status config_digest", mutate_status=lambda s: s.update({"config_digest": "A" * 64}))
        p = self.payload_with_digest(); p.pop("config_digest")
        self.assert_invalid(p, "payload config_digest")
        p = self.payload_with_digest(); p["config_digest"] = "bad"
        self.assert_invalid(p, "payload config_digest")
        p = self.payload_with_digest(); p["config_digest"] = "b" * 64
        self.assert_invalid(p, "payload config_digest")

    def test_user_bytes_must_equal_by_mount_sum_and_bounds_are_exclusive(self):
        p = self.payload_with_digest(); p["users"] = [{"uid": 1, "name": "u", "bytes": 7, "files": 1, "by_mount": {"/home": 5}}]
        self.assert_invalid(p, "user.bytes")
        p = self.payload_with_digest(); p["users"] = [{"uid": 1, "name": "u", "bytes": 10**18 - 1, "files": 1, "by_mount": {"/home": 10**18 - 1}}]
        status, data = self.status_data(p)
        snapshot.validate_download(status, data, self.desired())
        p = self.payload_with_digest(); p["users"] = [{"uid": 1, "name": "u", "bytes": 10**18, "files": 1, "by_mount": {"/home": 10**18}}]
        self.assert_invalid(p, "bytes")
        p = self.payload_with_digest(); p["selected_roots"][0]["scanned_bytes"] = 10**18; p["mounts"][0]["scanned_bytes"] = 10**18; p["mounts"][0]["tree"]["bytes"] = 10**18
        self.assert_invalid(p, "scanned_bytes|tree.bytes")

    def test_config_digest_requires_64hex_and_links_status_payload_desired(self):
        p = self.payload_with_digest()
        self.assert_invalid(p, "config_digest", mutate_status=lambda s: s.update({"config_digest": "abc"}))
        p = self.payload_with_digest(); p["config_digest"] = "b" * 64
        self.assert_invalid(p, "config_digest")
        p = self.payload_with_digest()
        status, data = self.status_data(p)
        result = snapshot.validate_download(status, data, self.desired("b" * 64))
        self.assertEqual(result.config_sync, "drifted")

    def test_validates_users_rows_file_rows_blocked_and_counter_upper_bound(self):
        p = self.payload_with_digest(); p["users"] = [{"uid": True, "name": "u", "bytes": 1, "files": 1, "by_mount": {"/home": 1}}]
        self.assert_invalid(p, "uid")
        p = self.payload_with_digest(); p["users"] = [{"uid": 1, "name": "u", "bytes": 10**18, "files": 1, "by_mount": {"/home": 1}}]
        self.assert_invalid(p, "bytes")
        p = self.payload_with_digest(); p["top_files"] = [{"path": "/home/a", "kind": "directory", "bytes": 1, "uid": 1, "owner": "u", "mtime": 1}]
        self.assert_invalid(p, "kind")
        p = self.payload_with_digest(); p["stale"] = [{"path": "/home/a", "kind": "file", "bytes": 1, "uid": 1, "owner": "u", "mtime": 1}]
        self.assert_invalid(p, "age_days")
        p = self.payload_with_digest(); p["blocked"] = [{"path": "/home/nope", "reason": "EACCES"}]
        status, data = self.status_data(p)
        snapshot.validate_download(status, data, self.desired())
        p = self.payload_with_digest(); p["blocked"] = [{"path": 7, "reason": "EACCES"}]
        self.assert_invalid(p, "blocked.path")
        p = self.payload_with_digest(); p["blocked"] = [{"path": "/bad\x00path", "reason": "EACCES"}]
        self.assert_invalid(p, "path")
        p = self.payload_with_digest(); p["blocked"] = [{"path": "/home/nope"}]
        self.assert_invalid(p, "reason")

class SnapshotQualityReviewTests(unittest.TestCase):
    DIGEST = "a" * 64

    def desired(self):
        return snapshot.DesiredServer(server_id="alpha-1", config_digest=self.DIGEST)

    def status_data(self, payload, *, status_value="complete"):
        status, data = status_for(payload, config_digest=self.DIGEST)
        status["status"] = status_value
        return status, data

    def valid_payload(self):
        p = base_payload()
        p["config_digest"] = self.DIGEST
        return p

    def assert_invalid(self, payload, pattern, *, status_mutator=None, status_value="complete"):
        status, data = self.status_data(payload, status_value=status_value)
        if status_mutator:
            status_mutator(status)
        with self.assertRaisesRegex(ValueError, pattern):
            snapshot.validate_download(status, data, self.desired())

    def test_generation_must_bind_server_id_started_timestamp_and_v1(self):
        p = self.valid_payload()
        self.assert_invalid(p, "generation", status_mutator=lambda s: s.update({"generation": "beta-1719200000-v1.json"}))
        self.assert_invalid(p, "generation", status_mutator=lambda s: s.update({"generation": "alpha-1-1719200001-v1.json"}))
        self.assert_invalid(p, "generation", status_mutator=lambda s: s.update({"generation": "alpha-1-1719200000-v2.json"}))
        p = self.valid_payload(); p["scan_generation"] = "alpha-1-1719200001-v1"
        self.assert_invalid(p, "generation|scan_generation")

    def test_cleanup_rows_and_user_by_mount_reference_tree_producing_roots_only(self):
        p = self.valid_payload()
        p["selected_roots"].append({"mount_id":"failed","major_minor":"8:2","mount_source":"/dev/storage-viz/f","mount_root":"/","mountpoint":"/failed","scan_root":"/failed","fstype":"xfs","status":"failed","scanned_bytes":0,"scanned_files":0,"scanned_dirs":0,"blocked_count":0,"error_count":1,"error_code":"EIO"})
        p["top_files"] = [{"path":"/failed/file","kind":"file","bytes":1,"uid":1,"owner":"u","mtime":1}]
        self.assert_invalid(p, "selected scan_root", status_value="partial")
        p = self.valid_payload(); p["stale"] = [{"path":"/etc/passwd","kind":"file","bytes":1,"uid":1,"owner":"u","mtime":1,"age_days":9}]
        self.assert_invalid(p, "selected scan_root")
        p = self.valid_payload(); p["users"] = [{"uid":1,"name":"u","bytes":1,"files":1,"by_mount":{"/failed":1}}]
        p["selected_roots"].append({"mount_id":"failed","major_minor":"8:2","mount_source":"/dev/storage-viz/f","mount_root":"/","mountpoint":"/failed","scan_root":"/failed","fstype":"xfs","status":"failed","scanned_bytes":0,"scanned_files":0,"scanned_dirs":0,"blocked_count":0,"error_count":1,"error_code":"EIO"})
        self.assert_invalid(p, "tree-producing", status_value="partial")

    def test_blocked_absolute_paths_must_be_selected_descendants_but_malformed_display_is_tolerated(self):
        p = self.valid_payload(); p["blocked"] = [{"path":"relative display path","reason":"EACCES"}, {"path":"not//canonical","reason":"EIO"}]
        status, data = self.status_data(p)
        snapshot.validate_download(status, data, self.desired())
        p = self.valid_payload(); p["blocked"] = [{"path":"/etc/passwd","reason":"EACCES"}]
        self.assert_invalid(p, "selected root")
        p = self.valid_payload(); p["blocked"] = [{"path":"/home//bad","reason":"EACCES"}]
        self.assert_invalid(p, "canonical")

    def test_dos_limits_are_enforced_with_monkeypatched_small_limits(self):
        p = self.valid_payload()
        status, data = self.status_data(p)
        old = snapshot.MAX_SNAPSHOT_BYTES
        snapshot.MAX_SNAPSHOT_BYTES = len(data) - 1
        try:
            with self.assertRaisesRegex(ValueError, "snapshot bytes"):
                snapshot.validate_download(status, data, self.desired())
        finally:
            snapshot.MAX_SNAPSHOT_BYTES = old
        p = self.valid_payload(); p["selected_roots"] = p["selected_roots"] * 2
        old = snapshot.MAX_SELECTED_ROOTS; snapshot.MAX_SELECTED_ROOTS = 1
        try:
            self.assert_invalid(p, "selected_roots")
        finally:
            snapshot.MAX_SELECTED_ROOTS = old
        p = self.valid_payload(); p["mounts"][0]["tree"]["children"] = [{"name":"a","kind":"file","bytes":1,"files":1,"uid":1,"mtime":1}, {"name":"b","kind":"file","bytes":1,"files":1,"uid":1,"mtime":1}]; p["mounts"][0]["tree"]["bytes"] = 12
        old = snapshot.MAX_CHILDREN_PER_NODE; snapshot.MAX_CHILDREN_PER_NODE = 1
        try:
            self.assert_invalid(p, "children")
        finally:
            snapshot.MAX_CHILDREN_PER_NODE = old

    def test_tree_files_are_at_least_child_files_and_children_cannot_exceed_parent(self):
        p = self.valid_payload()
        p["mounts"][0]["tree"]["children"][0]["files"] = 999999
        self.assert_invalid(p, "files")
        p = self.valid_payload()
        p["selected_roots"][0]["scanned_files"] = 5
        p["mounts"][0]["scanned_files"] = 5
        p["mounts"][0]["tree"]["files"] = 5
        status, data = self.status_data(p)
        snapshot.validate_download(status, data, self.desired())

    def test_status_scan_finished_unix_rejects_bool(self):
        p = self.valid_payload()
        self.assert_invalid(p, "scan_finished_unix", status_mutator=lambda s: s.update({"scan_finished_unix": True}))
