import json
import pathlib
import tempfile
import unittest

from collector import inventory


class InventoryTests(unittest.TestCase):
    def write_config(self, data):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = pathlib.Path(tmp.name) / "servers.yaml"
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def valid_config(self):
        return {
            "capacity_thresholds": {
                "warning_used_pct": 80,
                "critical_used_pct": 92,
                "warning_free_bytes": 549755813888,
                "critical_free_bytes": 137438953472,
            },
            "servers": [
                {
                    "id": "alpha-1",
                    "display_name": "Alpha One",
                    "order": 20,
                    "host": "alpha.example.test",
                    "port": 22,
                    "enabled": True,
                    "username": "monitoring",
                    "identity_file": "/etc/storage-viz/keys/alpha_ed25519",
                    "known_hosts_file": "/etc/storage-viz/known_hosts",
                    "scanner": {
                        "server_id": "alpha-1",
                        "scanner_path": "/opt/storage-viz/scanner/hstscan",
                        "data_dir": "/var/lib/storage-viz",
                        "run_dir": "/run/storage-viz",
                        "threads": 4,
                        "prune_home_mb": 50,
                        "prune_data_mb": 100,
                        "top": 200,
                        "stale_days": 180,
                    },
                },
                {
                    "id": "beta_2",
                    "display_name": "Beta Two",
                    "order": 10,
                    "host": "192.0.2.10",
                    "port": 2200,
                    "enabled": False,
                    "username": "monitoring",
                    "identity_file": "/etc/storage-viz/keys/beta_ed25519",
                    "known_hosts_file": "/etc/storage-viz/known_hosts",
                    "scanner": {
                        "server_id": "beta_2",
                        "scanner_path": "/opt/storage-viz/scanner/hstscan",
                    },
                },
            ],
        }

    def load(self, data):
        return inventory.load_inventory(self.write_config(data))

    def test_preserves_configured_server_order_and_threshold_defaults(self):
        cfg = self.valid_config()
        cfg.pop("capacity_thresholds")
        inv = self.load(cfg)
        self.assertEqual([server.id for server in inv.servers], ["alpha-1", "beta_2"])
        self.assertEqual([server.order for server in inv.servers], [20, 10])
        self.assertEqual([server.username for server in inv.servers], ["monitoring", "monitoring"])
        self.assertEqual(inv.capacity_thresholds.warning_used_pct, 80)
        self.assertEqual(inv.capacity_thresholds.critical_used_pct, 92)
        self.assertEqual(inv.capacity_thresholds.warning_free_bytes, 549755813888)
        self.assertEqual(inv.capacity_thresholds.critical_free_bytes, 137438953472)

    def test_rejects_duplicate_ids_unsafe_host_and_unknown_security_keys(self):
        cfg = self.valid_config()
        cfg["servers"][1]["id"] = "alpha-1"
        cfg["servers"][1]["scanner"]["server_id"] = "alpha-1"
        with self.assertRaisesRegex(ValueError, "duplicate server id"):
            self.load(cfg)
        cfg = self.valid_config()
        cfg["servers"][0]["host"] = "alpha.example.test;rm -rf /"
        with self.assertRaisesRegex(ValueError, "host"):
            self.load(cfg)
        cfg = self.valid_config()
        cfg["servers"][0]["ProxyCommand"] = "ssh bastion"
        with self.assertRaisesRegex(ValueError, "unknown|forbidden"):
            self.load(cfg)

    def test_rejects_inline_secrets_and_mount_selection_overrides(self):
        for key, value in [("private_key", "-----BEGIN OPENSSH PRIVATE KEY-----"), ("password", "secret"), ("token", "abc")]:
            cfg = self.valid_config()
            cfg["servers"][0][key] = value
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "secret|forbidden|unknown"):
                self.load(cfg)
        cfg = self.valid_config()
        cfg["servers"][0]["scanner"]["targets"] = ["/home"]
        with self.assertRaisesRegex(ValueError, "forbidden"):
            self.load(cfg)

    def test_rejects_key_paths_in_repo_or_inline_content(self):
        cfg = self.valid_config()
        cfg["servers"][0]["identity_file"] = "relative/key"
        with self.assertRaisesRegex(ValueError, "absolute"):
            self.load(cfg)
        cfg = self.valid_config()
        cfg["servers"][0]["identity_file"] = "/Users/shchoi/.config/superpowers/worktrees/storage-viz/multiserver-storage-dashboard/viewer/key"
        with self.assertRaisesRegex(ValueError, "/etc/storage-viz"):
            self.load(cfg)
        cfg = self.valid_config()
        cfg["servers"][0]["known_hosts_file"] = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAA"
        with self.assertRaisesRegex(ValueError, "inline"):
            self.load(cfg)


    def test_rejects_duplicate_order_and_credentials_outside_etc_storage_viz(self):
        cfg = self.valid_config()
        cfg["servers"][1]["order"] = cfg["servers"][0]["order"]
        with self.assertRaisesRegex(ValueError, "duplicate order"):
            self.load(cfg)
        cfg = self.valid_config()
        cfg["servers"][0]["identity_file"] = "/opt/storage-viz/key"
        with self.assertRaisesRegex(ValueError, "/etc/storage-viz"):
            self.load(cfg)


    def test_rejects_noncanonical_inventory_paths_and_requires_monitoring_username(self):
        for field in ("identity_file", "known_hosts_file"):
            for bad in ("/etc/storage-viz/./key", "/etc/storage-viz/a//key", "/etc/storage-viz/a/../key", "/etc/storage-viz/key\n", "/etc/storage-viz/key/"):
                cfg = self.valid_config()
                cfg["servers"][0][field] = bad
                with self.subTest(field=field, bad=bad), self.assertRaisesRegex(ValueError, "canonical|control|trailing|/etc/storage-viz"):
                    self.load(cfg)
        for field in ("scanner_path", "data_dir", "run_dir"):
            for bad in ("/opt/storage-viz/./hstscan", "/var/lib/storage-viz//data", "/run/storage-viz/../bad", "/run/storage-viz/", "/run/storage-viz/bad\x00"):
                cfg = self.valid_config()
                cfg["servers"][0]["scanner"][field] = bad
                with self.subTest(field=field, bad=bad), self.assertRaisesRegex(ValueError, "canonical|control|trailing"):
                    self.load(cfg)
        for username in ("root", "admin", "storageviz", "operator"):
            cfg = self.valid_config()
            cfg["servers"][0]["username"] = username
            with self.subTest(username=username), self.assertRaisesRegex(ValueError, "monitoring"):
                self.load(cfg)

    def test_threshold_upper_bound_is_exclusive(self):
        t = inventory.CapacityThresholds.from_mapping({"warning_free_bytes": 10**18 - 1, "critical_free_bytes": 0})
        self.assertEqual(t.warning_free_bytes, 10**18 - 1)
        self.assertEqual(t.pressure(used_pct=1, free_bytes=10**18 - 1), "warning")
        with self.assertRaisesRegex(ValueError, "warning_free_bytes"):
            inventory.CapacityThresholds.from_mapping({"warning_free_bytes": 10**18, "critical_free_bytes": 0})
        with self.assertRaisesRegex(ValueError, "free_bytes"):
            t.pressure(used_pct=1, free_bytes=10**18)

    def test_threshold_pressure_boundaries_and_invalid_bool_int_ordering(self):
        t = inventory.CapacityThresholds.default()
        self.assertEqual(t.pressure(used_pct=79, free_bytes=549755813889), "normal")
        self.assertEqual(t.pressure(used_pct=80, free_bytes=999999999999), "warning")
        self.assertEqual(t.pressure(used_pct=1, free_bytes=549755813888), "warning")
        self.assertEqual(t.pressure(used_pct=92, free_bytes=999999999999), "critical")
        self.assertEqual(t.pressure(used_pct=1, free_bytes=137438953472), "critical")
        self.assertEqual(t.pressure(used_pct=95, free_bytes=1), "critical")
        with self.assertRaisesRegex(ValueError, "warning_used_pct"):
            inventory.CapacityThresholds.from_mapping({"warning_used_pct": True})
        with self.assertRaisesRegex(ValueError, "warning_used_pct"):
            inventory.CapacityThresholds.from_mapping({"warning_used_pct": 95, "critical_used_pct": 92})
        with self.assertRaisesRegex(ValueError, "warning_free_bytes"):
            inventory.CapacityThresholds.from_mapping({"warning_free_bytes": 1, "critical_free_bytes": 2})

    def test_scanner_digest_is_canonical_and_only_supported_keys(self):
        inv1 = self.load(self.valid_config())
        cfg2 = self.valid_config()
        cfg2["servers"][0]["scanner"] = dict(reversed(list(cfg2["servers"][0]["scanner"].items())))
        inv2 = self.load(cfg2)
        self.assertEqual(inv1.servers[0].scanner_digest, inv2.servers[0].scanner_digest)
        cfg = self.valid_config()
        cfg["servers"][0]["scanner"]["extra"] = 1
        with self.assertRaisesRegex(ValueError, "unknown"):
            self.load(cfg)


if __name__ == "__main__":
    unittest.main()
