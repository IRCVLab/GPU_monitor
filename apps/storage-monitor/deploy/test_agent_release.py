"""build-agent-release.py: deterministic bundles that the host updater accepts and self-tests."""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT))

from agent.bootstrap import fixtures, update  # noqa: E402

builder = fixtures.load_builder()
SOURCES = [
    "agent/__init__.py", "agent/block_media.py", "agent/mount_policy.py", "agent/scan_runner.py",
    "agent/release_cli.py", "agent/bin/storage-viz-agent", "agent/RELEASE-FLOOR.json",
    "storage_fstypes.py", "scanner/hstscan.c", "deploy/build-agent-release.py",
]


def git(repo, *args):
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false", *args],
        cwd=repo, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
    ).stdout.decode().strip()


@unittest.skipUnless(shutil.which("git"), "git is required")
class BuildAgentReleaseTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="sv-agent-build."))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = self.tmp / "repo"
        app = self.repo / "apps/storage-monitor"
        for rel in SOURCES:
            (app / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(APP_ROOT / rel, app / rel)
        git(self.repo, "init", "-q")
        self.commit("initial")
        self.hstscan = self.tmp / "hstscan"
        self.hstscan.write_bytes(b"\x7fELF fixed test binary")

    def commit(self, message, path=None, text=None):
        if path:
            target = self.repo / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", message)
        return git(self.repo, "rev-parse", "HEAD")

    def build(self, sha=None, name="out"):
        sha = sha or git(self.repo, "rev-parse", "HEAD")
        out = self.tmp / name
        metadata = builder.build(self.repo, sha, out, self.hstscan)
        return metadata, (out / builder.ARCHIVE_NAME).read_bytes()

    def test_same_sha_gives_the_same_bytes(self):
        first, first_bytes = self.build(name="one")
        second, second_bytes = self.build(name="two")
        self.assertEqual(first_bytes, second_bytes)
        self.assertEqual(first["sha256"], hashlib.sha256(first_bytes).hexdigest())
        self.assertEqual(first, second)

    def test_commits_without_agent_changes_reuse_the_bundle(self):
        agent_sha = git(self.repo, "rev-parse", "HEAD")
        _, before = self.build(name="before")
        self.commit("viewer only", "apps/storage-monitor/viewer/app.js", "x")
        self.commit("bootstrap only", "apps/storage-monitor/agent/bootstrap/gate.py", "x")
        self.commit("agent test only", "apps/storage-monitor/agent/test_new.py", "x")
        metadata, after = self.build(name="after")
        self.assertEqual(after, before)
        self.assertEqual(metadata["source_sha"], agent_sha)

    def test_agent_change_cuts_a_new_release(self):
        first, _ = self.build(name="one")
        sha = self.commit("agent change", "apps/storage-monitor/agent/mount_policy.py",
                          (APP_ROOT / "agent/mount_policy.py").read_text() + "\n# change\n")
        second, _ = self.build(name="two")
        self.assertEqual(second["source_sha"], sha)
        self.assertEqual(second["release_seq"], first["release_seq"] + 1)

    def test_rollback_floor_trails_twenty_agent_releases_and_honours_the_floor_file(self):
        shas = [git(self.repo, "rev-parse", "HEAD")]
        for index in range(21):
            shas.append(self.commit(f"agent {index}", "apps/storage-monitor/agent/scan_runner.py",
                                    (APP_ROOT / "agent/scan_runner.py").read_text() + f"\n# {index}\n"))
            self.commit(f"noise {index}", "README.md", str(index))
        metadata, _ = self.build()
        self.assertEqual(metadata["rollback_floor_seq"], int(git(self.repo, "rev-list", "--count", shas[1])))
        self.commit("schema boundary", "apps/storage-monitor/agent/RELEASE-FLOOR.json", '{"min_rollback_floor_seq": 60}\n')
        self.assertEqual(self.build(name="floor")[0]["rollback_floor_seq"], 60)

    def test_shallow_checkout_is_refused(self):
        clone = self.tmp / "shallow"
        subprocess.run(["git", "clone", "-q", "--depth", "1", f"file://{self.repo}", str(clone)], check=True)
        with self.assertRaises(builder.BuildError):
            builder.build(clone, git(clone, "rev-parse", "HEAD"), self.tmp / "out", self.hstscan)

    def test_bundle_layout_is_what_the_updater_accepts(self):
        metadata, data = self.build()
        members, manifest = update.read_bundle(data, metadata["source_sha"])
        self.assertEqual(sorted(members), [
            "agent/__init__.py", "agent/block_media.py", "agent/mount_policy.py", "agent/release_cli.py",
            "agent/scan_runner.py", "bin/hstscan", "bin/storage-viz-agent", "storage_fstypes.py",
        ])
        self.assertEqual(members["bin/storage-viz-agent"][1], 0o755)
        self.assertEqual(
            {key: manifest[key] for key in ("application", "source_sha", "release_seq", "rollback_floor_seq", "bootstrap_version")},
            {key: metadata[key] for key in ("application", "source_sha", "release_seq", "rollback_floor_seq", "bootstrap_version")},
        )
        self.assertEqual(metadata["size"], len(data))


@unittest.skipUnless(shutil.which("cc") and shutil.which("git"), "cc and git are required to build hstscan")
class ReleaseSelftestTest(unittest.TestCase):
    """The release entry point, as the updater's sandboxed self-test runs it."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="sv-selftest."))
        cls.hstscan = cls.tmp / "hstscan"
        subprocess.run(["cc", "-O2", "-pthread", "-std=gnu11", "-o", str(cls.hstscan), str(APP_ROOT / "scanner/hstscan.c")], check=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, True)

    def install(self, name, hstscan_bytes):
        release = self.tmp / name
        files = {}
        for rel in ("agent/__init__.py", "agent/block_media.py", "agent/mount_policy.py", "agent/scan_runner.py", "agent/release_cli.py"):
            files[rel] = ((APP_ROOT / rel).read_bytes(), 0o644)
        files["storage_fstypes.py"] = ((APP_ROOT / "storage_fstypes.py").read_bytes(), 0o644)
        files["bin/storage-viz-agent"] = ((APP_ROOT / "agent/bin/storage-viz-agent").read_bytes(), 0o755)
        files["bin/hstscan"] = (hstscan_bytes, 0o755)
        update.extract(files, release)
        config = self.tmp / f"{name}.yaml"
        config.write_text(json.dumps({"server_id": "host-a", "scanner_path": str(release / "bin/hstscan")}))
        return release, config

    def agent(self, release, *args):
        return subprocess.run([sys.executable, str(release / "bin/storage-viz-agent"), *args],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120, check=False)

    def test_selftest_passes_with_the_real_scanner(self):
        release, config = self.install("good", self.hstscan.read_bytes())
        done = self.agent(release, "selftest", "--config", str(config))
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.agent(release, "check-config", "--config", str(config)).returncode, 0)

    def test_selftest_fails_when_the_scanner_miscounts(self):
        liar = b"#!/bin/sh\nwhile [ \"$1\" != --out ]; do shift; done\nprintf '{\"mounts\":[{\"scanned_files\":3,\"scanned_dirs\":4,\"scanned_bytes\":1,\"errors\":0}]}' > \"$2\"\n"
        release, config = self.install("liar", liar)
        done = self.agent(release, "selftest", "--config", str(config))
        self.assertEqual(done.returncode, 1)
        self.assertIn(b"scanned_files: expected 4", done.stderr)

    def test_selftest_and_check_config_fail_on_a_bad_config(self):
        release, config = self.install("badcfg", self.hstscan.read_bytes())
        config.write_text('{"server_id": "host-a", "scanner_path": "/nonexistent", "roots": ["/"]}')
        self.assertEqual(self.agent(release, "selftest", "--config", str(config)).returncode, 1)
        self.assertEqual(self.agent(release, "check-config", "--config", str(config)).returncode, 1)


if __name__ == "__main__":
    unittest.main()
