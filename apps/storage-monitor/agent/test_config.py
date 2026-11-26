import hashlib
import json
import os
import pathlib
import stat
import tempfile
import unittest

from agent.storage_viz_agent import config, mount_policy

def fake_lstat_result(mode, major=8, minor=1):
    return os.stat_result((mode, 0, os.makedev(major, minor), 0, 0, 0, 0, 0, 0, 0))

def mi(mid, parent, dev, root, mountpoint, options, fstype, source, super_options="rw"):
    return f"{mid} {parent} {dev} {root} {mountpoint} {options} - {fstype} {source} {super_options}"


class TestHostConfig(unittest.TestCase):
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
                        config.load_config(path)
            path, _ = self.write_config(tmp, surprise=True)
            with self.assertRaises(ValueError):
                config.load_config(path)

    def test_config_validation_and_canonical_sha256_config_digest(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            path, data = self.write_config(tmp)
            pretty = tmp / "pretty.yaml"
            pretty.write_text(json.dumps(data, indent=2), encoding="utf-8")

            cfg1 = config.load_config(path)
            cfg2 = config.load_config(pretty)
            expected = hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

            self.assertEqual(cfg1.config_digest, expected)
            self.assertEqual(cfg2.config_digest, expected)
            self.assertEqual(cfg1.server_id, "host-a")
            self.assertGreaterEqual(cfg1.threads, 1)

    def test_verified_root_directory_paths_synthesizes_only_same_device_data_directory(self):
        entries = mount_policy.parse_mountinfo(mi(1, 0, "8:1", "/", "/", "rw", "ext4", "/dev/sda1"))
        calls = []
        def fake_lstat(path):
            calls.append(path)
            if path == "/data":
                return fake_lstat_result(stat.S_IFDIR | 0o755, 8, 1)
            if path == "/data1/dataset":
                self.fail("must not probe arbitrary data-like paths")
            raise FileNotFoundError(path)

        self.assertEqual(mount_policy.verified_root_directory_paths(entries, lstat=fake_lstat), ("/data",))
        self.assertEqual(calls, ["/data"])

    def test_verified_root_directory_paths_treats_missing_data_as_absent(self):
        entries = mount_policy.parse_mountinfo(mi(1, 0, "8:1", "/", "/", "rw", "ext4", "/dev/sda1"))

        self.assertEqual(mount_policy.verified_root_directory_paths(entries, lstat=lambda path: (_ for _ in ()).throw(FileNotFoundError(path))), ())

    def test_verified_root_directory_paths_rejects_non_directory_data(self):
        entries = mount_policy.parse_mountinfo(mi(1, 0, "8:1", "/", "/", "rw", "ext4", "/dev/sda1"))

        self.assertEqual(
            mount_policy.verified_root_directory_paths(entries, lstat=lambda path: fake_lstat_result(stat.S_IFREG | 0o644, 8, 1)),
            (),
        )

    def test_verified_root_directory_paths_rejects_same_device_symlink_data(self):
        entries = mount_policy.parse_mountinfo(mi(1, 0, "8:1", "/", "/", "rw", "ext4", "/dev/sda1"))

        self.assertEqual(
            mount_policy.verified_root_directory_paths(entries, lstat=lambda path: fake_lstat_result(stat.S_IFLNK | 0o777, 8, 1)),
            (),
        )

    def test_verified_root_directory_paths_rejects_different_device_data(self):
        entries = mount_policy.parse_mountinfo(mi(1, 0, "8:1", "/", "/", "rw", "ext4", "/dev/sda1"))

        self.assertEqual(
            mount_policy.verified_root_directory_paths(entries, lstat=lambda path: fake_lstat_result(stat.S_IFDIR | 0o755, 8, 2)),
            (),
        )

    def test_verified_root_directory_paths_refuses_exact_explicit_local_data_mount(self):
        entries = mount_policy.parse_mountinfo("\n".join([
            mi(1, 0, "8:1", "/", "/", "rw", "ext4", "/dev/sda1"),
            mi(2, 1, "8:2", "/", "/data", "rw", "xfs", "/dev/sdb1"),
        ]))
        def not_called(path):
            self.fail("exact /data mount must prevent synthetic probing")

        self.assertEqual(mount_policy.verified_root_directory_paths(entries, lstat=not_called), ())

    def test_verified_root_directory_paths_refuses_exact_prohibited_data_mount(self):
        entries = mount_policy.parse_mountinfo("\n".join([
            mi(1, 0, "8:1", "/", "/", "rw", "ext4", "/dev/sda1"),
            mi(2, 1, "0:2", "/", "/data", "rw", "nfs", "server:/data"),
        ]))
        def not_called(path):
            self.fail("exact prohibited /data mount must prevent synthetic probing")

        self.assertEqual(mount_policy.verified_root_directory_paths(entries, lstat=not_called), ())

    def test_verified_root_directory_paths_refuses_exact_unsupported_data_mount(self):
        entries = mount_policy.parse_mountinfo("\n".join([
            mi(1, 0, "8:1", "/", "/", "rw", "ext4", "/dev/sda1"),
            mi(2, 1, "9:9", "/", "/data", "rw", "weirdfs", "weird"),
        ]))
        def not_called(path):
            self.fail("exact unsupported /data mount must prevent synthetic probing")

        self.assertEqual(mount_policy.verified_root_directory_paths(entries, lstat=not_called), ())

    def test_verified_root_directory_paths_treats_lstat_failure_as_absent(self):
        entries = mount_policy.parse_mountinfo(mi(1, 0, "8:1", "/", "/", "rw", "ext4", "/dev/sda1"))

        self.assertEqual(mount_policy.verified_root_directory_paths(entries, lstat=lambda path: (_ for _ in ()).throw(PermissionError(path))), ())

