import hashlib
import json
import os
import pathlib
import tempfile
import unittest

from agent import scan_runner
from data.test_fixtures import assert_schema_v1_snapshot_contract

def mi(mid, parent, dev, root, mountpoint, options, fstype, source, super_options="rw"):
    return f"{mid} {parent} {dev} {root} {mountpoint} {options} - {fstype} {source} {super_options}"


def raw_payload(path="/home", errors=0, started=100):
    return {
        "schema_version": 1,
        "hostname": "host-a",
        "scanner_version": "0.1.0",
        "scan_started_unix": started,
        "scan_duration_sec": 5.2,
        "run_as_root": False,
        "mounts": [{
            "path": path,
            "fstype": "ext4",
            "df_total": 1000,
            "df_used": 400,
            "df_avail": 600,
            "df_use_pct": 40,
            "scanned_bytes": 123,
            "scanned_files": 4,
            "scanned_dirs": 2,
            "errors": errors,
            "tree": {"name": path, "kind": "directory", "bytes": 123, "files": 4, "uid": 0, "mtime": 99, "other_bytes": 0},
        }],
        "users": [],
        "top_files": [{"path": f"{path}/big.bin", "kind": "file", "bytes": 90, "uid": 0, "owner": "root", "mtime": 80}],
        "stale": [{"path": f"{path}/old.bin", "kind": "file", "bytes": 91, "uid": 0, "owner": "root", "mtime": 1, "age_days": 200}],
        "blocked": [],
    }


def raw_payload_many(paths, blocked=None, errors_by_path=None):
    errors_by_path = errors_by_path or {}
    payload = raw_payload(paths[0], errors=errors_by_path.get(paths[0], 0))
    payload["mounts"] = []
    for path in paths:
        payload["mounts"].append({
            "path": path,
            "fstype": "ext4",
            "df_total": 1000,
            "df_used": 400,
            "df_avail": 600,
            "df_use_pct": 40,
            "scanned_bytes": 123,
            "scanned_files": 4,
            "scanned_dirs": 2,
            "errors": errors_by_path.get(path, 0),
            "tree": {"name": path, "kind": "directory", "bytes": 123, "files": 4, "uid": 0, "mtime": 99, "other_bytes": 0},
        })
    payload["blocked"] = blocked or []
    return payload


class Clock:
    def __init__(self, *values):
        self.values = list(values)
    def __call__(self):
        if len(self.values) == 1:
            return self.values[0]
        return self.values.pop(0)


class ScanRunnerTests(unittest.TestCase):
    def write_config(self, tmp, **overrides):
        data = {
            "server_id": "host-a",
            "scanner_path": "/bin/hstscan",
            "data_dir": str(tmp / "data"),
            "run_dir": str(tmp / "run"),
            "threads": 2,
            "prune_home_mb": 10,
            "prune_data_mb": 20,
            "top": 7,
            "stale_days": 180,
        }
        data.update(overrides)
        path = tmp / "scanner.yaml"
        path.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
        return path, data

    def test_forbidden_config_keys(self):
        forbidden = [
            "targets", "include_mounts", "exclude_mounts", "/", "mounts", "mountpoints",
            "scan_roots", "root", "roots", "paths", "path", "include_paths", "exclude_paths",
        ]
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            for key in forbidden:
                with self.subTest(key=key):
                    path, _ = self.write_config(tmp, **{key: ["/data"]})
                    with self.assertRaises(ValueError):
                        scan_runner.load_config(path)
            path, _ = self.write_config(tmp, surprise=True)
            with self.assertRaises(ValueError):
                scan_runner.load_config(path)

    def test_config_validation_and_canonical_sha256_config_digest(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            path, data = self.write_config(tmp)
            pretty = tmp / "pretty.yaml"
            pretty.write_text(json.dumps(data, indent=2), encoding="utf-8")

            cfg1 = scan_runner.load_config(path)
            cfg2 = scan_runner.load_config(pretty)
            expected = hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

            self.assertEqual(cfg1.config_digest, expected)
            self.assertEqual(cfg2.config_digest, expected)
            self.assertEqual(cfg1.server_id, "host-a")
            self.assertGreaterEqual(cfg1.threads, 1)

    def test_scanner_argv(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            config_path, _ = self.write_config(tmp)
            mountinfo = mi(1, 0, "8:1", "/", "/", "rw", "ext4", "/dev/sda1")
            calls = []
            def fake(argv, *, shell, stdout, stderr, text):
                calls.append((argv, shell))
                out = pathlib.Path(argv[argv.index("--out") + 1])
                out.write_text(json.dumps(raw_payload("/home")), encoding="utf-8")
                return scan_runner.CompletedScan(0, "", "")

            scan_runner.run_once(config_path, mountinfo_reader=lambda: mountinfo, scanner_runner=fake, clock=Clock(200))

            argv, shell = calls[0]
            self.assertFalse(shell)
            self.assertEqual(argv[:2], ["/bin/hstscan", "--threads"])
            self.assertIn("--out", argv)
            self.assertEqual(argv[-1], "/home")
            self.assertNotIn("/", argv[:-1])

    def test_partial_snapshot_requires_at_least_one_completed_root(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            config_path, _ = self.write_config(tmp)
            mountinfo = mi(1, 0, "0:1", "/", "/net", "rw", "nfs", "server:/net")
            def not_called(*args, **kwargs):
                self.fail("scanner must not run when no safe roots are selected")

            result = scan_runner.run_once(config_path, mountinfo_reader=lambda: mountinfo, scanner_runner=not_called, clock=Clock(200))

            self.assertEqual(result.status, "failed")
            self.assertFalse((tmp / "data" / "scan-status.json").exists())
            self.assertEqual(list((tmp / "data" / "snapshots").glob("*.json")), [])

    def test_partial_snapshot_keeps_failed_and_skipped_selected_roots(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            config_path, _ = self.write_config(tmp)
            mountinfo = "\n".join([
                mi(1, 0, "8:1", "/", "/", "rw", "ext4", "/dev/sda1"),
                mi(2, 1, "8:2", "/", "/data", "rw", "xfs", "/dev/sdb1"),
            ])
            def fake(argv, **kwargs):
                out = pathlib.Path(argv[argv.index("--out") + 1])
                out.write_text(json.dumps(raw_payload("/home")), encoding="utf-8")
                return scan_runner.CompletedScan(0, "", "")

            result = scan_runner.run_once(config_path, mountinfo_reader=lambda: mountinfo, scanner_runner=fake, clock=Clock(200))
            payload = json.loads(result.snapshot_path.read_text(encoding="utf-8"))

            self.assertEqual(result.status, "partial")
            roots = {root["scan_root"]: root for root in payload["selected_roots"]}
            self.assertEqual(roots["/home"]["status"], "complete")
            self.assertEqual(roots["/data"]["status"], "failed")
            self.assertEqual(roots["/data"]["error_code"], "MISSING_RAW_MOUNT")
            self.assertEqual([m["scan_root"] for m in payload["mounts"]], ["/home"])

    def test_total_failure_keeps_previous_generation_and_status(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            config_path, _ = self.write_config(tmp)
            mountinfo = mi(1, 0, "8:1", "/", "/", "rw", "ext4", "/dev/sda1")
            def good(argv, **kwargs):
                pathlib.Path(argv[argv.index("--out") + 1]).write_text(json.dumps(raw_payload("/home")), encoding="utf-8")
                return scan_runner.CompletedScan(0, "", "")
            first = scan_runner.run_once(config_path, mountinfo_reader=lambda: mountinfo, scanner_runner=good, clock=Clock(200))
            before_status = (tmp / "data" / "scan-status.json").read_text(encoding="utf-8")
            before_snapshot = first.snapshot_path.read_text(encoding="utf-8")

            def bad(argv, **kwargs):
                return scan_runner.CompletedScan(1, "", "boom")
            second = scan_runner.run_once(config_path, mountinfo_reader=lambda: mountinfo, scanner_runner=bad, clock=Clock(201))

            self.assertEqual(second.status, "failed")
            self.assertEqual((tmp / "data" / "scan-status.json").read_text(encoding="utf-8"), before_status)
            self.assertEqual(first.snapshot_path.read_text(encoding="utf-8"), before_snapshot)
            self.assertEqual(sorted(p.name for p in (tmp / "data" / "snapshots").glob("*.json")), [first.snapshot_path.name])

    def test_digest_size_tuple_and_retention(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            config_path, _ = self.write_config(tmp)
            mountinfo = mi(1, 0, "8:1", "/", "/", "rw", "ext4", "/dev/sda1")
            starts = iter((100, 101, 102))
            def fake(argv, **kwargs):
                pathlib.Path(argv[argv.index("--out") + 1]).write_text(json.dumps(raw_payload("/home", started=next(starts))), encoding="utf-8")
                return scan_runner.CompletedScan(0, "", "")

            results = [scan_runner.run_once(config_path, mountinfo_reader=lambda: mountinfo, scanner_runner=fake, clock=Clock(t)) for t in (200, 201, 202)]
            status = json.loads((tmp / "data" / "scan-status.json").read_text(encoding="utf-8"))
            current = results[-1].snapshot_path
            digest = hashlib.sha256(current.read_bytes()).hexdigest()

            self.assertEqual(status["generation"], current.name)
            self.assertEqual(status["byte_size"], current.stat().st_size)
            self.assertEqual(status["sha256"], digest)
            self.assertEqual(status["scan_finished_unix"], 202)
            self.assertEqual(status["server_id"], "host-a")
            self.assertEqual(status["config_digest"], scan_runner.load_config(config_path).config_digest)
            self.assertEqual(sorted(p.name for p in (tmp / "data" / "snapshots").glob("*.json")), [results[-2].snapshot_path.name, results[-1].snapshot_path.name])


    def test_run_once_snapshot_and_status_validate_with_collector_without_hand_editing(self):
        from collector import snapshot
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            config_path, _ = self.write_config(tmp)
            mountinfo = mi(1, 0, "8:1", "/", "/", "rw", "ext4", "/dev/sda1")
            def fake(argv, **kwargs):
                pathlib.Path(argv[argv.index("--out") + 1]).write_text(json.dumps(raw_payload("/home")), encoding="utf-8")
                return scan_runner.CompletedScan(0, "", "")

            result = scan_runner.run_once(config_path, mountinfo_reader=lambda: mountinfo, scanner_runner=fake, clock=Clock(200))
            status = json.loads(result.status_path.read_text(encoding="utf-8"))
            data = result.snapshot_path.read_bytes()
            desired = snapshot.DesiredServer("host-a", scan_runner.load_config(config_path).config_digest)

            validated = snapshot.validate_download(status, data, desired)

            self.assertEqual(validated.generation, "host-a-100-v1.json")
            self.assertEqual(validated.payload["scan_generation"], "host-a-100-v1")
            self.assertEqual(validated.payload["config_digest"], desired.config_digest)

    def test_scan_finished_unix_is_captured_after_scanner_and_enrichment(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            config_path, _ = self.write_config(tmp)
            mountinfo = mi(1, 0, "8:1", "/", "/", "rw", "ext4", "/dev/sda1")
            events = []
            def clock():
                self.assertEqual(events, ["scanner-ran"], "finish clock must be read only after scanner returns")
                events.append("clock-read")
                return 222
            def fake(argv, **kwargs):
                pathlib.Path(argv[argv.index("--out") + 1]).write_text(json.dumps(raw_payload("/home")), encoding="utf-8")
                events.append("scanner-ran")
                return scan_runner.CompletedScan(0, "", "")

            result = scan_runner.run_once(config_path, mountinfo_reader=lambda: mountinfo, scanner_runner=fake, clock=clock)
            payload = json.loads(result.snapshot_path.read_text(encoding="utf-8"))
            status = json.loads((tmp / "data" / "scan-status.json").read_text(encoding="utf-8"))

            self.assertEqual(events, ["scanner-ran", "clock-read"])
            self.assertEqual(result.generation, "host-a-100-v1")
            self.assertEqual(payload["scan_finished_unix"], 222)
            self.assertEqual(status["scan_finished_unix"], 222)

    def test_only_partial_roots_are_total_failure_and_preserve_previous_good(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            config_path, _ = self.write_config(tmp)
            mountinfo = mi(1, 0, "8:1", "/", "/", "rw", "ext4", "/dev/sda1")
            def good(argv, **kwargs):
                pathlib.Path(argv[argv.index("--out") + 1]).write_text(json.dumps(raw_payload("/home")), encoding="utf-8")
                return scan_runner.CompletedScan(0, "", "")
            first = scan_runner.run_once(config_path, mountinfo_reader=lambda: mountinfo, scanner_runner=good, clock=Clock(200, 201))
            before_status = (tmp / "data" / "scan-status.json").read_text(encoding="utf-8")
            before_snapshot = first.snapshot_path.read_text(encoding="utf-8")

            def partial_only(argv, **kwargs):
                pathlib.Path(argv[argv.index("--out") + 1]).write_text(json.dumps(raw_payload("/home", errors=2)), encoding="utf-8")
                return scan_runner.CompletedScan(0, "", "")
            result = scan_runner.run_once(config_path, mountinfo_reader=lambda: mountinfo, scanner_runner=partial_only, clock=Clock(300, 301))

            self.assertEqual(result.status, "failed")
            self.assertEqual((tmp / "data" / "scan-status.json").read_text(encoding="utf-8"), before_status)
            self.assertEqual(first.snapshot_path.read_text(encoding="utf-8"), before_snapshot)
            self.assertEqual(sorted(p.name for p in (tmp / "data" / "snapshots").glob("*.json")), [first.snapshot_path.name])

    def test_retention_uses_status_order_not_filesystem_mtime(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            config_path, _ = self.write_config(tmp)
            mountinfo = mi(1, 0, "8:1", "/", "/", "rw", "ext4", "/dev/sda1")
            starts = iter((100, 200, 300))
            def fake(argv, **kwargs):
                pathlib.Path(argv[argv.index("--out") + 1]).write_text(json.dumps(raw_payload("/home", started=next(starts))), encoding="utf-8")
                return scan_runner.CompletedScan(0, "", "")

            first = scan_runner.run_once(config_path, mountinfo_reader=lambda: mountinfo, scanner_runner=fake, clock=Clock(101))
            second = scan_runner.run_once(config_path, mountinfo_reader=lambda: mountinfo, scanner_runner=fake, clock=Clock(201))
            os.utime(first.snapshot_path, (999999999, 999999999))
            os.utime(second.snapshot_path, (1, 1))
            third = scan_runner.run_once(config_path, mountinfo_reader=lambda: mountinfo, scanner_runner=fake, clock=Clock(301))
            status = json.loads((tmp / "data" / "scan-status.json").read_text(encoding="utf-8"))

            retained = sorted(p.name for p in (tmp / "data" / "snapshots").glob("*.json"))
            self.assertEqual(retained, sorted([second.snapshot_path.name, third.snapshot_path.name]))
            self.assertEqual(status["retained_generations"], [third.snapshot_path.name, second.snapshot_path.name])

    def test_selected_roots_include_skipped_mount_policy_records_without_mounts(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            config_path, _ = self.write_config(tmp)
            mountinfo = "\n".join([
                mi(1, 0, "8:1", "/", "/", "rw", "ext4", "/dev/sda1"),
                mi(2, 1, "8:1", "/", "/mnt/root-bind", "rw", "ext4", "/dev/sda1"),
                mi(3, 1, "0:3", "/", "/net", "rw", "nfs", "server:/net"),
                mi(4, 1, "0:4", "/", "/mystery", "rw", "weirdfs", "mystery"),
            ])
            def fake(argv, **kwargs):
                pathlib.Path(argv[argv.index("--out") + 1]).write_text(json.dumps(raw_payload("/home")), encoding="utf-8")
                return scan_runner.CompletedScan(0, "", "")

            result = scan_runner.run_once(config_path, mountinfo_reader=lambda: mountinfo, scanner_runner=fake, clock=Clock(500, 501))
            payload = json.loads(result.snapshot_path.read_text(encoding="utf-8"))
            roots = {root["scan_root"]: root for root in payload["selected_roots"]}

            self.assertEqual(roots["/home"]["status"], "complete")
            for scan_root, code in (("/mnt/root-bind", "duplicate"), ("/net", "remote-fs"), ("/mystery", "unsupported-fstype"), ("/", "root-limited-to-home")):
                with self.subTest(scan_root=scan_root):
                    self.assertEqual(roots[scan_root]["status"], "skipped")
                    self.assertEqual(roots[scan_root]["error_code"], code)
                    self.assertLess(len(roots[scan_root]["error_code"]), 128)
                    self.assertEqual(roots[scan_root]["scanned_bytes"], 0)
                    self.assertEqual(roots[scan_root]["scanned_files"], 0)
                    self.assertEqual(roots[scan_root]["scanned_dirs"], 0)
            self.assertEqual([m["scan_root"] for m in payload["mounts"]], ["/home"])


    def test_blocked_count_is_attributed_to_longest_matching_selected_root_only(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            config_path, _ = self.write_config(tmp)
            mountinfo = "\n".join([
                mi(1, 0, "8:1", "/", "/", "rw", "ext4", "/dev/sda1"),
                mi(2, 1, "8:2", "/", "/data", "rw", "xfs", "/dev/sdb1"),
            ])
            blocked = [
                {"path": "/data/secret", "reason": "EACCES"},
                {"path": "/database/not-a-data-child", "reason": "EACCES"},
                {"path": "/opt/not-selected", "reason": "EACCES"},
            ]
            def fake(argv, **kwargs):
                pathlib.Path(argv[argv.index("--out") + 1]).write_text(
                    json.dumps(raw_payload_many(["/home", "/data"], blocked=blocked)), encoding="utf-8"
                )
                return scan_runner.CompletedScan(0, "", "")

            result = scan_runner.run_once(config_path, mountinfo_reader=lambda: mountinfo, scanner_runner=fake, clock=Clock(600))
            roots = {root["scan_root"]: root for root in json.loads(result.snapshot_path.read_text(encoding="utf-8"))["selected_roots"]}

            self.assertEqual(roots["/home"]["blocked_count"], 0)
            self.assertEqual(roots["/data"]["blocked_count"], 1)

    def test_blocked_count_uses_longest_match_for_nested_selected_roots(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            config_path, _ = self.write_config(tmp)
            mountinfo = "\n".join([
                mi(1, 0, "8:1", "/", "/data", "rw", "xfs", "/dev/sdb1"),
                mi(2, 1, "8:2", "/", "/data/projects", "rw", "xfs", "/dev/sdc1"),
            ])
            blocked = [
                {"path": "/data/root-only", "reason": "EACCES"},
                {"path": "/data/projects/secret", "reason": "EACCES"},
                {"path": "/data/projects-deceptive/secret", "reason": "EACCES"},
            ]
            def fake(argv, **kwargs):
                pathlib.Path(argv[argv.index("--out") + 1]).write_text(
                    json.dumps(raw_payload_many(["/data", "/data/projects"], blocked=blocked)), encoding="utf-8"
                )
                return scan_runner.CompletedScan(0, "", "")

            result = scan_runner.run_once(config_path, mountinfo_reader=lambda: mountinfo, scanner_runner=fake, clock=Clock(601))
            roots = {root["scan_root"]: root for root in json.loads(result.snapshot_path.read_text(encoding="utf-8"))["selected_roots"]}

            self.assertEqual(roots["/data"]["blocked_count"], 2)
            self.assertEqual(roots["/data/projects"]["blocked_count"], 1)


    def test_published_snapshot_satisfies_shared_schema_with_integer_timing(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            config_path, _ = self.write_config(tmp)
            mountinfo = mi(1, 0, "8:1", "/", "/", "rw", "ext4", "/dev/sda1")
            def fake(argv, **kwargs):
                payload = raw_payload("/home")
                payload["scan_started_unix"] = 100
                payload["scan_duration_sec"] = 999.9
                pathlib.Path(argv[argv.index("--out") + 1]).write_text(json.dumps(payload), encoding="utf-8")
                return scan_runner.CompletedScan(0, "", "")

            result = scan_runner.run_once(config_path, mountinfo_reader=lambda: mountinfo, scanner_runner=fake, clock=Clock(145))
            payload = json.loads(result.snapshot_path.read_text(encoding="utf-8"))

            self.assertEqual(payload["scan_started_unix"], 100)
            self.assertEqual(payload["scan_finished_unix"], 145)
            self.assertEqual(payload["scan_duration_sec"], 45)
            self.assertIsInstance(payload["scan_duration_sec"], int)
            assert_schema_v1_snapshot_contract(self, payload)

    def test_negative_or_inconsistent_timing_is_total_failure(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            config_path, _ = self.write_config(tmp)
            mountinfo = mi(1, 0, "8:1", "/", "/", "rw", "ext4", "/dev/sda1")
            def fake(argv, **kwargs):
                payload = raw_payload("/home")
                payload["scan_started_unix"] = 200
                pathlib.Path(argv[argv.index("--out") + 1]).write_text(json.dumps(payload), encoding="utf-8")
                return scan_runner.CompletedScan(0, "", "")

            result = scan_runner.run_once(config_path, mountinfo_reader=lambda: mountinfo, scanner_runner=fake, clock=Clock(199))

            self.assertEqual(result.status, "failed")
            self.assertFalse((tmp / "data" / "scan-status.json").exists())
            self.assertEqual(list((tmp / "data" / "snapshots").glob("*.json")), [])

    def test_invalid_tree_and_file_kinds_are_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            config_path, _ = self.write_config(tmp)
            mountinfo = mi(1, 0, "8:1", "/", "/", "rw", "ext4", "/dev/sda1")
            cases = []
            p1 = raw_payload("/home")
            p1["mounts"][0]["tree"]["kind"] = "banana"
            cases.append(p1)
            p2 = raw_payload("/home")
            p2["mounts"][0]["tree"]["children"] = ["not-object"]
            cases.append(p2)
            p3 = raw_payload("/home")
            p3["top_files"] = ["not-object"]
            cases.append(p3)
            p4 = raw_payload("/home")
            p4["stale"][0]["kind"] = "directory"
            cases.append(p4)
            for idx, payload in enumerate(cases):
                with self.subTest(idx=idx):
                    def fake(argv, **kwargs):
                        pathlib.Path(argv[argv.index("--out") + 1]).write_text(json.dumps(payload), encoding="utf-8")
                        return scan_runner.CompletedScan(0, "", "")
                    result = scan_runner.run_once(config_path, mountinfo_reader=lambda: mountinfo, scanner_runner=fake, clock=Clock(300 + idx))
                    self.assertEqual(result.status, "failed")

    def test_config_path_defaults_and_absolute_path_validation(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            path = tmp / "scanner.yaml"
            path.write_text(json.dumps({"server_id": "host-a", "scanner_path": "/bin/hstscan"}), encoding="utf-8")
            cfg = scan_runner.load_config(path)
            self.assertEqual(cfg.data_dir, pathlib.Path("/var/lib/storage-viz"))
            self.assertEqual(cfg.run_dir, pathlib.Path("/run/storage-viz"))

            for key in ("scanner_path", "data_dir", "run_dir"):
                with self.subTest(key=key):
                    bad = {"server_id": "host-a", "scanner_path": "/bin/hstscan"}
                    bad[key] = "relative/path"
                    bad_path = tmp / f"bad-{key}.json"
                    bad_path.write_text(json.dumps(bad), encoding="utf-8")
                    with self.assertRaises(ValueError):
                        scan_runner.load_config(bad_path)

    def test_managed_final_data_or_run_dir_symlink_is_rejected_but_ancestor_symlink_allowed(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            real_parent = tmp / "real-parent"
            real_parent.mkdir()
            ancestor_link = tmp / "ancestor-link"
            ancestor_link.symlink_to(real_parent, target_is_directory=True)
            good_config, _ = self.write_config(tmp, data_dir=str(ancestor_link / "data"), run_dir=str(ancestor_link / "run"))
            mountinfo = mi(1, 0, "8:1", "/", "/", "rw", "ext4", "/dev/sda1")
            def good(argv, **kwargs):
                pathlib.Path(argv[argv.index("--out") + 1]).write_text(json.dumps(raw_payload("/home")), encoding="utf-8")
                return scan_runner.CompletedScan(0, "", "")
            self.assertEqual(scan_runner.run_once(good_config, mountinfo_reader=lambda: mountinfo, scanner_runner=good, clock=Clock(400)).status, "complete")

            real_data = tmp / "real-data"
            real_data.mkdir()
            data_link = tmp / "data-link"
            data_link.symlink_to(real_data, target_is_directory=True)
            bad_data_config, _ = self.write_config(tmp, data_dir=str(data_link), run_dir=str(tmp / "run2"))
            self.assertEqual(scan_runner.run_once(bad_data_config, mountinfo_reader=lambda: mountinfo, scanner_runner=good, clock=Clock(401)).status, "failed")

            real_run = tmp / "real-run"
            real_run.mkdir()
            run_link = tmp / "run-link"
            run_link.symlink_to(real_run, target_is_directory=True)
            bad_run_config, _ = self.write_config(tmp, data_dir=str(tmp / "data2"), run_dir=str(run_link))
            self.assertEqual(scan_runner.run_once(bad_run_config, mountinfo_reader=lambda: mountinfo, scanner_runner=good, clock=Clock(402)).status, "failed")


    def test_malformed_users_or_blocked_raw_shapes_are_total_failure(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            config_path, _ = self.write_config(tmp)
            mountinfo = mi(1, 0, "8:1", "/", "/", "rw", "ext4", "/dev/sda1")
            cases = []
            p1 = raw_payload("/home")
            p1["users"] = {"uid": 0}
            cases.append(p1)
            p2 = raw_payload("/home")
            p2["users"] = ["not-object"]
            cases.append(p2)
            p3 = raw_payload("/home")
            p3["users"] = [{"uid": "0", "name": "root", "bytes": 1, "files": 1, "by_mount": {"/home": 1}}]
            cases.append(p3)
            p4 = raw_payload("/home")
            p4["users"] = [{"uid": 0, "name": "root", "bytes": 1, "files": 1, "by_mount": {"relative": 1}}]
            cases.append(p4)
            p5 = raw_payload("/home")
            p5["blocked"] = {"path": "/home/secret"}
            cases.append(p5)
            p6 = raw_payload("/home")
            p6["blocked"] = ["not-object"]
            cases.append(p6)
            p7 = raw_payload("/home")
            p7["blocked"] = [{"path": 42, "reason": "EACCES"}]
            cases.append(p7)
            p8 = raw_payload("/home")
            p8["blocked"] = [{"path": "/home/secret", "reason": "x" * 128}]
            cases.append(p8)

            for idx, payload in enumerate(cases):
                with self.subTest(idx=idx):
                    def fake(argv, **kwargs):
                        pathlib.Path(argv[argv.index("--out") + 1]).write_text(json.dumps(payload), encoding="utf-8")
                        return scan_runner.CompletedScan(0, "", "")
                    result = scan_runner.run_once(config_path, mountinfo_reader=lambda: mountinfo, scanner_runner=fake, clock=Clock(700 + idx))
                    self.assertEqual(result.status, "failed")
                    self.assertFalse((tmp / "data" / "scan-status.json").exists())

    def test_valid_users_and_blocked_raw_shapes_are_preserved(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            config_path, _ = self.write_config(tmp)
            mountinfo = mi(1, 0, "8:1", "/", "/", "rw", "ext4", "/dev/sda1")
            payload = raw_payload("/home")
            payload["users"] = [{"uid": 0, "name": "root", "bytes": 12, "files": 2, "by_mount": {"/home": 12}}]
            payload["blocked"] = [{"path": "/home/secret", "reason": "EACCES"}]
            def fake(argv, **kwargs):
                pathlib.Path(argv[argv.index("--out") + 1]).write_text(json.dumps(payload), encoding="utf-8")
                return scan_runner.CompletedScan(0, "", "")

            result = scan_runner.run_once(config_path, mountinfo_reader=lambda: mountinfo, scanner_runner=fake, clock=Clock(800))
            published = json.loads(result.snapshot_path.read_text(encoding="utf-8"))

            self.assertEqual(result.status, "complete")
            self.assertEqual(published["users"], payload["users"])
            self.assertEqual(published["blocked"], payload["blocked"])
            self.assertEqual(published["selected_roots"][0]["blocked_count"], 1)


    def test_malformed_copied_raw_fields_preserve_previous_good_generation_and_status(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            config_path, _ = self.write_config(tmp)
            mountinfo = mi(1, 0, "8:1", "/", "/", "rw", "ext4", "/dev/sda1")
            def good(argv, **kwargs):
                payload = raw_payload("/home")
                payload["users"] = [{"uid": 0, "name": "root", "bytes": 12, "files": 2, "by_mount": {"/home": 12}}]
                payload["blocked"] = [{"path": "/home/secret", "reason": "EACCES"}]
                pathlib.Path(argv[argv.index("--out") + 1]).write_text(json.dumps(payload), encoding="utf-8")
                return scan_runner.CompletedScan(0, "", "")
            first = scan_runner.run_once(config_path, mountinfo_reader=lambda: mountinfo, scanner_runner=good, clock=Clock(900))
            before_status = (tmp / "data" / "scan-status.json").read_text(encoding="utf-8")
            before_snapshot = first.snapshot_path.read_text(encoding="utf-8")

            def malformed(argv, **kwargs):
                payload = raw_payload("/home")
                payload["users"] = {"uid": 0}
                pathlib.Path(argv[argv.index("--out") + 1]).write_text(json.dumps(payload), encoding="utf-8")
                return scan_runner.CompletedScan(0, "", "")
            result = scan_runner.run_once(config_path, mountinfo_reader=lambda: mountinfo, scanner_runner=malformed, clock=Clock(901))

            self.assertEqual(result.status, "failed")
            self.assertEqual((tmp / "data" / "scan-status.json").read_text(encoding="utf-8"), before_status)
            self.assertEqual(first.snapshot_path.read_text(encoding="utf-8"), before_snapshot)
            self.assertEqual(sorted(p.name for p in (tmp / "data" / "snapshots").glob("*.json")), [first.snapshot_path.name])


    def test_adversarial_raw_schema_scalars_are_total_failure(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            config_path, _ = self.write_config(tmp)
            mountinfo = mi(1, 0, "8:1", "/", "/", "rw", "ext4", "/dev/sda1")
            cases = []
            for mutate in (
                lambda p: p.__setitem__("schema_version", True),
                lambda p: p.__setitem__("hostname", 123),
                lambda p: p.__setitem__("scanner_version", ""),
                lambda p: p.__setitem__("run_as_root", "false"),
                lambda p: p["mounts"][0].__setitem__("df_total", "1000"),
                lambda p: p["mounts"][0].__setitem__("df_used", True),
                lambda p: p["mounts"][0].__setitem__("df_use_pct", -1),
                lambda p: p["mounts"][0]["tree"].__setitem__("bytes", "123"),
                lambda p: p["mounts"][0]["tree"].__setitem__("name", 99),
                lambda p: p["mounts"][0]["tree"].__setitem__("files", True),
            ):
                payload = raw_payload("/home")
                mutate(payload)
                cases.append(payload)
            for idx, payload in enumerate(cases):
                with self.subTest(idx=idx):
                    def fake(argv, **kwargs):
                        pathlib.Path(argv[argv.index("--out") + 1]).write_text(json.dumps(payload), encoding="utf-8")
                        return scan_runner.CompletedScan(0, "", "")
                    result = scan_runner.run_once(config_path, mountinfo_reader=lambda: mountinfo, scanner_runner=fake, clock=Clock(1000 + idx))
                    self.assertEqual(result.status, "failed")

    def test_recursive_tree_scalars_and_byte_invariants_are_validated(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            config_path, _ = self.write_config(tmp)
            mountinfo = mi(1, 0, "8:1", "/", "/", "rw", "ext4", "/dev/sda1")
            cases = []
            child_bad_uid = raw_payload("/home")
            child_bad_uid["mounts"][0]["tree"] = {
                "name": "/home", "kind": "directory", "bytes": 10, "files": 1, "uid": 0, "mtime": 1, "other_bytes": 0,
                "children": [{"name": "alice", "kind": "directory", "bytes": 10, "files": 1, "uid": True, "mtime": 1, "other_bytes": 0}],
            }
            cases.append(child_bad_uid)
            bad_invariant = raw_payload("/home")
            bad_invariant["mounts"][0]["tree"] = {
                "name": "/home", "kind": "directory", "bytes": 11, "files": 1, "uid": 0, "mtime": 1, "other_bytes": 0,
                "children": [{"name": "alice", "kind": "directory", "bytes": 10, "files": 1, "uid": 1000, "mtime": 1, "other_bytes": 0}],
            }
            cases.append(bad_invariant)
            bad_other_bytes = raw_payload("/home")
            bad_other_bytes["mounts"][0]["tree"]["other_bytes"] = True
            cases.append(bad_other_bytes)
            for idx, payload in enumerate(cases):
                with self.subTest(idx=idx):
                    def fake(argv, **kwargs):
                        pathlib.Path(argv[argv.index("--out") + 1]).write_text(json.dumps(payload), encoding="utf-8")
                        return scan_runner.CompletedScan(0, "", "")
                    result = scan_runner.run_once(config_path, mountinfo_reader=lambda: mountinfo, scanner_runner=fake, clock=Clock(1100 + idx))
                    self.assertEqual(result.status, "failed")

    def test_file_rows_require_documented_fields_and_reject_bool_integers(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            config_path, _ = self.write_config(tmp)
            mountinfo = mi(1, 0, "8:1", "/", "/", "rw", "ext4", "/dev/sda1")
            cases = []
            p1 = raw_payload("/home")
            del p1["top_files"][0]["owner"]
            cases.append(p1)
            p2 = raw_payload("/home")
            p2["top_files"][0]["bytes"] = True
            cases.append(p2)
            p3 = raw_payload("/home")
            del p3["stale"][0]["age_days"]
            cases.append(p3)
            p4 = raw_payload("/home")
            p4["stale"][0]["mtime"] = False
            cases.append(p4)
            for idx, payload in enumerate(cases):
                with self.subTest(idx=idx):
                    def fake(argv, **kwargs):
                        pathlib.Path(argv[argv.index("--out") + 1]).write_text(json.dumps(payload), encoding="utf-8")
                        return scan_runner.CompletedScan(0, "", "")
                    result = scan_runner.run_once(config_path, mountinfo_reader=lambda: mountinfo, scanner_runner=fake, clock=Clock(1200 + idx))
                    self.assertEqual(result.status, "failed")

    def test_unattributable_blocked_paths_are_ignored_not_publication_failures(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            config_path, _ = self.write_config(tmp)
            mountinfo = "\n".join([
                mi(1, 0, "8:1", "/", "/", "rw", "ext4", "/dev/sda1"),
                mi(2, 1, "8:2", "/", "/data", "rw", "xfs", "/dev/sdb1"),
            ])
            blocked = [
                {"path": "/data/secret", "reason": "EACCES"},
                {"path": "/database/not-a-data-child", "reason": "EACCES"},
                {"path": "/opt/not-selected", "reason": "EACCES"},
                {"path": "relative/path", "reason": "EACCES"},
                {"path": "", "reason": "EACCES"},
                {"reason": "EACCES"},
            ]
            def fake(argv, **kwargs):
                pathlib.Path(argv[argv.index("--out") + 1]).write_text(
                    json.dumps(raw_payload_many(["/home", "/data"], blocked=blocked)), encoding="utf-8"
                )
                return scan_runner.CompletedScan(0, "", "")

            result = scan_runner.run_once(config_path, mountinfo_reader=lambda: mountinfo, scanner_runner=fake, clock=Clock(1300))
            payload = json.loads(result.snapshot_path.read_text(encoding="utf-8"))
            roots = {root["scan_root"]: root for root in payload["selected_roots"]}

            self.assertEqual(result.status, "complete")
            self.assertEqual(roots["/home"]["blocked_count"], 0)
            self.assertEqual(roots["/data"]["blocked_count"], 1)
            self.assertEqual(payload["blocked"], blocked)

    def test_blocked_container_corruption_and_scalar_overflow_still_fail(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            config_path, _ = self.write_config(tmp)
            mountinfo = mi(1, 0, "8:1", "/", "/", "rw", "ext4", "/dev/sda1")
            cases = []
            p1 = raw_payload("/home")
            p1["blocked"] = {"path": "/home/secret"}
            cases.append(p1)
            p2 = raw_payload("/home")
            p2["blocked"] = ["not-object"]
            cases.append(p2)
            p3 = raw_payload("/home")
            p3["blocked"] = [{"path": 42, "reason": "EACCES"}]
            cases.append(p3)
            p4 = raw_payload("/home")
            p4["blocked"] = [{"path": "/home/secret", "reason": "x" * 128}]
            cases.append(p4)
            for idx, payload in enumerate(cases):
                with self.subTest(idx=idx):
                    def fake(argv, **kwargs):
                        pathlib.Path(argv[argv.index("--out") + 1]).write_text(json.dumps(payload), encoding="utf-8")
                        return scan_runner.CompletedScan(0, "", "")
                    result = scan_runner.run_once(config_path, mountinfo_reader=lambda: mountinfo, scanner_runner=fake, clock=Clock(1400 + idx))
                    self.assertEqual(result.status, "failed")


    def test_null_tree_children_is_total_failure_and_preserves_previous_good_but_omitted_is_valid(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            config_path, _ = self.write_config(tmp)
            mountinfo = mi(1, 0, "8:1", "/", "/", "rw", "ext4", "/dev/sda1")
            def good(argv, **kwargs):
                payload = raw_payload("/home")
                payload["mounts"][0]["tree"].pop("children", None)
                pathlib.Path(argv[argv.index("--out") + 1]).write_text(json.dumps(payload), encoding="utf-8")
                return scan_runner.CompletedScan(0, "", "")
            first = scan_runner.run_once(config_path, mountinfo_reader=lambda: mountinfo, scanner_runner=good, clock=Clock(1500))
            before_status = (tmp / "data" / "scan-status.json").read_text(encoding="utf-8")
            before_snapshot = first.snapshot_path.read_text(encoding="utf-8")
            self.assertEqual(first.status, "complete")

            def null_children(argv, **kwargs):
                payload = raw_payload("/home")
                payload["mounts"][0]["tree"]["children"] = None
                pathlib.Path(argv[argv.index("--out") + 1]).write_text(json.dumps(payload), encoding="utf-8")
                return scan_runner.CompletedScan(0, "", "")
            result = scan_runner.run_once(config_path, mountinfo_reader=lambda: mountinfo, scanner_runner=null_children, clock=Clock(1501))

            self.assertEqual(result.status, "failed")
            self.assertEqual((tmp / "data" / "scan-status.json").read_text(encoding="utf-8"), before_status)
            self.assertEqual(first.snapshot_path.read_text(encoding="utf-8"), before_snapshot)

    def test_blocked_reason_is_required_even_when_path_is_ignored_for_attribution(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            config_path, _ = self.write_config(tmp)
            mountinfo = mi(1, 0, "8:1", "/", "/", "rw", "ext4", "/dev/sda1")
            cases = []
            for row in (
                {"path": "/home/secret"},
                {"path": "/home/secret", "reason": None},
                {"path": "/home/secret", "reason": ""},
                {"path": "/home/secret", "reason": "x" * 128},
                {"path": "relative", "reason": None},
                {"reason": None},
            ):
                payload = raw_payload("/home")
                payload["blocked"] = [row]
                cases.append(payload)
            for idx, payload in enumerate(cases):
                with self.subTest(idx=idx):
                    def fake(argv, **kwargs):
                        pathlib.Path(argv[argv.index("--out") + 1]).write_text(json.dumps(payload), encoding="utf-8")
                        return scan_runner.CompletedScan(0, "", "")
                    result = scan_runner.run_once(config_path, mountinfo_reader=lambda: mountinfo, scanner_runner=fake, clock=Clock(1600 + idx))
                    self.assertEqual(result.status, "failed")

    def test_malformed_blocked_path_with_valid_reason_is_preserved_and_ignored_for_attribution(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            config_path, _ = self.write_config(tmp)
            mountinfo = mi(1, 0, "8:1", "/", "/", "rw", "ext4", "/dev/sda1")
            blocked = [
                {"path": "relative", "reason": "EACCES"},
                {"path": "", "reason": "EACCES"},
                {"reason": "EACCES"},
                {"path": "/not-selected", "reason": "EACCES"},
            ]
            def fake(argv, **kwargs):
                payload = raw_payload("/home")
                payload["blocked"] = blocked
                pathlib.Path(argv[argv.index("--out") + 1]).write_text(json.dumps(payload), encoding="utf-8")
                return scan_runner.CompletedScan(0, "", "")

            result = scan_runner.run_once(config_path, mountinfo_reader=lambda: mountinfo, scanner_runner=fake, clock=Clock(1700))
            published = json.loads(result.snapshot_path.read_text(encoding="utf-8"))

            self.assertEqual(result.status, "complete")
            self.assertEqual(published["blocked"], blocked)
            self.assertEqual(published["selected_roots"][0]["blocked_count"], 0)


    def test_selected_root_bounded_counters_reject_10e18_and_preserve_previous_good(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            config_path, _ = self.write_config(tmp)
            mountinfo = mi(1, 0, "8:1", "/", "/", "rw", "ext4", "/dev/sda1")
            def good(argv, **kwargs):
                pathlib.Path(argv[argv.index("--out") + 1]).write_text(json.dumps(raw_payload("/home")), encoding="utf-8")
                return scan_runner.CompletedScan(0, "", "")
            first = scan_runner.run_once(config_path, mountinfo_reader=lambda: mountinfo, scanner_runner=good, clock=Clock(1800))
            before_status = (tmp / "data" / "scan-status.json").read_text(encoding="utf-8")
            before_snapshot = first.snapshot_path.read_text(encoding="utf-8")

            def make_payload(field):
                payload = raw_payload("/home")
                mount = payload["mounts"][0]
                if field == "scanned_bytes":
                    mount["scanned_bytes"] = 10**18
                    mount["tree"]["bytes"] = 10**18
                elif field == "scanned_files":
                    mount["scanned_files"] = 10**18
                    mount["tree"]["files"] = 10**18
                elif field == "scanned_dirs":
                    mount["scanned_dirs"] = 10**18
                elif field == "error_count":
                    mount["errors"] = 10**18
                return payload

            for idx, field in enumerate(("scanned_bytes", "scanned_files", "scanned_dirs", "error_count")):
                with self.subTest(field=field):
                    def fake(argv, **kwargs):
                        pathlib.Path(argv[argv.index("--out") + 1]).write_text(json.dumps(make_payload(field)), encoding="utf-8")
                        return scan_runner.CompletedScan(0, "", "")
                    result = scan_runner.run_once(config_path, mountinfo_reader=lambda: mountinfo, scanner_runner=fake, clock=Clock(1801 + idx))
                    self.assertEqual(result.status, "failed")
                    self.assertEqual((tmp / "data" / "scan-status.json").read_text(encoding="utf-8"), before_status)
                    self.assertEqual(first.snapshot_path.read_text(encoding="utf-8"), before_snapshot)

    def test_selected_root_bounded_counters_allow_10e18_minus_one_where_consistent(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            config_path, _ = self.write_config(tmp)
            mountinfo = mi(1, 0, "8:1", "/", "/", "rw", "ext4", "/dev/sda1")
            limit_value = 10**18 - 1
            def fake(argv, **kwargs):
                payload = raw_payload("/home")
                mount = payload["mounts"][0]
                mount["scanned_bytes"] = limit_value
                mount["scanned_files"] = limit_value
                mount["scanned_dirs"] = limit_value
                mount["tree"]["bytes"] = limit_value
                mount["tree"]["files"] = limit_value
                pathlib.Path(argv[argv.index("--out") + 1]).write_text(json.dumps(payload), encoding="utf-8")
                return scan_runner.CompletedScan(0, "", "")

            result = scan_runner.run_once(config_path, mountinfo_reader=lambda: mountinfo, scanner_runner=fake, clock=Clock(1900))
            root = json.loads(result.snapshot_path.read_text(encoding="utf-8"))["selected_roots"][0]

            self.assertEqual(result.status, "complete")
            self.assertEqual(root["scanned_bytes"], limit_value)
            self.assertEqual(root["scanned_files"], limit_value)
            self.assertEqual(root["scanned_dirs"], limit_value)


    def test_status_write_failure_does_not_prune_generation_referenced_by_durable_status(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            config_path, _ = self.write_config(tmp)
            mountinfo = mi(1, 0, "8:1", "/", "/", "rw", "ext4", "/dev/sda1")
            starts = iter((2000, 2001, 2002))
            def fake(argv, **kwargs):
                pathlib.Path(argv[argv.index("--out") + 1]).write_text(json.dumps(raw_payload("/home", started=next(starts))), encoding="utf-8")
                return scan_runner.CompletedScan(0, "", "")

            first = scan_runner.run_once(config_path, mountinfo_reader=lambda: mountinfo, scanner_runner=fake, clock=Clock(2005))
            second = scan_runner.run_once(config_path, mountinfo_reader=lambda: mountinfo, scanner_runner=fake, clock=Clock(2006))
            status_path = tmp / "data" / "scan-status.json"
            previous_status_text = status_path.read_text(encoding="utf-8")
            previous_status = json.loads(previous_status_text)
            referenced = [previous_status["generation"], *previous_status["retained_generations"]]

            original_write_json_atomic = scan_runner._write_json_atomic
            def failing_status_write(path, payload):
                if pathlib.Path(path).name == "scan-status.json":
                    raise OSError("injected status write failure")
                return original_write_json_atomic(path, payload)
            scan_runner._write_json_atomic = failing_status_write
            try:
                result = scan_runner.run_once(config_path, mountinfo_reader=lambda: mountinfo, scanner_runner=fake, clock=Clock(2002))
            finally:
                scan_runner._write_json_atomic = original_write_json_atomic

            self.assertEqual(result.status, "failed")
            self.assertEqual(status_path.read_text(encoding="utf-8"), previous_status_text)
            for generation in referenced:
                with self.subTest(generation=generation):
                    self.assertTrue((tmp / "data" / "snapshots" / generation).exists())
            current_status = json.loads(status_path.read_text(encoding="utf-8"))
            self.assertNotEqual(current_status["generation"], "host-a-2002-v1.json")
            self.assertTrue(first.snapshot_path.exists())
            self.assertTrue(second.snapshot_path.exists())


    def test_status_dir_fsync_failure_after_replace_rolls_back_previous_status_without_pruning(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            config_path, _ = self.write_config(tmp)
            mountinfo = mi(1, 0, "8:1", "/", "/", "rw", "ext4", "/dev/sda1")
            starts = iter((2100, 2101, 2102))
            def fake(argv, **kwargs):
                pathlib.Path(argv[argv.index("--out") + 1]).write_text(json.dumps(raw_payload("/home", started=next(starts))), encoding="utf-8")
                return scan_runner.CompletedScan(0, "", "")

            first = scan_runner.run_once(config_path, mountinfo_reader=lambda: mountinfo, scanner_runner=fake, clock=Clock(2105))
            second = scan_runner.run_once(config_path, mountinfo_reader=lambda: mountinfo, scanner_runner=fake, clock=Clock(2106))
            status_path = tmp / "data" / "scan-status.json"
            snapshots_dir = tmp / "data" / "snapshots"
            previous_status_text = status_path.read_text(encoding="utf-8")
            previous_status = json.loads(previous_status_text)
            previous_referenced = [previous_status["generation"], *previous_status["retained_generations"]]

            original_fsync_dir = scan_runner._fsync_dir
            fail_once = {"armed": True}
            def fail_status_dir_once(path):
                if fail_once["armed"] and pathlib.Path(path) == tmp / "data":
                    fail_once["armed"] = False
                    raise OSError("injected post-replace status dir fsync failure")
                return original_fsync_dir(path)
            scan_runner._fsync_dir = fail_status_dir_once
            try:
                result = scan_runner.run_once(config_path, mountinfo_reader=lambda: mountinfo, scanner_runner=fake, clock=Clock(2107))
            finally:
                scan_runner._fsync_dir = original_fsync_dir

            self.assertEqual(result.status, "failed")
            self.assertIn("injected post-replace", result.error)
            self.assertEqual(status_path.read_text(encoding="utf-8"), previous_status_text)
            visible_status = json.loads(status_path.read_text(encoding="utf-8"))
            visible_referenced = [visible_status["generation"], *visible_status["retained_generations"]]
            for generation in set(previous_referenced + visible_referenced):
                with self.subTest(generation=generation):
                    self.assertTrue((snapshots_dir / generation).exists())
            self.assertTrue(first.snapshot_path.exists())
            self.assertTrue(second.snapshot_path.exists())
            self.assertTrue((snapshots_dir / "host-a-2102-v1.json").exists())
            leftovers = [p.name for p in (tmp / "data").iterdir() if p.name.startswith(".scan-status.json")]
            self.assertEqual(leftovers, [])

    def test_lock_conflict_exits_without_invoking_scanner(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            config_path, _ = self.write_config(tmp)
            (tmp / "run").mkdir()
            lock_fd = os.open(tmp / "run" / "scan.lock", os.O_CREAT | os.O_RDWR, 0o640)
            try:
                self.assertTrue(scan_runner.try_lock_fd(lock_fd))
                def not_called(*args, **kwargs):
                    self.fail("scanner must not be invoked on lock conflict")
                result = scan_runner.run_once(config_path, mountinfo_reader=lambda: "", scanner_runner=not_called, clock=Clock(200))
                self.assertEqual(result.status, "lock-conflict")
            finally:
                os.close(lock_fd)

    def test_invalid_raw_json_does_not_replace_previous_good_status(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            config_path, _ = self.write_config(tmp)
            mountinfo = mi(1, 0, "8:1", "/", "/", "rw", "ext4", "/dev/sda1")
            def good(argv, **kwargs):
                pathlib.Path(argv[argv.index("--out") + 1]).write_text(json.dumps(raw_payload("/home")), encoding="utf-8")
                return scan_runner.CompletedScan(0, "", "")
            scan_runner.run_once(config_path, mountinfo_reader=lambda: mountinfo, scanner_runner=good, clock=Clock(200))
            before_status = (tmp / "data" / "scan-status.json").read_text(encoding="utf-8")
            def invalid(argv, **kwargs):
                pathlib.Path(argv[argv.index("--out") + 1]).write_text("not-json", encoding="utf-8")
                return scan_runner.CompletedScan(0, "", "")
            result = scan_runner.run_once(config_path, mountinfo_reader=lambda: mountinfo, scanner_runner=invalid, clock=Clock(201))
            self.assertEqual(result.status, "failed")
            self.assertEqual((tmp / "data" / "scan-status.json").read_text(encoding="utf-8"), before_status)


if __name__ == "__main__":
    unittest.main()
