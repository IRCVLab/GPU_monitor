#!/usr/bin/env python3
"""Regression checks for tracked storage-viz data fixtures."""
import json
import pathlib
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
GENERATOR = DATA_DIR / "gen_sample.py"


class DataFixtureTests(unittest.TestCase):
    def test_sample_generator_uses_repo_relative_output(self):
        text = GENERATOR.read_text(encoding="utf-8")
        self.assertNotIn("/home/shchoi/storage-viz", text)

    def test_hosts_manifest_shape(self):
        hosts_path = DATA_DIR / "hosts.json"
        hosts = json.loads(hosts_path.read_text(encoding="utf-8"))
        self.assertIsInstance(hosts, list)
        self.assertGreaterEqual(len(hosts), 1)
        defaults = [host for host in hosts if host.get("default") is True]
        self.assertEqual(len(defaults), 1)
        for host in hosts:
            with self.subTest(host=host):
                self.assertRegex(host["id"], r"^[A-Za-z0-9_.-]+$")
                self.assertTrue(host["label"])
                self.assertTrue(host["file"])
                self.assertNotIn("/", host["file"])
                self.assertFalse(host["file"].endswith(".json"))

    def test_sample_generator_writes_valid_schema_v1_fixture(self):
        text = GENERATOR.read_text(encoding="utf-8")
        if "/home/shchoi/storage-viz" in text:
            self.skipTest("generator still uses hardcoded output path")
        sample = DATA_DIR / "hinton.sample.json"
        if sample.exists():
            sample.unlink()
        result = subprocess.run(
            [sys.executable, str(GENERATOR)],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertIn("tree byte-consistency: OK", result.stdout)
        payload = json.loads(sample.read_text(encoding="utf-8"))
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["hostname"], "hinton")
        self.assertGreaterEqual(len(payload["mounts"]), 1)
        self.assertGreaterEqual(len(payload["users"]), 1)
        self.assertIsInstance(payload.get("top_files"), list)
        self.assertIsInstance(payload.get("stale"), list)


if __name__ == "__main__":
    unittest.main()
