from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[3]
BUILDER = REPO_ROOT / "apps/storage-monitor/deploy/build-dashboard-release.py"
APP_ROOT = Path("apps/storage-monitor")


class DashboardReleaseBuilderTest(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.repo = Path(self.tempdir.name) / "repo"
        self.output_dir = Path(self.tempdir.name) / "out"
        self.repo.mkdir()
        self._git("init", "-q")
        self._git("config", "user.email", "release-test@example.invalid")
        self._git("config", "user.name", "Release Test")
        self._write_fixture()
        self._git("add", ".")
        self._git("commit", "-q", "-m", "fixture")
        self.sha = self._git("rev-parse", "HEAD").stdout.strip()

    def _git(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=self.repo,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )

    def _write(self, relative: str, text: str) -> None:
        path = self.repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def _write_fixture(self) -> None:
        runtime_files = {
            "viewer/serve.py": "from collector.inventory import load_inventory\nfrom collector.jobs import RescanJobManager\nfrom collector.service import PollService\nfrom collector.store import CentralStore\nfrom collector.transport import OpenSshTransport\n",
            "viewer/app.js": "console.log('app');\n",
            "viewer/data-client.js": "export const dataClient = {};\n",
            "viewer/debug.html": "<html>debug</html>\n",
            "viewer/echarts.min.js": "/*! echarts */\n",
            "viewer/index.html": "<html><script src='app.js'></script></html>\n",
            "viewer/overview.js": "export const overview = {};\n",
            "viewer/selection.js": "export const selection = {};\n",
            "viewer/styles.css": "body { color: black; }\n",
            "viewer/tables.js": "export const tables = {};\n",
            "viewer/treemap.js": "export const treemap = {};\n",
            "viewer/users-chart.js": "export const users = {};\n",
            "collector/__init__.py": "",
            "collector/inventory.py": "SERVER_ID_RE = None\ndef load_inventory(): pass\n",
            "collector/jobs.py": "class RescanJobManager: pass\n",
            "collector/service.py": "class PollService: pass\n",
            "collector/snapshot.py": "class Snapshot: pass\n",
            "collector/store.py": "class CentralStore: pass\n",
            "collector/transport.py": "class OpenSshTransport: pass\n",
            "config/servers.example.yaml": "servers: []\n",
            "docs/schema-v1.md": "# Schema\n",
            "deploy/direct_proxy.py": "print('proxy')\n",
        }
        excluded_files = {
            "agent/scan_runner.py": "print('remote agent')\n",
            "scanner/hstscan.c": "int main(void){return 0;}\n",
            "deploy/install-agent.sh": "#!/bin/sh\n",
            "deploy/deploy-agent.sh": "#!/bin/sh\n",
            "deploy/systemd/storage-viz-scan.service.in": "scan\n",
            "deploy/systemd/storage-viz-scan.timer": "scan\n",
            "deploy/test_direct_proxy.py": "test\n",
            "viewer/viewer.test.js": "test\n",
            "viewer/test_serve.py": "test\n",
            "collector/test_store.py": "test\n",
            "data/hosts.json": "[]\n",
            "data/atlas.sample.json": "{}\n",
            "state/dashboard-state.json": "{}\n",
            "secrets/dashboard-token": "secret\n",
            ".env": "SECRET=1\n",
            "config/id_rsa": "private\n",
            "config/known_hosts": "host key\n",
            "dist/old.tar.gz": "old\n",
            "viewer/__pycache__/serve.cpython-312.pyc": "pyc\n",
        }
        for rel, text in {**runtime_files, **excluded_files}.items():
            self._write(str(APP_ROOT / rel), text)
        os.symlink("../data", self.repo / APP_ROOT / "viewer/data")

    def _run_builder(self, *extra: str) -> subprocess.CompletedProcess[str]:
        self.output_dir.mkdir(exist_ok=True)
        return subprocess.run(
            ["python3.12", str(BUILDER), "--sha", self.sha, "--output-dir", str(self.output_dir), *extra],
            cwd=self.repo,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def _build(self) -> tuple[Path, Path, dict[str, object], list[tarfile.TarInfo]]:
        result = self._run_builder()
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        archive = self.output_dir / f"storage-monitor-dashboard-{self.sha}.tar.gz"
        metadata = self.output_dir / f"storage-monitor-dashboard-{self.sha}.sha256.json"
        self.assertTrue(archive.is_file())
        self.assertTrue(metadata.is_file())
        with tarfile.open(archive, "r:gz") as tar:
            members = tar.getmembers()
            manifest_bytes = tar.extractfile("storage-monitor/RELEASE-MANIFEST.json").read()
        return archive, metadata, json.loads(manifest_bytes), members

    def test_builds_deterministic_central_runtime_archive_with_manifest_and_metadata(self) -> None:
        archive1, metadata_path, manifest, members = self._build()
        first_bytes = archive1.read_bytes()
        archive1.unlink()
        metadata_path.unlink()
        archive2, metadata_path2, manifest2, members2 = self._build()

        self.assertEqual(first_bytes, archive2.read_bytes())
        self.assertEqual(manifest, manifest2)
        self.assertEqual([m.name for m in members], [m.name for m in members2])

        member_names = [m.name for m in members]
        self.assertEqual(member_names, sorted(member_names))
        self.assertEqual(member_names[0], "storage-monitor/RELEASE-MANIFEST.json")
        self.assertIn("storage-monitor/viewer/serve.py", member_names)
        self.assertIn("storage-monitor/collector/store.py", member_names)
        self.assertIn("storage-monitor/deploy/direct_proxy.py", member_names)
        self.assertIn("storage-monitor/config/servers.example.yaml", member_names)
        self.assertIn("storage-monitor/docs/schema-v1.md", member_names)
        self.assertNotIn("storage-monitor/state/dashboard-state.json", member_names)
        self.assertNotIn("storage-monitor/secrets/dashboard-token", member_names)
        forbidden_fragments = (
            "/agent/", "/scanner/", "/data/", "/state/", "/secrets/", "test_", ".test.js", "install-agent",
            "deploy-agent", "storage-viz-scan", ".env", "id_rsa", "known_hosts", "__pycache__", "dist/",
        )
        self.assertFalse([name for name in member_names if any(fragment in name for fragment in forbidden_fragments)])
        self.assertTrue(all(name.startswith("storage-monitor/") for name in member_names))
        self.assertTrue(all(not m.issym() and not m.islnk() for m in members))
        self.assertTrue(all((m.mtime, m.uid, m.gid, m.uname, m.gname) == (0, 0, 0, "", "") for m in members))
        self.assertTrue(all(m.mode in {0o644, 0o755} for m in members))

        self.assertEqual(manifest["artifact_format_version"], 1)
        self.assertEqual(manifest["application_name"], "storage-monitor")
        self.assertEqual(manifest["schema_version"], 1)
        self.assertEqual(manifest["archive"], archive2.name)
        self.assertEqual(manifest["source_sha"], self.sha)
        included_paths = manifest["included_paths"]
        self.assertEqual(included_paths, sorted(included_paths))
        self.assertEqual(sorted([f"storage-monitor/{path}" for path in included_paths] + ["storage-monitor/RELEASE-MANIFEST.json"]), sorted(member_names))
        self.assertEqual(set(manifest["files"]), set(included_paths))
        for rel_path, file_hash in manifest["files"].items():
            source_bytes = (self.repo / APP_ROOT / rel_path).read_bytes()
            self.assertEqual(file_hash, hashlib.sha256(source_bytes).hexdigest())

        metadata = json.loads(metadata_path2.read_text(encoding="utf-8"))
        self.assertEqual(metadata["application_name"], "storage-monitor")
        self.assertEqual(metadata["schema_version"], 1)
        self.assertEqual(metadata["source_sha"], self.sha)
        self.assertEqual(metadata["archive"], archive2.name)
        self.assertEqual(metadata["sha256"], hashlib.sha256(archive2.read_bytes()).hexdigest())

    def test_fails_closed_for_invalid_sha_mismatched_head_and_dirty_worktree(self) -> None:
        self.output_dir.mkdir(exist_ok=True)
        invalid = subprocess.run(
            ["python3.12", str(BUILDER), "--sha", "not-a-sha", "--output-dir", str(self.output_dir)],
            cwd=self.repo,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertNotEqual(invalid.returncode, 0)
        self.assertIn("--sha", invalid.stderr)

        wrong_sha = "0" * 40 if self.sha != "0" * 40 else "1" * 40
        mismatch = subprocess.run(
            ["python3.12", str(BUILDER), "--sha", wrong_sha, "--output-dir", str(self.output_dir)],
            cwd=self.repo,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertNotEqual(mismatch.returncode, 0)
        self.assertIn("HEAD", mismatch.stderr)

        self._write(str(APP_ROOT / "viewer/app.js"), "dirty\n")
        dirty = self._run_builder()
        self.assertNotEqual(dirty.returncode, 0)
        self.assertIn("clean", dirty.stderr.lower())

        self._git("checkout", "--", str(APP_ROOT / "viewer/app.js"))
        self._write(str(APP_ROOT / "untracked.txt"), "untracked\n")
        untracked = self._run_builder()
        self.assertNotEqual(untracked.returncode, 0)
        self.assertIn("clean", untracked.stderr.lower())

    def test_rejects_required_tracked_symlinks_and_unsafe_paths(self) -> None:
        (self.repo / APP_ROOT / "viewer/app.js").unlink()
        os.symlink("serve.py", self.repo / APP_ROOT / "viewer/app.js")
        self._git("add", str(APP_ROOT / "viewer/app.js"))
        self._git("commit", "-q", "-m", "make required file symlink")
        self.sha = self._git("rev-parse", "HEAD").stdout.strip()

        symlink = self._run_builder()
        self.assertNotEqual(symlink.returncode, 0)
        self.assertIn("symlink", symlink.stderr.lower())

        self._git("rm", "-q", str(APP_ROOT / "viewer/app.js"))
        self._write(str(APP_ROOT / "viewer/app.js"), "console.log('restored');\n")
        self._git("add", str(APP_ROOT / "viewer/app.js"))
        self._git("commit", "-q", "-m", "restore app")
        self._write(str(APP_ROOT / "viewer/../evil.txt"), "evil\n")
        self._git("add", str(APP_ROOT / "evil.txt"))
        self._git("commit", "-q", "-m", "add extra safe path")
        self.sha = self._git("rev-parse", "HEAD").stdout.strip()
        ok = self._run_builder()
        self.assertEqual(ok.returncode, 0, ok.stderr + ok.stdout)


if __name__ == "__main__":
    unittest.main()
