#!/usr/bin/env python3
"""Regression checks for tracked storage-viz data fixtures."""
import hashlib
import json
import pathlib
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
GENERATOR = DATA_DIR / "gen_sample.py"
EXPECTED_SAMPLE_IDS = ["hinton", "atlas", "orion", "zeus"]
MEDIA_VALUES = {"ssd", "hdd", "mixed", "unknown"}
CONFIDENCE_VALUES = {"resolved", "unresolved"}



STATUSES_WITH_TREES = {"complete", "partial"}
STATUSES_WITHOUT_TREES = {"failed", "skipped"}
ALL_STATUSES = STATUSES_WITH_TREES | STATUSES_WITHOUT_TREES
NODE_KINDS = {"directory", "file", "symlink", "other"}


def walk_tree(node):
    yield node
    for child in node.get("children", []):
        yield from walk_tree(child)


def assert_bounded_count(testcase, value, name):
    testcase.assertIsInstance(value, int, name)
    testcase.assertGreaterEqual(value, 0, name)
    testcase.assertLess(value, 10**18, name)


def assert_nullable_error_code(testcase, value):
    if value is not None:
        testcase.assertIsInstance(value, str)
        testcase.assertTrue(value)
        testcase.assertLess(len(value), 128)


def assert_tree_byte_invariants(testcase, node):
    testcase.assertIsInstance(node.get("bytes"), int)
    testcase.assertGreaterEqual(node["bytes"], 0)
    testcase.assertIsInstance(node.get("files"), int)
    testcase.assertGreaterEqual(node["files"], 0)
    other = node.get("other_bytes", 0)
    testcase.assertIsInstance(other, int)
    testcase.assertGreaterEqual(other, 0)
    children = node.get("children", [])
    if children:
        testcase.assertEqual(
            node["bytes"],
            sum(child["bytes"] for child in children) + other,
            f"byte mismatch at {node.get('name')}",
        )
    else:
        testcase.assertEqual(other, 0, f"leaf other_bytes should be zero at {node.get('name')}")
    for child in children:
        assert_tree_byte_invariants(testcase, child)


def assert_schema_v1_snapshot_contract(testcase, payload):
    testcase.assertEqual(payload["schema_version"], 1)
    testcase.assertIsInstance(payload["scan_started_unix"], int)
    testcase.assertIsInstance(payload["scan_finished_unix"], int)
    testcase.assertIsInstance(payload["scan_duration_sec"], int)
    testcase.assertEqual(
        payload["scan_finished_unix"],
        payload["scan_started_unix"] + payload["scan_duration_sec"],
    )
    testcase.assertIsInstance(payload["selected_roots"], list)
    testcase.assertGreaterEqual(len(payload["selected_roots"]), 1)
    testcase.assertIsInstance(payload.get("mounts"), list)
    testcase.assertIsInstance(payload.get("top_files"), list)
    testcase.assertIsInstance(payload.get("stale"), list)

    roots_by_id = {root["mount_id"]: root for root in payload["selected_roots"]}
    testcase.assertEqual(len(roots_by_id), len(payload["selected_roots"]))
    mounts_by_id = {}
    for mount in payload["mounts"]:
        with testcase.subTest(mount=mount.get("path")):
            testcase.assertIn(mount["mount_id"], roots_by_id)
            testcase.assertEqual(mount["scan_root"], roots_by_id[mount["mount_id"]]["scan_root"])
            mounts_by_id[mount["mount_id"]] = mount
            testcase.assertEqual(mount["tree"]["bytes"], mount["scanned_bytes"])
            testcase.assertEqual(mount["tree"]["files"], mount["scanned_files"])
            assert_tree_byte_invariants(testcase, mount["tree"])
            root = roots_by_id[mount["mount_id"]]
            testcase.assertEqual(mount.get("storage_media"), root.get("storage_media"))
            testcase.assertEqual(mount.get("storage_media_confidence"), root.get("storage_media_confidence"))
            testcase.assertEqual(mount.get("capacity_id"), root.get("capacity_id"))
            for tree_node in walk_tree(mount["tree"]):
                testcase.assertIn(tree_node["kind"], NODE_KINDS)

    for root in payload["selected_roots"]:
        with testcase.subTest(selected_root=root.get("scan_root")):
            testcase.assertRegex(root["mount_id"], r"^[A-Za-z0-9_.-]+$")
            testcase.assertRegex(root["major_minor"], r"^\d+:\d+$")
            testcase.assertTrue(root["mount_source"])
            testcase.assertTrue(root["mount_root"].startswith("/"))
            testcase.assertTrue(root["mountpoint"].startswith("/"))
            testcase.assertTrue(root["scan_root"].startswith("/"))
            testcase.assertTrue(root["fstype"])
            media = root.get("storage_media")
            confidence = root.get("storage_media_confidence")
            testcase.assertIn(media, MEDIA_VALUES)
            testcase.assertIn(confidence, CONFIDENCE_VALUES)
            if media == "unknown":
                testcase.assertEqual(confidence, "unresolved")
            else:
                testcase.assertEqual(confidence, "resolved")
                testcase.assertEqual(root.get("capacity_id"), "dev-" + root["major_minor"].replace(":", "-"))
            testcase.assertIn(root["status"], ALL_STATUSES)
            assert_bounded_count(testcase, root["scanned_bytes"], "selected root scanned_bytes")
            assert_bounded_count(testcase, root["scanned_files"], "selected root scanned_files")
            assert_bounded_count(testcase, root["scanned_dirs"], "selected root scanned_dirs")
            assert_bounded_count(testcase, root["blocked_count"], "selected root blocked_count")
            assert_bounded_count(testcase, root["error_count"], "selected root error_count")
            assert_nullable_error_code(testcase, root["error_code"])
            if root["status"] in STATUSES_WITH_TREES:
                testcase.assertIn(root["mount_id"], mounts_by_id)
            else:
                testcase.assertNotIn(root["mount_id"], mounts_by_id)
                testcase.assertEqual(root["scanned_bytes"], 0)
                testcase.assertEqual(root["scanned_files"], 0)
                testcase.assertEqual(root["scanned_dirs"], 0)

    for row_set in (payload["top_files"], payload["stale"]):
        for row in row_set:
            with testcase.subTest(row=row.get("path")):
                testcase.assertIn(row["kind"], NODE_KINDS)


class DataFixtureTests(unittest.TestCase):
    def test_sample_generator_uses_repo_relative_output(self):
        text = GENERATOR.read_text(encoding="utf-8")
        self.assertNotIn("/home/shchoi/storage-viz", text)

    def test_hosts_manifest_shape(self):
        hosts_path = DATA_DIR / "hosts.json"
        hosts = json.loads(hosts_path.read_text(encoding="utf-8"))
        self.assertIsInstance(hosts, list)
        self.assertEqual([host["id"] for host in hosts], EXPECTED_SAMPLE_IDS)
        defaults = [host for host in hosts if host.get("default") is True]
        self.assertEqual(len(defaults), 1)
        for host in hosts:
            with self.subTest(host=host):
                self.assertRegex(host["id"], r"^[A-Za-z0-9_.-]+$")
                self.assertTrue(host["label"])
                self.assertTrue(host["file"])
                self.assertNotIn("/", host["file"])
                self.assertFalse(host["file"].endswith(".json"))
                self.assertEqual(host["file"], host["id"])
                self.assertIs(host.get("sample_data"), True)

    def test_sample_generator_writes_all_ordered_fixtures_byte_for_byte(self):
        before = {sid: (DATA_DIR / f"{sid}.sample.json").read_bytes() for sid in EXPECTED_SAMPLE_IDS}
        result = subprocess.run(
            [sys.executable, str(GENERATOR)],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertIn("tree byte-consistency: OK", result.stdout)
        after = {sid: (DATA_DIR / f"{sid}.sample.json").read_bytes() for sid in EXPECTED_SAMPLE_IDS}
        self.assertEqual(
            {sid: hashlib.sha256(data).hexdigest() for sid, data in after.items()},
            {sid: hashlib.sha256(data).hexdigest() for sid, data in before.items()},
        )

    def test_generated_fixtures_have_unique_ids_metadata_and_coverage(self):
        payloads = []
        statuses = set()
        media_sets = {sid: set() for sid in EXPECTED_SAMPLE_IDS}
        generations = set()
        for sid in EXPECTED_SAMPLE_IDS:
            payload = json.loads((DATA_DIR / f"{sid}.sample.json").read_text(encoding="utf-8"))
            payloads.append(payload)
            self.assertEqual(payload["hostname"], sid)
            self.assertEqual(payload["server_id"], sid)
            self.assertNotIn("example.com", json.dumps(payload))
            self.assertNotIn("/Users/", json.dumps(payload))
            self.assertRegex(payload["scan_generation"], rf"^{sid}-1719200\d{{3}}-v1$")
            generations.add(payload["scan_generation"])
            self.assertGreaterEqual(len(payload["mounts"]), 1)
            self.assertGreaterEqual(len(payload["users"]), 1)
            assert_schema_v1_snapshot_contract(self, payload)
            roots_by_id = {root["mount_id"]: root for root in payload["selected_roots"]}
            for mount in payload["mounts"]:
                root = roots_by_id[mount["mount_id"]]
                media_sets[sid].add(mount["storage_media"] if mount["storage_media"] != "unknown" else "unknown")
                pct = mount["df_use_pct"]
                if pct >= 92 or mount["df_avail"] <= 137438953472:
                    statuses.add("critical")
                elif pct >= 80 or mount["df_avail"] <= 549755813888:
                    statuses.add("warning")
                else:
                    statuses.add("healthy")
                self.assertEqual(mount["scan_root"], root["scan_root"])
            mount_paths = {mount["path"] for mount in payload["mounts"]}
            for user in payload["users"]:
                self.assertLessEqual(set(user["by_mount"]), mount_paths)
        self.assertEqual([payload["server_id"] for payload in payloads], EXPECTED_SAMPLE_IDS)
        self.assertEqual(len(generations), len(EXPECTED_SAMPLE_IDS))
        self.assertTrue(any(media == {"ssd"} for media in media_sets.values()))
        self.assertTrue(any(media == {"hdd"} for media in media_sets.values()))
        self.assertIn("mixed", set().union(*media_sets.values()))
        self.assertIn("unknown", set().union(*media_sets.values()))
        self.assertEqual(statuses, {"healthy", "warning", "critical"})

    def test_hinton_preserves_rich_tree_semantics(self):
        payload = json.loads((DATA_DIR / "hinton.sample.json").read_text(encoding="utf-8"))
        roots_by_id = {root["mount_id"]: root for root in payload["selected_roots"]}
        self.assertEqual(roots_by_id["rootfs"]["mountpoint"], "/")
        self.assertEqual(roots_by_id["rootfs"]["scan_root"], "/home")
        rootfs_mount = next(mount for mount in payload["mounts"] if mount["mount_id"] == "rootfs")
        self.assertEqual(rootfs_mount["path"], "/home")
        self.assertEqual(rootfs_mount["scan_root"], "/home")
        self.assertEqual(rootfs_mount["tree"]["name"], "/home")
        self.assertEqual({root["status"] for root in payload["selected_roots"]}, {"complete", "partial"})

    def test_schema_contract_allows_failed_and_skipped_roots_without_mounts(self):
        complete_tree = {
            "name": "/home",
            "kind": "directory",
            "bytes": 10,
            "files": 1,
            "uid": 0,
            "mtime": 1719200000,
            "other_bytes": 0,
        }
        partial_tree = {
            "name": "/data",
            "kind": "directory",
            "bytes": 20,
            "files": 2,
            "uid": 0,
            "mtime": 1719200000,
            "other_bytes": 0,
        }
        payload = {
            "schema_version": 1,
            "hostname": "contract-host",
            "server_id": "contract-host",
            "scanner_version": "contract",
            "scan_started_unix": 1719200000,
            "scan_finished_unix": 1719200042,
            "scan_duration_sec": 42,
            "scan_generation": "contract-host-1719200000-v1",
            "run_as_root": True,
            "selected_roots": [
                {
                    "mount_id": "home",
                    "major_minor": "8:1",
                    "capacity_id": "dev-8-1",
                    "storage_media": "ssd",
                    "storage_media_confidence": "resolved",
                    "mount_source": "/dev/storage-viz/home",
                    "mount_root": "/",
                    "mountpoint": "/",
                    "scan_root": "/home",
                    "fstype": "ext4",
                    "status": "complete",
                    "scanned_bytes": 10,
                    "scanned_files": 1,
                    "scanned_dirs": 1,
                    "blocked_count": 0,
                    "error_count": 0,
                    "error_code": None,
                },
                {
                    "mount_id": "data",
                    "major_minor": "8:16",
                    "capacity_id": "dev-8-16",
                    "storage_media": "mixed",
                    "storage_media_confidence": "resolved",
                    "mount_source": "/dev/storage-viz/data",
                    "mount_root": "/",
                    "mountpoint": "/data",
                    "scan_root": "/data",
                    "fstype": "xfs",
                    "status": "partial",
                    "scanned_bytes": 20,
                    "scanned_files": 2,
                    "scanned_dirs": 1,
                    "blocked_count": 1,
                    "error_count": 1,
                    "error_code": "EACCES",
                },
                {
                    "mount_id": "archive",
                    "major_minor": "8:32",
                    "capacity_id": "dev-8-32",
                    "storage_media": "hdd",
                    "storage_media_confidence": "resolved",
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
                    "error_code": "ENOENT",
                },
                {
                    "mount_id": "scratch",
                    "major_minor": "8:48",
                    "capacity_id": "dev-8-48",
                    "storage_media": "unknown",
                    "storage_media_confidence": "unresolved",
                    "mount_source": "/dev/storage-viz/scratch",
                    "mount_root": "/",
                    "mountpoint": "/scratch",
                    "scan_root": "/scratch",
                    "fstype": "xfs",
                    "status": "skipped",
                    "scanned_bytes": 0,
                    "scanned_files": 0,
                    "scanned_dirs": 0,
                    "blocked_count": 0,
                    "error_count": 0,
                    "error_code": None,
                },
            ],
            "mounts": [
                {
                    "path": "/home",
                    "mount_id": "home",
                    "capacity_id": "dev-8-1",
                    "storage_media": "ssd",
                    "storage_media_confidence": "resolved",
                    "scan_root": "/home",
                    "fstype": "ext4",
                    "df_total": 100,
                    "df_used": 10,
                    "df_avail": 90,
                    "df_use_pct": 10,
                    "scanned_bytes": 10,
                    "scanned_files": 1,
                    "scanned_dirs": 1,
                    "errors": 0,
                    "tree": complete_tree,
                },
                {
                    "path": "/data",
                    "mount_id": "data",
                    "capacity_id": "dev-8-16",
                    "storage_media": "mixed",
                    "storage_media_confidence": "resolved",
                    "scan_root": "/data",
                    "fstype": "xfs",
                    "df_total": 100,
                    "df_used": 20,
                    "df_avail": 80,
                    "df_use_pct": 20,
                    "scanned_bytes": 20,
                    "scanned_files": 2,
                    "scanned_dirs": 1,
                    "errors": 1,
                    "tree": partial_tree,
                },
            ],
            "users": [],
            "top_files": [{"path": "/home/a.bin", "kind": "file", "bytes": 10, "uid": 0, "owner": "root", "mtime": 1719200000}],
            "stale": [],
            "blocked": [],
        }

        assert_schema_v1_snapshot_contract(self, payload)


if __name__ == "__main__":
    unittest.main()
