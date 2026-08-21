from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest import mock


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


class DashboardReleaseActivationTest(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)
        self.release_root = self.root / "srv/storage-viz-dashboard/releases"
        self.app_path = self.root / "opt/storage-viz-dashboard"
        self.state_path = self.root / "var/lib/storage-viz-dashboard/activation-state.json"
        self.lock_path = self.root / "var/lib/storage-viz-dashboard/activation.lock"
        self.incoming = self.root / "var/lib/storage-viz-dashboard/incoming"
        self.gpu_sentinel = self.root / "srv/gpu-dashboard/SENTINEL"
        self.gpu_sentinel.parent.mkdir(parents=True)
        self.gpu_sentinel.write_bytes(b"gpu bytes must not change")
        self.sha = "0123456789abcdef0123456789abcdef01234567"
        self.old_sha = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        self.archive_name = f"storage-monitor-dashboard-{self.sha}.tar.gz"
        self.restart_calls: list[str] = []
        self.health_calls = 0
        self.module = self._load_module()

    def _load_module(self):
        import importlib.util
        import sys

        script = REPO_ROOT / "apps/storage-monitor/deploy/server/activate-dashboard-release.py"
        spec = importlib.util.spec_from_file_location("activate_dashboard_release", script)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module

    def _config(self, **overrides):
        kwargs = dict(
            release_root=self.release_root,
            app_path=self.app_path,
            state_path=self.state_path,
            lock_path=self.lock_path,
            incoming_dir=self.incoming,
            max_input_bytes=1024 * 1024,
            max_archive_bytes=1024 * 1024,
            max_members=50,
            max_file_bytes=256 * 1024,
            max_total_bytes=512 * 1024,
            keep_releases=2,
        )
        kwargs.update(overrides)
        return self.module.ActivationConfig(**kwargs)

    def _restart(self, phase: str = "activate") -> None:
        self.restart_calls.append(phase)

    def _health(self) -> bool:
        self.health_calls += 1
        return True

    def _manifest(self, files: dict[str, bytes], sha: str | None = None, archive: str | None = None) -> dict[str, object]:
        return {
            "artifact_format_version": 1,
            "application_name": "storage-monitor",
            "schema_version": 1,
            "archive": archive or self.archive_name,
            "source_sha": sha or self.sha,
            "included_paths": sorted(files),
            "files": {path: hashlib.sha256(data).hexdigest() for path, data in files.items()},
        }

    def _archive(self, *, files: dict[str, bytes] | None = None, manifest: dict[str, object] | None = None,
                 metadata: dict[str, object] | None = None, members: list[tarfile.TarInfo] | None = None,
                 name: str | None = None, directory: Path | None = None) -> tuple[Path, Path, str]:
        files = files or self._runtime_files()
        manifest = manifest or self._manifest(files, archive=name or self.archive_name)
        archive_dir = directory or self.incoming
        archive_path = archive_dir / (name or self.archive_name)
        metadata_path = archive_dir / f"{archive_path.name}.sha256.json"
        archive_dir.mkdir(parents=True, exist_ok=True)
        with tarfile.open(archive_path, "w:gz") as tar:
            if members is None:
                manifest_bytes = json.dumps(manifest, sort_keys=True).encode("utf-8")
                info = tarfile.TarInfo("storage-monitor/RELEASE-MANIFEST.json")
                info.size = len(manifest_bytes)
                info.mode = 0o444
                tar.addfile(info, fileobj=__import__("io").BytesIO(manifest_bytes))
                for rel in sorted(files):
                    data = files[rel]
                    info = tarfile.TarInfo(f"storage-monitor/{rel}")
                    info.size = len(data)
                    info.mode = 0o444 if not rel.endswith(".py") else 0o555
                    tar.addfile(info, fileobj=__import__("io").BytesIO(data))
            else:
                for info in members:
                    payload = b"x" * info.size
                    tar.addfile(info, fileobj=__import__("io").BytesIO(payload) if info.isfile() else None)
        digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
        metadata = metadata or {
            "application_name": "storage-monitor",
            "schema_version": 1,
            "source_sha": self.sha,
            "archive": archive_path.name,
            "sha256": digest,
        }
        metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
        return archive_path, metadata_path, digest

    def _runtime_files(self) -> dict[str, bytes]:
        return {
            "viewer/serve.py": b"print('serve')\n",
            "viewer/app.js": b"console.log('ok');\n",
            "viewer/data-client.js": b"export const dataClient = {};\n",
            "viewer/debug.html": b"<html>debug</html>\n",
            "viewer/echarts.min.js": b"/*! echarts */\n",
            "viewer/index.html": b"<html></html>\n",
            "viewer/overview.js": b"export const overview = {};\n",
            "viewer/selection.js": b"export const selection = {};\n",
            "viewer/styles.css": b"body { color: black; }\n",
            "viewer/tables.js": b"export const tables = {};\n",
            "viewer/treemap.js": b"export const treemap = {};\n",
            "viewer/users-chart.js": b"export const users = {};\n",
            "collector/__init__.py": b"",
            "collector/inventory.py": b"def load_inventory(): pass\n",
            "collector/jobs.py": b"class RescanJobManager: pass\n",
            "collector/service.py": b"class PollService: pass\n",
            "collector/snapshot.py": b"class Snapshot: pass\n",
            "collector/store.py": b"class CentralStore: pass\n",
            "collector/transport.py": b"class OpenSshTransport: pass\n",
            "config/servers.example.yaml": b"servers: []\n",
            "docs/schema-v1.md": b"# Schema\n",
            "deploy/direct_proxy.py": b"print('proxy')\n",
        }

    def _activate(self, archive: Path, metadata: Path, digest: str, **config_overrides):
        return self.module.activate_release(
            self._config(**config_overrides),
            sha=self.sha,
            expected_digest=digest,
            artifact_path=archive,
            metadata_path=metadata,
            restart=self._restart,
            health=self._health,
        )

    def _assert_gpu_sentinel_preserved(self) -> None:
        self.assertEqual(self.gpu_sentinel.read_bytes(), b"gpu bytes must not change")

    def test_rejects_invalid_sha_digest_and_metadata_identity_before_mutation(self) -> None:
        archive, metadata, digest = self._archive()
        cases = [
            {"sha": "not-a-sha", "expected_digest": digest, "metadata_path": metadata},
            {"sha": self.sha, "expected_digest": "not-a-digest", "metadata_path": metadata},
            {"sha": self.sha, "expected_digest": "0" * 64, "metadata_path": metadata},
        ]
        bad_metadata = self.incoming / "bad.json"
        bad_metadata.write_text(json.dumps({"application_name": "gpu-monitor", "schema_version": 1, "source_sha": self.sha, "archive": archive.name, "sha256": digest}), encoding="utf-8")
        cases.append({"sha": self.sha, "expected_digest": digest, "metadata_path": bad_metadata})
        for kwargs in cases:
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(self.module.ActivationError):
                    self.module.activate_release(
                        self._config(), artifact_path=archive, restart=self._restart, health=self._health, **kwargs
                    )
        self.assertFalse(self.app_path.exists())
        self.assertEqual(self.restart_calls, [])
        self._assert_gpu_sentinel_preserved()

    def test_rejects_stdin_payload_larger_than_configured_bound(self) -> None:
        archive, metadata, digest = self._archive()
        with self.assertRaises(self.module.ActivationError):
            self.module.activate_release(
                self._config(max_input_bytes=len(archive.read_bytes()) - 1),
                sha=self.sha,
                expected_digest=digest,
                artifact_stdin=__import__("io").BytesIO(archive.read_bytes()),
                metadata_path=metadata,
                restart=self._restart,
                health=self._health,
            )
        self.assertFalse(self.app_path.exists())

    def test_rejects_artifact_or_metadata_paths_outside_private_incoming_dir(self) -> None:
        archive, metadata, digest = self._archive()
        outside_archive = self.root / "outside.tar.gz"
        outside_archive.write_bytes(archive.read_bytes())
        outside_metadata = self.root / "outside.json"
        outside_metadata.write_bytes(metadata.read_bytes())

        for artifact_path, metadata_path in [(outside_archive, metadata), (archive, outside_metadata)]:
            with self.subTest(artifact=artifact_path, metadata=metadata_path):
                with self.assertRaises(self.module.ActivationError):
                    self.module.activate_release(
                        self._config(),
                        sha=self.sha,
                        expected_digest=digest,
                        artifact_path=artifact_path,
                        metadata_path=metadata_path,
                        restart=self._restart,
                        health=self._health,
                    )
        self.assertFalse(self.app_path.exists())

    def test_extracts_private_verified_bytes_when_original_artifact_is_swapped_after_validation(self) -> None:
        original_files = self._runtime_files()
        archive, metadata, digest = self._archive(files=original_files)
        replacement_files = dict(original_files)
        replacement_files["viewer/app.js"] = b"console.log('pw');\n"
        replacement, _, _ = self._archive(files=replacement_files, directory=self.incoming / "replacement")
        original_start_state = self.module._current_start_state
        observed_stages: list[tuple[Path, int]] = []

        def swap_source_after_validation(config) -> object:
            observed_stages.extend(
                (path, path.stat().st_mode & 0o777)
                for path in self.incoming.iterdir()
                if path.is_file() and path not in {archive, metadata}
            )
            os.replace(replacement, archive)
            return original_start_state(config)

        with mock.patch.object(self.module, "_current_start_state", side_effect=swap_source_after_validation):
            self._activate(archive, metadata, digest)

        extracted = self.release_root / self.sha / "storage-monitor/viewer/app.js"
        self.assertEqual(extracted.read_bytes(), original_files["viewer/app.js"])
        self.assertEqual(len(observed_stages), 1)
        stage_path, stage_mode = observed_stages[0]
        self.assertEqual(stage_mode, 0o600)
        self.assertFalse(stage_path.exists())

    def test_rejects_unsafe_archive_members_before_extraction(self) -> None:
        cases: list[tuple[str, tarfile.TarInfo]] = []
        for name in ["storage-monitor/../evil", "/storage-monitor/viewer/app.js", "other-root/file"]:
            info = tarfile.TarInfo(name)
            info.size = 1
            cases.append((name, info))
        link = tarfile.TarInfo("storage-monitor/viewer/link")
        link.type = tarfile.SYMTYPE
        link.linkname = "app.js"
        cases.append(("symlink", link))
        hardlink = tarfile.TarInfo("storage-monitor/viewer/hard")
        hardlink.type = tarfile.LNKTYPE
        hardlink.linkname = "storage-monitor/viewer/app.js"
        cases.append(("hardlink", hardlink))
        fifo = tarfile.TarInfo("storage-monitor/viewer/fifo")
        fifo.type = tarfile.FIFOTYPE
        cases.append(("fifo", fifo))
        char_device = tarfile.TarInfo("storage-monitor/viewer/device")
        char_device.type = tarfile.CHRTYPE
        cases.append(("device", char_device))
        unsafe_mode = tarfile.TarInfo("storage-monitor/viewer/world-writable")
        unsafe_mode.mode = 0o777
        unsafe_mode.size = 1
        cases.append(("unsafe-mode", unsafe_mode))

        for label, bad_member in cases:
            with self.subTest(label=label):
                archive, metadata, digest = self._archive(members=[bad_member])
                with self.assertRaises(self.module.ActivationError):
                    self._activate(archive, metadata, digest)
                if self.app_path.is_symlink() or self.app_path.exists():
                    self.fail("unsafe archive mutated app path")

    def test_rejects_duplicate_member_count_and_expanded_size_limits(self) -> None:
        duplicate_a = tarfile.TarInfo("storage-monitor/viewer/app.js")
        duplicate_a.size = 1
        duplicate_b = tarfile.TarInfo("storage-monitor/viewer/app.js")
        duplicate_b.size = 1
        archive, metadata, digest = self._archive(members=[duplicate_a, duplicate_b])
        with self.assertRaises(self.module.ActivationError):
            self._activate(archive, metadata, digest)

        archive, metadata, digest = self._archive()
        with self.assertRaises(self.module.ActivationError):
            self._activate(archive, metadata, digest, max_members=2)
        with self.assertRaises(self.module.ActivationError):
            self._activate(archive, metadata, digest, max_file_bytes=4)
        with self.assertRaises(self.module.ActivationError):
            self._activate(archive, metadata, digest, max_total_bytes=8)

    def test_member_limit_stops_reading_before_rest_of_member_bomb(self) -> None:
        members = []
        for index in range(100):
            member = tarfile.TarInfo(f"storage-monitor/bomb/{index:04d}.txt")
            member.mode = 0o444
            members.append(member)
        archive, metadata, digest = self._archive(members=members)
        original_next = tarfile.TarFile.next
        next_calls = 0

        def bounded_next(tar: tarfile.TarFile):
            nonlocal next_calls
            next_calls += 1
            if next_calls > 8:
                raise AssertionError("tar parser read materially beyond max_members")
            return original_next(tar)

        with mock.patch.object(tarfile.TarFile, "next", bounded_next):
            with self.assertRaisesRegex(self.module.ActivationError, "member-count bound"):
                self._activate(archive, metadata, digest, max_members=2)

        self.assertLessEqual(next_calls, 8)
        staged = {
            path for path in self.incoming.iterdir()
            if path.is_file() and path not in {archive, metadata}
        }
        self.assertEqual(staged, set())

    def test_rejects_missing_runtime_file_and_manifest_file_hash_mismatch(self) -> None:
        for missing in ["deploy/direct_proxy.py", "viewer/styles.css"]:
            with self.subTest(missing=missing):
                files = self._runtime_files()
                files.pop(missing)
                archive, metadata, digest = self._archive(files=files)
                with self.assertRaises(self.module.ActivationError):
                    self._activate(archive, metadata, digest)

        files = self._runtime_files()
        manifest = self._manifest(files)
        manifest["files"]["viewer/app.js"] = "0" * 64
        archive, metadata, digest = self._archive(files=files, manifest=manifest)
        with self.assertRaises(self.module.ActivationError):
            self._activate(archive, metadata, digest)

    def test_migrates_real_legacy_directory_to_backup_and_activates_immutable_release(self) -> None:
        self.app_path.mkdir(parents=True)
        sentinel = self.app_path / "legacy-sentinel.txt"
        sentinel.write_bytes(b"legacy bytes")
        archive, metadata, digest = self._archive()

        status = self._activate(archive, metadata, digest)

        self.assertEqual(status["status"], "active")
        self.assertTrue(self.app_path.is_symlink())
        self.assertEqual(self.app_path.resolve(), (self.release_root / self.sha / "storage-monitor").resolve())
        backup = Path(status["legacy_backup"])
        self.assertTrue(backup.is_dir())
        self.assertEqual((backup / "legacy-sentinel.txt").read_bytes(), b"legacy bytes")
        self.assertEqual((self.app_path / "viewer/app.js").stat().st_mode & 0o222, 0)
        persisted = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.assertEqual(persisted["source_sha"], self.sha)
        self.assertEqual(persisted["archive_digest"], digest)
        self.assertEqual(self.restart_calls, ["activate"])
        self.assertEqual(self.health_calls, 1)
        self._assert_gpu_sentinel_preserved()

    def test_switches_valid_prior_release_symlink_atomically_and_records_previous(self) -> None:
        previous_target = self.release_root / self.old_sha / "storage-monitor"
        previous_target.mkdir(parents=True)
        (previous_target / "old.txt").write_text("old", encoding="utf-8")
        self.app_path.parent.mkdir(parents=True)
        self.app_path.symlink_to(previous_target)
        archive, metadata, digest = self._archive()

        status = self._activate(archive, metadata, digest)

        self.assertEqual(Path(status["previous"]).resolve(), previous_target.resolve())
        self.assertEqual(self.app_path.resolve(), (self.release_root / self.sha / "storage-monitor").resolve())
        self.assertEqual(self.restart_calls, ["activate"])

    def test_fails_closed_for_broken_or_external_symlink_without_restart(self) -> None:
        external = self.root / "tmp/external"
        external.mkdir(parents=True)
        for target in [self.root / "missing", external]:
            with self.subTest(target=target):
                if self.app_path.is_symlink() or self.app_path.exists():
                    self.app_path.unlink()
                self.app_path.parent.mkdir(parents=True, exist_ok=True)
                self.app_path.symlink_to(target)
                archive, metadata, digest = self._archive()
                with self.assertRaises(self.module.ActivationError):
                    self._activate(archive, metadata, digest)
                self.assertEqual(os.readlink(self.app_path), str(target))
                self.assertEqual(self.restart_calls, [])

    def test_accepts_duplicate_exact_release_directory_but_rejects_mismatch(self) -> None:
        archive, metadata, digest = self._archive()
        self._activate(archive, metadata, digest)
        self.restart_calls.clear()
        status = self._activate(archive, metadata, digest)
        self.assertEqual(status["status"], "active")
        self.assertEqual(self.restart_calls, ["activate"])

        (self.release_root / self.sha / "storage-monitor/viewer/app.js").chmod(0o644)
        (self.release_root / self.sha / "storage-monitor/viewer/app.js").write_bytes(b"tampered")
        with self.assertRaises(self.module.ActivationError):
            self._activate(archive, metadata, digest)

    def test_failed_health_rolls_back_prior_symlink_and_restarts_rollback(self) -> None:
        previous_target = self.release_root / self.old_sha / "storage-monitor"
        previous_target.mkdir(parents=True)
        self.app_path.parent.mkdir(parents=True)
        self.app_path.symlink_to(previous_target)
        archive, metadata, digest = self._archive()

        def failing_health() -> bool:
            return False

        with self.assertRaises(self.module.ActivationError):
            self.module.activate_release(
                self._config(), sha=self.sha, expected_digest=digest, artifact_path=archive, metadata_path=metadata,
                restart=self._restart, health=failing_health,
            )
        self.assertEqual(self.app_path.resolve(), previous_target.resolve())
        self.assertEqual(self.restart_calls, ["activate", "rollback"])
        self.assertFalse(any(path.name.startswith(".activate-") for path in self.app_path.parent.iterdir()))

    def test_rollback_restart_failure_is_persisted_and_raised_after_candidate_health_failure(self) -> None:
        previous_target = self.release_root / self.old_sha / "storage-monitor"
        previous_target.mkdir(parents=True)
        self.app_path.parent.mkdir(parents=True)
        self.app_path.symlink_to(previous_target)
        previous_state = {
            "status": "active",
            "release": str(previous_target),
            "source_sha": self.old_sha,
            "archive_digest": "d" * 64,
        }
        self.state_path.parent.mkdir(parents=True)
        self.state_path.write_text(json.dumps(previous_state), encoding="utf-8")
        archive, metadata, digest = self._archive()

        def restart_with_failed_rollback(phase: str) -> None:
            self.restart_calls.append(phase)
            if phase == "rollback":
                raise RuntimeError("restored service restart failed")

        with self.assertRaises(self.module.ActivationError) as raised:
            self.module.activate_release(
                self._config(), sha=self.sha, expected_digest=digest, artifact_path=archive, metadata_path=metadata,
                restart=restart_with_failed_rollback, health=lambda: False,
            )

        self.assertIn("health check failed after activation", str(raised.exception))
        self.assertIn("rollback restart failed: restored service restart failed", str(raised.exception))
        self.assertEqual(self.app_path.resolve(), previous_target.resolve())
        self.assertEqual(self.restart_calls, ["activate", "rollback"])
        persisted = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.assertEqual(persisted["release"], str(previous_target))
        self.assertEqual(persisted["source_sha"], self.old_sha)
        self.assertEqual(persisted["archive_digest"], "d" * 64)
        self.assertEqual(persisted["status"], "rollback_restart_failed")
        self.assertIs(persisted["restored"], True)
        self.assertEqual(persisted["activation_error"], "health check failed after activation")
        self.assertEqual(persisted["rollback_restart_error"], "restored service restart failed")

    def test_failed_health_rolls_back_first_legacy_migration_byte_for_byte(self) -> None:
        self.app_path.mkdir(parents=True)
        (self.app_path / "legacy-sentinel.txt").write_bytes(b"legacy bytes")
        archive, metadata, digest = self._archive()

        with self.assertRaises(self.module.ActivationError):
            self.module.activate_release(
                self._config(), sha=self.sha, expected_digest=digest, artifact_path=archive, metadata_path=metadata,
                restart=self._restart, health=lambda: False,
            )
        self.assertFalse(self.app_path.is_symlink())
        self.assertEqual((self.app_path / "legacy-sentinel.txt").read_bytes(), b"legacy bytes")
        self.assertEqual(self.restart_calls, ["activate", "rollback"])

    def test_prunes_only_old_inactive_storage_releases(self) -> None:
        previous_target = self.release_root / self.old_sha / "storage-monitor"
        active_target = self.release_root / self.sha / "storage-monitor"
        old_extra = self.release_root / ("b" * 40) / "storage-monitor"
        for path in [previous_target, old_extra]:
            path.mkdir(parents=True)
            (path / "sentinel").write_text(path.parent.name, encoding="utf-8")
        incoming_file = self.incoming / "keep.tar.gz"
        incoming_file.parent.mkdir(parents=True)
        incoming_file.write_text("incoming", encoding="utf-8")
        self.app_path.parent.mkdir(parents=True)
        self.app_path.symlink_to(previous_target)
        archive, metadata, digest = self._archive()

        self._activate(archive, metadata, digest, keep_releases=1)

        self.assertTrue(active_target.exists())
        self.assertTrue(previous_target.exists())
        self.assertFalse(old_extra.exists())
        self.assertTrue(incoming_file.exists())


if __name__ == "__main__":
    unittest.main()
