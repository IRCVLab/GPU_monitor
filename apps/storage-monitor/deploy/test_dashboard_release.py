from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
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

    def test_activation_state_is_group_readable_using_parent_runtime_group(self) -> None:
        self.state_path.parent.mkdir(parents=True)

        self.module._atomic_write_json(self.state_path, {"status": "active"})

        state_stat = self.state_path.stat()
        self.assertEqual(stat.S_IMODE(state_stat.st_mode), 0o640)
        self.assertEqual(state_stat.st_gid, self.state_path.parent.stat().st_gid)

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

    def test_rejects_external_artifact_but_safely_accepts_regular_external_metadata(self) -> None:
        archive, metadata, digest = self._archive()
        outside_archive = self.root / "outside.tar.gz"
        outside_archive.write_bytes(archive.read_bytes())
        outside_metadata = self.root / "outside.json"
        outside_metadata.write_bytes(metadata.read_bytes())

        with self.assertRaises(self.module.ActivationError):
            self.module.activate_release(
                self._config(),
                sha=self.sha,
                expected_digest=digest,
                artifact_path=outside_archive,
                metadata_path=metadata,
                restart=self._restart,
                health=self._health,
            )

        status = self.module.prepare_release(
            self._config(),
            sha=self.sha,
            expected_digest=digest,
            artifact_path=archive,
            metadata_path=outside_metadata,
        )
        self.assertEqual(status["status"], "prepared")
        self.assertFalse(self.app_path.exists())

        metadata_link = self.root / "metadata-link.json"
        metadata_link.symlink_to(outside_metadata)
        with self.assertRaises(self.module.ActivationError):
            self.module.prepare_release(
                self._config(),
                sha=self.sha,
                expected_digest=digest,
                artifact_path=archive,
                metadata_path=metadata_link,
            )

    def test_release_parent_is_readonly_and_traversable_under_restrictive_umask(self) -> None:
        archive, metadata, digest = self._archive()
        previous_umask = os.umask(0o077)
        try:
            status = self.module.prepare_release(
                self._config(),
                sha=self.sha,
                expected_digest=digest,
                artifact_path=archive,
                metadata_path=metadata,
            )
        finally:
            os.umask(previous_umask)

        release_parent = Path(status["candidate_release"]).parent
        self.assertEqual(stat.S_IMODE(release_parent.stat().st_mode), 0o555)
        self.assertEqual(stat.S_IMODE((release_parent / "storage-monitor").stat().st_mode), 0o555)

    def test_extracts_private_verified_bytes_when_original_artifact_is_swapped_after_validation(self) -> None:
        original_files = self._runtime_files()
        archive, metadata, digest = self._archive(files=original_files)
        replacement_files = dict(original_files)
        replacement_files["viewer/app.js"] = b"console.log('pw');\n"
        replacement, _, _ = self._archive(files=replacement_files, directory=self.incoming / "replacement")
        original_start_state = self.module._current_start_state

        def swap_source_after_validation(config) -> object:
            os.replace(replacement, archive)
            return original_start_state(config)

        with mock.patch.object(self.module, "_current_start_state", side_effect=swap_source_after_validation):
            self._activate(archive, metadata, digest)

        extracted = self.release_root / self.sha / "storage-monitor/viewer/app.js"
        self.assertEqual(extracted.read_bytes(), original_files["viewer/app.js"])
        unexpected_stages = {
            path for path in self.incoming.iterdir()
            if path.is_file() and path not in {archive, metadata}
        }
        self.assertEqual(unexpected_stages, set())

    def test_extracts_from_held_descriptor_when_staged_directory_entry_is_replaced(self) -> None:
        original_files = self._runtime_files()
        archive, metadata, digest = self._archive(files=original_files)
        replacement_files = dict(original_files)
        replacement_files["viewer/app.js"] = b"console.log('pw');\n"
        replacement, _, _ = self._archive(files=replacement_files, directory=self.incoming / "replacement")
        original_extract = self.module._extract_private
        observed_stage_modes: list[int] = []

        def replace_staged_entry(config, prepared, sha: str) -> Path:
            staged_file = getattr(prepared, "staged_file", None)
            if staged_file is None:
                observed_stage_modes.append(prepared.staged_path.stat().st_mode & 0o777)
            else:
                observed_stage_modes.append(os.fstat(staged_file.fileno()).st_mode & 0o777)
            os.replace(replacement, prepared.staged_path)
            return original_extract(config, prepared, sha)

        with mock.patch.object(self.module, "_extract_private", side_effect=replace_staged_entry):
            self._activate(archive, metadata, digest)

        extracted = self.release_root / self.sha / "storage-monitor/viewer/app.js"
        self.assertEqual(extracted.read_bytes(), original_files["viewer/app.js"])
        self.assertEqual(observed_stage_modes, [0o600])

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

    def test_prepare_only_cli_extracts_supplied_release_without_switch_restart_or_state(self) -> None:
        self.app_path.mkdir(parents=True)
        (self.app_path / "legacy-sentinel.txt").write_bytes(b"legacy bytes")
        archive, metadata, digest = self._archive()
        stdout = io.StringIO()

        with mock.patch("sys.stdout", stdout):
            rc = self.module.main([
                "--prepare-only",
                "--sha", self.sha,
                "--expected-digest", digest,
                "--artifact", str(archive),
                "--metadata", str(metadata),
                "--release-root", str(self.release_root),
                "--app-path", str(self.app_path),
                "--state-path", str(self.state_path),
                "--lock-path", str(self.lock_path),
                "--incoming-dir", str(self.incoming),
                "--restart-argv", "/bin/false",
                "--health-argv", "/bin/false",
            ])

        self.assertEqual(rc, 0)
        status = json.loads(stdout.getvalue())
        prepared = self.release_root / self.sha / "storage-monitor"
        self.assertEqual(status, {
            "archive_digest": digest,
            "candidate_release": str(prepared),
            "source_sha": self.sha,
            "status": "prepared",
        })
        self.assertTrue(prepared.is_dir())
        self.assertEqual((prepared / "viewer/app.js").stat().st_mode & 0o222, 0)
        self.assertFalse(self.app_path.is_symlink())
        self.assertEqual((self.app_path / "legacy-sentinel.txt").read_bytes(), b"legacy bytes")
        self.assertFalse(self.state_path.exists())

        activated = self._activate(archive, metadata, digest)
        self.assertEqual(activated["release"], str(prepared))
        self.assertEqual(self.app_path.resolve(), prepared.resolve())

    def test_rollback_cli_does_not_require_activation_metadata(self) -> None:
        previous_target = self.release_root / self.old_sha / "storage-monitor"
        failed_target = self.release_root / self.sha / "storage-monitor"
        previous_target.mkdir(parents=True)
        failed_target.mkdir(parents=True)
        self.app_path.parent.mkdir(parents=True)
        self.app_path.symlink_to(failed_target)
        self.state_path.parent.mkdir(parents=True)
        self.state_path.write_text(json.dumps({
            "status": "active",
            "source_sha": self.sha,
            "archive_digest": "b" * 64,
            "release": str(failed_target),
            "previous": str(previous_target),
            "legacy_backup": None,
        }) + "\n", encoding="utf-8")

        rc = self.module.main([
            "--rollback-state",
            "--state-path", str(self.state_path),
            "--app-path", str(self.app_path),
            "--release-root", str(self.release_root),
            "--lock-path", str(self.lock_path),
            "--incoming-dir", str(self.incoming),
        ])

        self.assertEqual(rc, 0)
        self.assertEqual(self.app_path.resolve(), previous_target.resolve())

    def test_record_restored_legacy_cli_writes_state_without_mutating_real_app(self) -> None:
        serve = self.app_path / "viewer/serve.py"
        proxy = self.app_path / "deploy/direct_proxy.py"
        serve.parent.mkdir(parents=True)
        proxy.parent.mkdir(parents=True)
        serve.write_bytes(b"#!/usr/bin/env python3\nprint('legacy serve')\n")
        proxy.write_bytes(b"#!/usr/bin/env python3\nprint('legacy proxy')\n")
        serve.chmod(0o755)
        proxy.chmod(0o755)
        sentinel = self.app_path / "legacy-data.bin"
        sentinel.write_bytes(b"\x00legacy\xffunchanged")
        before = {path.relative_to(self.app_path): (path.read_bytes(), path.stat().st_mode) for path in self.app_path.rglob("*") if path.is_file()}
        stdout = io.StringIO()

        with mock.patch("sys.stdout", stdout):
            rc = self.module.main([
                "--record-restored-legacy",
                "--state-path", str(self.state_path),
                "--app-path", str(self.app_path),
                "--release-root", str(self.release_root),
                "--lock-path", str(self.lock_path),
                "--incoming-dir", str(self.incoming),
            ])

        self.assertEqual(rc, 0)
        canonical_app = str(self.app_path.resolve())
        expected = {
            "legacy_proxy_original_path": str(self.app_path.resolve() / "deploy/direct_proxy.py"),
            "managed_legacy_proxy_sha256": hashlib.sha256(proxy.read_bytes()).hexdigest(),
            "managed_legacy_proxy_target": str(self.app_path.resolve() / "deploy/direct_proxy.py"),
            "restored": canonical_app,
            "restored_legacy_target": canonical_app,
            "status": "rolled_back",
        }
        self.assertEqual(json.loads(stdout.getvalue()), expected)
        self.assertEqual(json.loads(self.state_path.read_text(encoding="utf-8")), expected)
        self.assertFalse(set(expected) & {"release", "current", "source_sha", "archive_digest", "failed_release"})
        after = {path.relative_to(self.app_path): (path.read_bytes(), path.stat().st_mode) for path in self.app_path.rglob("*") if path.is_file()}
        self.assertEqual(after, before)

    def test_external_legacy_proxy_is_snapshotted_and_launcher_uses_only_managed_copy(self) -> None:
        serve = self.app_path / "viewer/serve.py"
        serve.parent.mkdir(parents=True)
        serve.write_bytes(b"#!/usr/bin/env python3\nprint('legacy serve')\n")
        serve.chmod(0o755)
        sentinel = self.app_path / "legacy-data.bin"
        sentinel.write_bytes(b"\x00legacy\xffunchanged")
        external_proxy = self.root / "home/ircv/workspace/storage-viz-direct/proxy.py"
        external_proxy.parent.mkdir(parents=True)
        external_proxy.write_bytes(b"#!/usr/bin/env python3\nprint('external legacy proxy')\n")
        external_proxy.chmod(0o755)

        prepared = self.module.prepare_legacy_proxy_recovery(self._config(), external_proxy)
        managed_target = Path(prepared["managed_legacy_proxy_target"])
        digest = hashlib.sha256(external_proxy.read_bytes()).hexdigest()

        self.assertEqual(prepared["managed_legacy_proxy_sha256"], digest)
        self.assertEqual(managed_target.read_bytes(), external_proxy.read_bytes())
        self.assertEqual(stat.S_IMODE(managed_target.stat().st_mode), 0o440)
        external_proxy.write_text("changed after snapshot\n", encoding="utf-8")

        status = self.module.record_restored_legacy(
            self._config(),
            managed_legacy_proxy_target=managed_target,
            managed_legacy_proxy_sha256=digest,
            legacy_proxy_original_path=external_proxy,
        )

        self.assertEqual(status["managed_legacy_proxy_target"], str(managed_target.resolve()))
        self.assertEqual(status["managed_legacy_proxy_sha256"], digest)
        self.assertEqual(status["legacy_proxy_original_path"], str(external_proxy.resolve()))
        self.assertEqual(sentinel.read_bytes(), b"\x00legacy\xffunchanged")
        launcher = self._load_named_module(
            "storage_viz_proxy_launcher_external_rollback",
            REPO_ROOT / "apps/storage-monitor/deploy/server/storage-viz-proxy-launcher.py",
        )
        launcher_config = launcher.LauncherConfig(
            release_root=self.release_root,
            state_path=self.state_path,
            app_path=self.app_path,
        )
        self.assertEqual(launcher.resolve_proxy_target(launcher_config), managed_target.resolve())
        with self.assertRaises(launcher.LauncherError):
            launcher.validate_proxy_target(external_proxy, launcher_config)

        managed_target.chmod(0o640)
        managed_target.write_text("tampered\n", encoding="utf-8")
        managed_target.chmod(0o440)
        with self.assertRaises(launcher.LauncherError):
            launcher.resolve_proxy_target(launcher_config)

    def test_first_activation_carries_prepared_external_proxy_recovery_metadata(self) -> None:
        serve = self.app_path / "viewer/serve.py"
        serve.parent.mkdir(parents=True)
        serve.write_text("print('legacy dashboard')\n", encoding="utf-8")
        external_proxy = self.root / "home/ircv/workspace/storage-viz-direct/proxy.py"
        external_proxy.parent.mkdir(parents=True)
        external_proxy.write_text("print('legacy proxy')\n", encoding="utf-8")
        external_proxy.chmod(0o755)
        prepared = self.module.prepare_legacy_proxy_recovery(self._config(), external_proxy)
        archive, metadata, digest = self._archive()

        status = self.module.activate_release(
            self._config(),
            sha=self.sha,
            expected_digest=digest,
            artifact_path=archive,
            metadata_path=metadata,
            restart=self._restart,
            health=self._health,
            legacy_proxy_recovery=prepared,
        )

        for key in (
            "legacy_proxy_original_path",
            "managed_legacy_proxy_target",
            "managed_legacy_proxy_sha256",
        ):
            self.assertEqual(status[key], prepared[key])
        self.assertIsNotNone(status["legacy_backup"])

    def test_failed_first_activation_does_not_restore_stale_release_state_over_real_legacy_app(self) -> None:
        serve = self.app_path / "viewer/serve.py"
        serve.parent.mkdir(parents=True)
        serve.write_text("print('real legacy dashboard')\n", encoding="utf-8")
        external_proxy = self.root / "home/ircv/proxy.py"
        external_proxy.parent.mkdir(parents=True)
        external_proxy.write_text("print('real legacy proxy')\n", encoding="utf-8")
        external_proxy.chmod(0o755)
        prepared = self.module.prepare_legacy_proxy_recovery(self._config(), external_proxy)
        self.state_path.write_text(json.dumps({
            "status": "active",
            "release": "/stale/missing/release",
            "legacy_backup": "/stale/missing/legacy-backup",
            "previous": None,
        }), encoding="utf-8")
        archive, metadata, digest = self._archive()

        with self.assertRaisesRegex(self.module.ActivationError, "health check failed"):
            self.module.activate_release(
                self._config(),
                sha=self.sha,
                expected_digest=digest,
                artifact_path=archive,
                metadata_path=metadata,
                restart=self._restart,
                health=lambda: False,
                legacy_proxy_recovery=prepared,
            )

        self.assertFalse(self.app_path.is_symlink())
        self.assertEqual(serve.read_text(encoding="utf-8"), "print('real legacy dashboard')\n")
        self.assertFalse(self.state_path.exists())

    def test_external_legacy_proxy_recovery_cli_prepares_and_records_exact_snapshot(self) -> None:
        serve = self.app_path / "viewer/serve.py"
        serve.parent.mkdir(parents=True)
        serve.write_text("print('legacy dashboard')\n", encoding="utf-8")
        external_proxy = self.root / "home/ircv/workspace/storage-viz-direct/proxy.py"
        external_proxy.parent.mkdir(parents=True)
        external_proxy.write_text("print('legacy proxy')\n", encoding="utf-8")
        external_proxy.chmod(0o755)
        stdout = io.StringIO()
        common = [
            "--state-path", str(self.state_path),
            "--app-path", str(self.app_path),
            "--release-root", str(self.release_root),
            "--lock-path", str(self.lock_path),
            "--incoming-dir", str(self.incoming),
        ]

        with mock.patch("sys.stdout", stdout):
            rc = self.module.main([
                "--prepare-legacy-proxy",
                "--legacy-proxy-source", str(external_proxy),
                *common,
            ])
        self.assertEqual(rc, 0)
        prepared = json.loads(stdout.getvalue())
        stdout = io.StringIO()
        with mock.patch("sys.stdout", stdout):
            rc = self.module.main([
                "--record-restored-legacy",
                "--legacy-proxy-recovery-target", prepared["managed_legacy_proxy_target"],
                "--legacy-proxy-recovery-sha256", prepared["managed_legacy_proxy_sha256"],
                "--legacy-proxy-original-path", prepared["legacy_proxy_original_path"],
                *common,
            ])
        self.assertEqual(rc, 0)
        recorded = json.loads(stdout.getvalue())
        self.assertEqual(recorded["managed_legacy_proxy_target"], prepared["managed_legacy_proxy_target"])
        self.assertEqual(recorded["managed_legacy_proxy_sha256"], prepared["managed_legacy_proxy_sha256"])

    def test_managed_legacy_proxy_rejects_group_writable_recovery_directory(self) -> None:
        serve = self.app_path / "viewer/serve.py"
        serve.parent.mkdir(parents=True)
        serve.write_text("print('legacy dashboard')\n", encoding="utf-8")
        external_proxy = self.root / "home/ircv/proxy.py"
        external_proxy.parent.mkdir(parents=True)
        external_proxy.write_text("print('legacy proxy')\n", encoding="utf-8")
        external_proxy.chmod(0o755)
        prepared = self.module.prepare_legacy_proxy_recovery(self._config(), external_proxy)
        managed = Path(prepared["managed_legacy_proxy_target"])
        managed.parent.parent.chmod(0o770)

        with self.assertRaisesRegex(self.module.ActivationError, "recovery directory mode"):
            self.module.record_restored_legacy(
                self._config(),
                managed_legacy_proxy_target=managed,
                managed_legacy_proxy_sha256=str(prepared["managed_legacy_proxy_sha256"]),
                legacy_proxy_original_path=external_proxy,
            )

    def test_record_restored_legacy_rejects_symlink_missing_and_unsafe_legacy_layouts(self) -> None:
        def real_layout(root: Path) -> tuple[Path, Path]:
            serve = root / "viewer/serve.py"
            proxy = root / "deploy/direct_proxy.py"
            serve.parent.mkdir(parents=True)
            proxy.parent.mkdir(parents=True)
            serve.write_text("serve\n", encoding="utf-8")
            proxy.write_text("proxy\n", encoding="utf-8")
            serve.chmod(0o755)
            proxy.chmod(0o755)
            return serve, proxy

        missing = self.root / "missing-layout"
        missing.mkdir()
        with self.assertRaises(self.module.ActivationError):
            self.module.record_restored_legacy(self._config(app_path=missing))

        external = self.root / "external-layout"
        real_layout(external)
        app_link = self.root / "linked-app"
        app_link.symlink_to(external)
        with self.assertRaises(self.module.ActivationError):
            self.module.record_restored_legacy(self._config(app_path=app_link))

        linked_file_root = self.root / "linked-file-layout"
        serve, proxy = real_layout(linked_file_root)
        proxy.unlink()
        proxy.symlink_to(external / "deploy/direct_proxy.py")
        with self.assertRaises(self.module.ActivationError):
            self.module.record_restored_legacy(self._config(app_path=linked_file_root))

        unsafe_root = self.root / "unsafe-layout"
        _, unsafe_proxy = real_layout(unsafe_root)
        unsafe_proxy.chmod(0o775)
        with self.assertRaises(self.module.ActivationError):
            self.module.record_restored_legacy(self._config(app_path=unsafe_root))

    def test_rollback_contract_restores_previous_symlink_from_activation_state(self) -> None:
        previous_target = self.release_root / self.old_sha / "storage-monitor"
        failed_target = self.release_root / self.sha / "storage-monitor"
        previous_proxy = previous_target / "deploy/direct_proxy.py"
        previous_proxy.parent.mkdir(parents=True)
        previous_proxy.write_bytes(b"old proxy")
        previous_proxy.chmod(0o555)
        failed_target.mkdir(parents=True)
        self.app_path.parent.mkdir(parents=True)
        self.app_path.symlink_to(failed_target)
        self.state_path.parent.mkdir(parents=True)
        self.state_path.write_text(json.dumps({
            "status": "active",
            "source_sha": self.sha,
            "archive_digest": "b" * 64,
            "release": str(failed_target),
            "previous": str(previous_target),
            "legacy_backup": None,
            "failed_release": str(failed_target),
            "failed_source_sha": self.sha,
            "failed_archive_digest": "b" * 64,
            "activation_error": "candidate failed",
        }) + "\n", encoding="utf-8")

        status = self.module.rollback_to_state(self._config(), restart=self._restart)

        self.assertEqual(status["status"], "rolled_back")
        self.assertEqual(self.app_path.resolve(), previous_target.resolve())
        self.assertEqual(self.restart_calls, ["rollback"])
        persisted = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.assertEqual(persisted["release"], str(previous_target))
        self.assertEqual(persisted["current"], str(previous_target))
        self.assertEqual(persisted["source_sha"], self.old_sha)
        for key in ("failed_release", "failed_source_sha", "failed_archive_digest", "activation_error", "rollback_restart_error"):
            self.assertNotIn(key, persisted)

    def test_rollback_rejects_present_malformed_state_without_restart_or_rewrite(self) -> None:
        self.state_path.parent.mkdir(parents=True)
        malformed = b'{"status":"active"\n'
        self.state_path.write_bytes(malformed)

        with self.assertRaisesRegex(self.module.ActivationError, "invalid or unreadable"):
            self.module.rollback_to_state(self._config(), restart=self._restart)

        self.assertEqual(self.restart_calls, [])
        self.assertEqual(self.state_path.read_bytes(), malformed)

    def test_legacy_rollback_preserves_backup_restores_real_opt_and_launcher_accepts_target(self) -> None:
        failed_target = self.release_root / self.sha / "storage-monitor"
        failed_target.mkdir(parents=True)
        self.app_path.parent.mkdir(parents=True)
        self.app_path.symlink_to(failed_target)
        backup = self.app_path.with_name("storage-viz-dashboard.legacy.protected")
        proxy = backup / "deploy/direct_proxy.py"
        proxy.parent.mkdir(parents=True)
        proxy.write_bytes(b"#!/usr/bin/env python3\nprint('legacy')\n")
        proxy.chmod(0o755)
        serve = backup / "viewer/serve.py"
        serve.parent.mkdir(parents=True)
        serve.write_bytes(b"#!/usr/bin/env python3\nprint('legacy dashboard')\n")
        serve.chmod(0o755)
        (backup / "legacy-sentinel.bin").write_bytes(b"\x00legacy\xffbytes")
        self.state_path.parent.mkdir(parents=True)
        self.state_path.write_text(json.dumps({
            "status": "active",
            "source_sha": self.sha,
            "archive_digest": "b" * 64,
            "release": str(failed_target),
            "previous": None,
            "legacy_backup": str(backup),
            "failed_release": str(failed_target),
            "failed_source_sha": self.sha,
        }) + "\n", encoding="utf-8")

        status = self.module.rollback_to_state(self._config(), restart=self._restart)

        self.assertFalse(self.app_path.is_symlink())
        self.assertEqual((self.app_path / "legacy-sentinel.bin").read_bytes(), b"\x00legacy\xffbytes")
        self.assertEqual((backup / "legacy-sentinel.bin").read_bytes(), b"\x00legacy\xffbytes")
        self.assertEqual(status["protected_legacy_backup"], str(backup))
        self.assertEqual(status["restored_legacy_target"], str(self.app_path.resolve()))
        self.assertEqual(status["managed_legacy_proxy_target"], str(self.app_path.resolve() / "deploy/direct_proxy.py"))
        self.assertNotIn("failed_release", status)
        self.assertNotIn("failed_source_sha", status)

        launcher = self._load_named_module(
            "storage_viz_proxy_launcher_rollback",
            REPO_ROOT / "apps/storage-monitor/deploy/server/storage-viz-proxy-launcher.py",
        )
        accepted = launcher.validate_proxy_target(
            self.app_path / "deploy/direct_proxy.py",
            launcher.LauncherConfig(release_root=self.release_root, state_path=self.state_path, app_path=self.app_path),
        )
        self.assertEqual(accepted, (self.app_path / "deploy/direct_proxy.py").resolve())

    def test_legacy_rollback_restores_dashboard_and_uses_prepared_external_proxy_copy(self) -> None:
        failed_target = self.release_root / self.sha / "storage-monitor"
        failed_target.mkdir(parents=True)
        self.app_path.parent.mkdir(parents=True)
        self.app_path.symlink_to(failed_target)
        backup = self.app_path.with_name("storage-viz-dashboard.legacy.external")
        serve = backup / "viewer/serve.py"
        serve.parent.mkdir(parents=True)
        serve.write_bytes(b"#!/usr/bin/env python3\nprint('legacy dashboard')\n")
        serve.chmod(0o755)
        (backup / "legacy-sentinel.bin").write_bytes(b"\x00legacy\xffbytes")
        external_proxy = self.root / "home/ircv/workspace/storage-viz-direct/proxy.py"
        external_proxy.parent.mkdir(parents=True)
        external_proxy.write_bytes(b"#!/usr/bin/env python3\nprint('external legacy proxy')\n")
        external_proxy.chmod(0o755)
        prepared = self.module.prepare_legacy_proxy_recovery(self._config(), external_proxy)
        self.state_path.write_text(json.dumps({
            "status": "active",
            "source_sha": self.sha,
            "archive_digest": "b" * 64,
            "release": str(failed_target),
            "previous": None,
            "legacy_backup": str(backup),
            "legacy_proxy_original_path": prepared["legacy_proxy_original_path"],
            "managed_legacy_proxy_target": prepared["managed_legacy_proxy_target"],
            "managed_legacy_proxy_sha256": prepared["managed_legacy_proxy_sha256"],
        }) + "\n", encoding="utf-8")

        status = self.module.rollback_to_state(self._config(), restart=self._restart)

        self.assertFalse(self.app_path.is_symlink())
        self.assertEqual((self.app_path / "legacy-sentinel.bin").read_bytes(), b"\x00legacy\xffbytes")
        self.assertFalse((self.app_path / "deploy/direct_proxy.py").exists())
        self.assertEqual(status["managed_legacy_proxy_target"], prepared["managed_legacy_proxy_target"])
        self.assertEqual(status["managed_legacy_proxy_sha256"], prepared["managed_legacy_proxy_sha256"])
        launcher = self._load_named_module(
            "storage_viz_proxy_launcher_external_state_rollback",
            REPO_ROOT / "apps/storage-monitor/deploy/server/storage-viz-proxy-launcher.py",
        )
        accepted = launcher.resolve_proxy_target(
            launcher.LauncherConfig(release_root=self.release_root, state_path=self.state_path, app_path=self.app_path)
        )
        self.assertEqual(accepted, Path(str(prepared["managed_legacy_proxy_target"])).resolve())

    def _load_named_module(self, name: str, path: Path):
        import importlib.util
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        self.addCleanup(sys.modules.pop, spec.name, None)
        return module

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



class DashboardProductionHealthContractTest(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)
        self.module = self._load("health_check_dashboard", REPO_ROOT / "apps/storage-monitor/deploy/server/health-check-dashboard.py")

    def _load(self, name: str, path: Path):
        import importlib.util
        import sys
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module

    def _write_envs(
        self,
        *,
        dashboard_overrides: dict[str, str | None] | None = None,
        proxy_overrides: dict[str, str | None] | None = None,
        dashboard_extra: str = "",
        proxy_extra: str = "",
    ) -> tuple[Path, Path, Path]:
        dash = self.root / "dashboard.env"
        proxy = self.root / "proxy.env"
        inv = self.root / "servers.json"
        dashboard_values = {
            "STORAGE_VIZ_BIND": "127.0.0.1",
            "STORAGE_VIZ_PORT": "8088",
            "STORAGE_VIZ_TRUSTED_PROXY": "1",
            "STORAGE_VIZ_ALLOWED_ORIGINS": "http://166.104.167.11:505",
            "STORAGE_VIZ_OPERATOR_ALLOWLIST": "ops-viewer,fixed-proxy-operator",
            "STORAGE_VIZ_SESSION_COOKIE_SECURE": "0",
            "STORAGE_VIZ_INVENTORY": str(inv),
        }
        proxy_values = {
            "STORAGE_VIZ_PROXY_BIND": "0.0.0.0",
            "STORAGE_VIZ_PROXY_PORT": "505",
            "STORAGE_VIZ_PROXY_UPSTREAM_HOST": "127.0.0.1",
            "STORAGE_VIZ_PROXY_UPSTREAM_PORT": "8088",
            "STORAGE_VIZ_PROXY_OPERATOR": "fixed-proxy-operator",
            "STORAGE_VIZ_PROXY_PUBLIC_ORIGIN": "http://166.104.167.11:505",
            "STORAGE_VIZ_PROXY_MAX_RESPONSE_BYTES": "1048576",
        }
        for values, overrides in (
            (dashboard_values, dashboard_overrides or {}),
            (proxy_values, proxy_overrides or {}),
        ):
            for key, value in overrides.items():
                if value is None:
                    values.pop(key, None)
                else:
                    values[key] = value
        dash.write_text(
            "".join(f"{key}={value}\n" for key, value in dashboard_values.items()) + dashboard_extra,
            encoding="utf-8",
        )
        proxy.write_text(
            "".join(f"{key}={value}\n" for key, value in proxy_values.items()) + proxy_extra,
            encoding="utf-8",
        )
        inv.write_text(json.dumps({"servers":[
            {"id":"atlas","enabled":True},
            {"id":"disabled","enabled":False},
            {"id":"hinton","enabled":True},
        ]}), encoding="utf-8")
        return dash, proxy, inv

    def test_rejects_shell_syntax_duplicates_missing_and_incoherent_env_pairs(self) -> None:
        dash, proxy, _ = self._write_envs()
        contract = self.module.load_contract(dashboard_env=dash, proxy_env=proxy)
        self.assertEqual(contract.public_origin, "http://166.104.167.11:505")
        self.assertEqual(contract.public_host, "166.104.167.11:505")
        self.assertEqual(contract.enabled_server_ids, ["atlas", "hinton"])
        self.assertEqual(contract.proxy_env["STORAGE_VIZ_PROXY_UPSTREAM_HOST"], "127.0.0.1")
        self.assertEqual(contract.proxy_env["STORAGE_VIZ_PROXY_UPSTREAM_PORT"], "8088")

        cases = [
            ("dup", {"dashboard_extra":"STORAGE_VIZ_PORT=8089\n"}),
            ("malformed", {"dashboard_extra":"export STORAGE_VIZ_PORT=8088\n"}),
            ("shell", {"proxy_overrides":{"STORAGE_VIZ_PROXY_PUBLIC_ORIGIN":"http://$(hostname):505"}}),
            ("missing", {"proxy_overrides":{"STORAGE_VIZ_PROXY_OPERATOR":None}}),
            ("sample", {"dashboard_overrides":{"STORAGE_VIZ_DEV_SAMPLE_DIR":"/tmp/samples"}}),
            ("direct", {"dashboard_overrides":{"STORAGE_VIZ_DIRECT_LOOPBACK_RESCAN":"1"}}),
            ("bad_origin", {"dashboard_overrides":{"STORAGE_VIZ_ALLOWED_ORIGINS":"http://166.104.167.11:8088"}}),
            ("bad_upstream_host", {"proxy_overrides":{"STORAGE_VIZ_PROXY_UPSTREAM_HOST":"127.0.0.2"}}),
            ("bad_upstream_port", {"proxy_overrides":{"STORAGE_VIZ_PROXY_UPSTREAM_PORT":"8089"}}),
            ("bad_proxy_port_zero", {"proxy_overrides":{"STORAGE_VIZ_PROXY_PORT":"0"}}),
            ("bad_proxy_port_large", {"proxy_overrides":{"STORAGE_VIZ_PROXY_PORT":"65536"}}),
            ("bad_proxy_port_text", {"proxy_overrides":{"STORAGE_VIZ_PROXY_PORT":"eight"}}),
            ("wildcard_dashboard_port_conflict", {"proxy_overrides":{"STORAGE_VIZ_PROXY_PORT":"8088"}}),
            ("loopback_bind", {"proxy_overrides":{"STORAGE_VIZ_PROXY_BIND":"127.0.0.1"}}),
            ("host_bind", {"proxy_overrides":{"STORAGE_VIZ_PROXY_BIND":"public.example.com"}}),
            ("bad_response_bound", {"proxy_overrides":{"STORAGE_VIZ_PROXY_MAX_RESPONSE_BYTES":"536870913"}}),
            ("invented_upstream", {"proxy_extra":"STORAGE_VIZ_PROXY_UPSTREAM=http://127.0.0.1:8088\n"}),
            ("invented_origin", {"proxy_extra":"STORAGE_VIZ_PUBLIC_ORIGIN=http://166.104.167.11:505\n"}),
            ("invented_host", {"proxy_extra":"STORAGE_VIZ_PUBLIC_HOST=166.104.167.11:505\n"}),
        ]
        for label, kwargs in cases:
            with self.subTest(label=label):
                dash, proxy, _ = self._write_envs(**kwargs)
                with self.assertRaises(self.module.HealthCheckError):
                    self.module.load_contract(dashboard_env=dash, proxy_env=proxy)

    def test_validated_proxy_env_enables_real_direct_proxy_contract(self) -> None:
        dash, proxy_env, _ = self._write_envs()
        contract = self.module.load_contract(dashboard_env=dash, proxy_env=proxy_env)
        module_path = REPO_ROOT / "apps/storage-monitor/deploy/direct_proxy.py"
        with mock.patch.dict(os.environ, dict(contract.proxy_env), clear=True):
            direct_proxy = self._load("storage_direct_proxy_contract", module_path)
        self.addCleanup(sys.modules.pop, "storage_direct_proxy_contract", None)

        self.assertEqual(direct_proxy.BIND, "0.0.0.0")
        self.assertEqual(direct_proxy.PORT, 505)
        self.assertEqual(direct_proxy.UPSTREAM_HOST, "127.0.0.1")
        self.assertEqual(direct_proxy.UPSTREAM_PORT, 8088)
        self.assertEqual(direct_proxy.OPERATOR_ID, "fixed-proxy-operator")
        self.assertEqual(direct_proxy.PUBLIC_ORIGIN, "http://166.104.167.11:505")
        self.assertEqual(direct_proxy.MAX_RESPONSE_BYTES, 1048576)
        self.assertIs(direct_proxy.RESCAN_POST_ENABLED, True)

    def test_nat_mapped_public_origin_connects_to_configured_internal_proxy_listener(self) -> None:
        dash, proxy, _ = self._write_envs(proxy_overrides={
            "STORAGE_VIZ_PROXY_BIND": "192.168.0.3",
            "STORAGE_VIZ_PROXY_PORT": "8088",
        })
        contract = self.module.load_contract(dashboard_env=dash, proxy_env=proxy)
        connections: list[tuple[str, int, int]] = []

        class FakeResponse:
            status = 200
            def read(self): return b"{}"
            def getheaders(self): return []

        class FakeConnection:
            def __init__(self, host, port, timeout):
                connections.append((host, port, timeout))
            def request(self, method, path, body=None, headers=None): pass
            def getresponse(self): return FakeResponse()
            def close(self): pass

        self.module._request(FakeConnection, contract, "GET", "/api/session")

        self.assertEqual(connections, [("192.168.0.3", 8088, 5)])
        self.assertEqual(contract.public_origin, "http://166.104.167.11:505")
        self.assertEqual(contract.public_host, "166.104.167.11:505")

    def test_wildcard_proxy_health_connects_to_public_host_not_unspecified_address(self) -> None:
        dash, proxy, _ = self._write_envs()
        contract = self.module.load_contract(dashboard_env=dash, proxy_env=proxy)
        connections: list[tuple[str, int, int]] = []

        class FakeResponse:
            status = 200
            def read(self): return b"{}"
            def getheaders(self): return []

        class FakeConnection:
            def __init__(self, host, port, timeout):
                connections.append((host, port, timeout))
            def request(self, method, path, body=None, headers=None): pass
            def getresponse(self): return FakeResponse()
            def close(self): pass

        self.module._request(FakeConnection, contract, "GET", "/api/session")

        self.assertEqual(connections, [("166.104.167.11", 505, 5)])

    def test_probe_checks_systemd_public_session_servers_and_unknown_rescan_without_mutation(self) -> None:
        dash, proxy, _ = self._write_envs()
        contract = self.module.load_contract(dashboard_env=dash, proxy_env=proxy)
        calls: list[list[str]] = []
        def runner(argv, **kwargs):
            calls.append(list(argv))
            class Result:
                returncode = 0
                stdout = "active\n"
                stderr = ""
            return Result()

        requests: list[tuple[str, str, dict[str, str], bytes | None]] = []
        class FakeResponse:
            def __init__(self, status, body, headers=None):
                self.status = status; self._body = json.dumps(body).encode(); self._headers = headers or {}
            def read(self): return self._body
            def getheaders(self): return list(self._headers.items())
        class FakeConnection:
            responses = [
                FakeResponse(200, {"can_rescan": True, "csrf_token": "csrf"}, {"Set-Cookie":"storage_viz_session=abc; Path=/"}),
                FakeResponse(200, {"data_mode":"inventory", "servers":[{"id":"atlas"},{"id":"hinton"}]}),
                FakeResponse(404, {"error":"UNKNOWN_SERVER"}),
            ]
            def __init__(self, host, port, timeout):
                self.host = host; self.port = port; self.timeout = timeout
            def request(self, method, path, body=None, headers=None):
                requests.append((method, path, dict(headers or {}), body))
            def getresponse(self): return self.responses.pop(0)
            def close(self): pass
        self.module.run_health_check(contract, runner=runner, connection_factory=FakeConnection, sleep=lambda _: None)
        self.assertEqual(calls, [["systemctl", "is-active", "storage-viz-dashboard.service"], ["systemctl", "is-active", "storage-viz-proxy.service"]])
        self.assertEqual([r[0:2] for r in requests], [("GET", "/api/session"), ("GET", "/api/servers"), ("POST", requests[2][1])])
        self.assertRegex(requests[2][1], r"^/api/servers/[A-Za-z0-9_.-]{1,128}/rescan$")
        self.assertNotIn(requests[2][1].split("/")[3], contract.enabled_server_ids)
        self.assertEqual(requests[2][3], b"{}")
        self.assertEqual(requests[2][2]["Cookie"], "storage_viz_session=abc")
        self.assertEqual(requests[2][2]["X-CSRF-Token"], "csrf")
        self.assertEqual(requests[2][2]["Host"], "166.104.167.11:505")
        self.assertEqual(requests[2][2]["Origin"], "http://166.104.167.11:505")

    def test_probe_waits_for_dashboard_readiness_after_initial_proxy_502s(self) -> None:
        dash, proxy, _ = self._write_envs()
        contract = self.module.load_contract(dashboard_env=dash, proxy_env=proxy)

        class Result:
            returncode = 0
            stdout = "active\n"
            stderr = ""

        class FakeResponse:
            def __init__(self, status, body, headers=None, *, raw=False):
                self.status = status
                self._body = body if raw else json.dumps(body).encode()
                self._headers = headers or {}

            def read(self):
                return self._body

            def getheaders(self):
                return list(self._headers.items())

        responses = [
            FakeResponse(502, b"storage-viz upstream unavailable", raw=True)
            for _ in range(4)
        ] + [
            FakeResponse(200, {"can_rescan": True, "csrf_token": "csrf"}, {"Set-Cookie": "storage_viz_session=abc; Path=/"}),
            FakeResponse(200, {"data_mode": "inventory", "servers": [{"id": "atlas"}, {"id": "hinton"}]}),
            FakeResponse(404, {"error": "UNKNOWN_SERVER"}),
        ]

        class FakeConnection:
            def __init__(self, host, port, timeout):
                pass

            def request(self, method, path, body=None, headers=None):
                pass

            def getresponse(self):
                return responses.pop(0)

            def close(self):
                pass

        sleeps: list[float] = []
        self.module.run_health_check(
            contract,
            runner=lambda *args, **kwargs: Result(),
            connection_factory=FakeConnection,
            sleep=sleeps.append,
        )

        self.assertEqual(sleeps, [0.2] * 4)
        self.assertEqual(responses, [])

    def test_probe_reports_proxy_http_boundary_for_non_json_readiness_failure(self) -> None:
        dash, proxy, _ = self._write_envs()
        contract = self.module.load_contract(dashboard_env=dash, proxy_env=proxy)

        class Result:
            returncode = 0
            stdout = "active\n"
            stderr = ""

        class FakeResponse:
            status = 502

            def read(self):
                return b"storage-viz upstream unavailable: connection refused"

            def getheaders(self):
                return [("Content-Type", "text/plain; charset=utf-8")]

        class FakeConnection:
            def __init__(self, host, port, timeout):
                pass

            def request(self, method, path, body=None, headers=None):
                pass

            def getresponse(self):
                return FakeResponse()

            def close(self):
                pass

        with self.assertRaisesRegex(
            self.module.HealthCheckError,
            r"HTTP 502 content-type=text/plain; charset=utf-8 body=storage-viz upstream unavailable",
        ):
            self.module.run_health_check(
                contract,
                runner=lambda *args, **kwargs: Result(),
                connection_factory=FakeConnection,
                sleep=lambda _: None,
                ready_attempts=1,
            )

    def test_candidate_override_connects_to_loopback_1505_without_systemd_and_keeps_public_semantics(self) -> None:
        dash, proxy, _ = self._write_envs()
        contract = self.module.load_contract(
            dashboard_env=dash,
            proxy_env=proxy,
            connect_host="127.0.0.1",
            connect_port=1505,
        )
        connections: list[tuple[str, int, int]] = []
        requests: list[tuple[str, str, dict[str, str]]] = []

        class FakeResponse:
            def __init__(self, status, body, headers=None):
                self.status = status
                self._body = json.dumps(body).encode()
                self._headers = headers or {}

            def read(self):
                return self._body

            def getheaders(self):
                return list(self._headers.items())

        responses = [
            FakeResponse(200, {"can_rescan": True, "csrf_token": "candidate-csrf"}, {"Set-Cookie": "storage_viz_session=candidate; Path=/"}),
            FakeResponse(200, {"data_mode": "inventory", "servers": [{"id": "atlas"}, {"id": "hinton"}]}),
            FakeResponse(404, {"error": "UNKNOWN_SERVER"}),
        ]

        class FakeConnection:
            def __init__(self, host, port, timeout):
                connections.append((host, port, timeout))

            def request(self, method, path, body=None, headers=None):
                requests.append((method, path, dict(headers or {})))

            def getresponse(self):
                return responses.pop(0)

            def close(self):
                pass

        def forbidden_runner(*args, **kwargs):
            raise AssertionError("candidate health must skip systemd service checks")

        self.module.run_health_check(
            contract,
            skip_service_check=True,
            runner=forbidden_runner,
            connection_factory=FakeConnection,
            sleep=lambda _: None,
        )

        self.assertEqual(connections, [("127.0.0.1", 1505, 5)] * 3)
        self.assertEqual([request[:2] for request in requests], [
            ("GET", "/api/session"),
            ("GET", "/api/servers"),
            ("POST", requests[2][1]),
        ])
        self.assertTrue(all(request[2]["Host"] == "166.104.167.11:505" for request in requests))
        self.assertEqual(requests[2][2]["Origin"], "http://166.104.167.11:505")
        self.assertEqual(requests[2][2]["Cookie"], "storage_viz_session=candidate")
        self.assertEqual(requests[2][2]["X-CSRF-Token"], "candidate-csrf")

    def test_candidate_cli_exposes_bounded_connection_override_and_skip_service_flag(self) -> None:
        dash, proxy, _ = self._write_envs()
        observed: dict[str, object] = {}

        def capture(contract, **kwargs):
            observed["contract"] = contract
            observed.update(kwargs)

        with mock.patch.object(self.module, "run_health_check", side_effect=capture):
            rc = self.module.main([
                "--dashboard-env", str(dash),
                "--proxy-env", str(proxy),
                "--connect-host", "127.0.0.1",
                "--connect-port", "1505",
                "--skip-service-check",
            ])

        self.assertEqual(rc, 0)
        contract = observed["contract"]
        self.assertEqual((contract.connect_host, contract.connect_port), ("127.0.0.1", 1505))
        self.assertIs(observed["skip_service_check"], True)

    def test_probe_rejects_code_only_unknown_server_response(self) -> None:
        dash, proxy, _ = self._write_envs()
        contract = self.module.load_contract(dashboard_env=dash, proxy_env=proxy)

        class Result:
            returncode = 0
            stdout = "active\n"
            stderr = ""

        class FakeResponse:
            def __init__(self, status, body, headers=None):
                self.status = status
                self._body = json.dumps(body).encode()
                self._headers = headers or {}

            def read(self):
                return self._body

            def getheaders(self):
                return list(self._headers.items())

        responses = []
        for _ in range(3):
            responses.extend(
                [
                    FakeResponse(
                        200,
                        {"can_rescan": True, "csrf_token": "csrf"},
                        {"Set-Cookie": "storage_viz_session=abc; Path=/"},
                    ),
                    FakeResponse(200, {"data_mode": "inventory", "servers": [{"id": "atlas"}, {"id": "hinton"}]}),
                    FakeResponse(404, {"code": "UNKNOWN_SERVER"}),
                ]
            )

        class FakeConnection:
            def __init__(self, host, port, timeout):
                pass

            def request(self, method, path, body=None, headers=None):
                pass

            def getresponse(self):
                return responses.pop(0)

            def close(self):
                pass

        with self.assertRaisesRegex(self.module.HealthCheckError, "unknown-server rescan probe failed"):
            self.module.run_health_check(
                contract,
                runner=lambda argv, **kwargs: Result(),
                connection_factory=FakeConnection,
                sleep=lambda _: None,
                ready_attempts=3,
            )


class StorageVizProxyLauncherTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(); self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)
        self.module = self._load("storage_viz_proxy_launcher", REPO_ROOT / "apps/storage-monitor/deploy/server/storage-viz-proxy-launcher.py")

    def _load(self, name: str, path: Path):
        import importlib.util, sys
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec); assert spec.loader is not None
        sys.modules[spec.name] = module; spec.loader.exec_module(module); return module

    def _target(self, sha: str = "0123456789abcdef0123456789abcdef01234567") -> Path:
        target = self.root / "srv/storage-viz-dashboard/releases" / sha / "storage-monitor/deploy/direct_proxy.py"
        target.parent.mkdir(parents=True)
        target.write_text("print('proxy')\n", encoding="utf-8")
        target.chmod(0o555)
        return target

    def test_accepts_only_immutable_active_release_or_coherent_restored_legacy_proxy(self) -> None:
        target = self._target()
        state = self.root / "state.json"
        state.write_text(json.dumps({"status": "active", "release": str(target.parents[1])}), encoding="utf-8")
        cfg = self.module.LauncherConfig(
            release_root=self.root / "srv/storage-viz-dashboard/releases",
            state_path=state,
            app_path=self.root / "opt/storage-viz-dashboard",
        )
        self.assertEqual(self.module.validate_proxy_target(target, cfg), target.resolve())

        backup = self.root / "legacy_backup"
        backup_proxy = backup / "deploy/direct_proxy.py"
        backup_proxy.parent.mkdir(parents=True); backup_proxy.write_text("print('legacy')\n", encoding="utf-8"); backup_proxy.chmod(0o755)
        restored = self.root / "opt/storage-viz-dashboard"
        legacy = restored / "deploy/direct_proxy.py"
        legacy.parent.mkdir(parents=True); legacy.write_bytes(backup_proxy.read_bytes()); legacy.chmod(0o755)
        legacy_serve = restored / "viewer/serve.py"
        legacy_serve.parent.mkdir(parents=True); legacy_serve.write_text("print('serve')\n", encoding="utf-8"); legacy_serve.chmod(0o755)
        state.write_text(json.dumps({
            "status": "rolled_back",
            "release": str(restored),
            "current": str(restored),
            "restored_legacy_target": str(restored.resolve()),
            "managed_legacy_proxy_target": str(legacy.resolve()),
            "protected_legacy_backup": str(backup),
        }), encoding="utf-8")
        self.assertEqual(self.module.validate_proxy_target(legacy, cfg), legacy.resolve())

        legacy.write_text("print('tampered')\n", encoding="utf-8")
        with self.assertRaises(self.module.LauncherError):
            self.module.validate_proxy_target(legacy, cfg)
        legacy.write_bytes(backup_proxy.read_bytes())

        bads = [
            self.root / "srv/gpu-dashboard/releases" / target.parent.name / "direct_proxy.py",
            self.root / "tmp/direct_proxy.py",
            backup_proxy,
        ]
        writable = self._target("1111111111111111111111111111111111111111"); writable.chmod(0o755); bads.append(writable)
        link = self.root / "link.py"; link.symlink_to(target); bads.append(link)
        for bad in bads:
            with self.subTest(bad=bad):
                with self.assertRaises(self.module.LauncherError):
                    self.module.validate_proxy_target(bad, cfg)

    def test_launch_without_target_resolves_active_proxy_from_activation_state(self) -> None:
        target = self._target()
        state = self.root / "state.json"
        state.write_text(json.dumps({"status": "active", "release": str(target.parents[1])}), encoding="utf-8")
        cfg = self.module.LauncherConfig(
            release_root=self.root / "srv/storage-viz-dashboard/releases",
            state_path=state,
            app_path=self.root / "opt/storage-viz-dashboard",
        )
        calls: list[tuple[str, list[str]]] = []

        self.module.launch([], config=cfg, execv=lambda exe, argv: calls.append((exe, argv)))

        self.assertEqual(calls[0][1][-1], str(target.resolve()))

    def test_recovery_state_accepts_only_exact_safe_real_legacy_proxy(self) -> None:
        app = self.root / "opt/storage-viz-dashboard"
        serve = app / "viewer/serve.py"
        proxy = app / "deploy/direct_proxy.py"
        serve.parent.mkdir(parents=True)
        proxy.parent.mkdir(parents=True)
        serve.write_text("serve\n", encoding="utf-8")
        proxy.write_text("proxy\n", encoding="utf-8")
        serve.chmod(0o755)
        proxy.chmod(0o755)
        state = self.root / "state.json"
        state.write_text(json.dumps({
            "status": "rolled_back",
            "restored": str(app.resolve()),
            "restored_legacy_target": str(app.resolve()),
            "managed_legacy_proxy_target": str(proxy.resolve()),
        }), encoding="utf-8")
        cfg = self.module.LauncherConfig(
            release_root=self.root / "srv/storage-viz-dashboard/releases",
            state_path=state,
            app_path=app,
        )

        self.assertEqual(self.module.validate_proxy_target(proxy, cfg), proxy.resolve())

        external = self.root / "external/direct_proxy.py"
        external.parent.mkdir(parents=True)
        external.write_text("proxy\n", encoding="utf-8")
        external.chmod(0o755)
        proxy_link = app / "deploy/proxy-link.py"
        proxy_link.symlink_to(proxy)
        cases = [external, proxy_link]
        for candidate in cases:
            with self.subTest(candidate=candidate):
                with self.assertRaises(self.module.LauncherError):
                    self.module.validate_proxy_target(candidate, cfg)

        proxy.chmod(0o775)
        with self.assertRaises(self.module.LauncherError):
            self.module.validate_proxy_target(proxy, cfg)

        proxy.chmod(0o755)
        state.write_text(json.dumps({
            "status": "rolled_back",
            "restored_legacy_target": str(self.root / "external"),
            "managed_legacy_proxy_target": str(external),
        }), encoding="utf-8")
        with self.assertRaises(self.module.LauncherError):
            self.module.validate_proxy_target(external, cfg)

    def test_execs_python_direct_proxy_without_shell(self) -> None:
        target = self._target()
        installed = self.root / "opt/storage-viz-dashboard"
        installed.parent.mkdir(parents=True)
        installed.symlink_to(target.parents[1])
        installed_target = installed / "deploy/direct_proxy.py"
        state = self.root / "state.json"
        state.write_text(json.dumps({"release": str(target.parents[1])}), encoding="utf-8")
        observed = {}
        def execv(exe, argv):
            observed["exe"] = exe; observed["argv"] = argv; raise SystemExit(0)
        with self.assertRaises(SystemExit):
            self.module.launch(
                [str(installed_target)],
                config=self.module.LauncherConfig(
                    release_root=self.root / "srv/storage-viz-dashboard/releases",
                    state_path=state,
                    app_path=installed,
                ),
                execv=execv,
            )
        self.assertEqual(observed["argv"][:2], [sys.executable, str(target.resolve())])
        self.assertEqual(observed["argv"][2:], [])

        with self.assertRaises(self.module.LauncherError):
            self.module.launch(
                [str(installed_target), "--port", "505"],
                config=self.module.LauncherConfig(
                    release_root=self.root / "srv/storage-viz-dashboard/releases",
                    state_path=state,
                    app_path=installed,
                ),
                execv=execv,
            )


class StorageVizProxySystemdUnitTest(unittest.TestCase):
    def test_proxy_unit_uses_existing_dashboard_identity_minimal_bind_cap_and_launcher(self) -> None:
        unit = (REPO_ROOT / "apps/storage-monitor/deploy/server/systemd/storage-viz-proxy.service").read_text(encoding="utf-8")
        self.assertIn("EnvironmentFile=/etc/storage-viz/proxy.env", unit)
        self.assertIn("User=storage-viz", unit)
        self.assertIn("Group=storage-viz", unit)
        self.assertNotIn("User=root", unit)
        self.assertIn("CapabilityBoundingSet=CAP_NET_BIND_SERVICE", unit)
        self.assertIn("AmbientCapabilities=CAP_NET_BIND_SERVICE", unit)
        self.assertNotIn("CAP_SYS_ADMIN", unit)
        self.assertNotIn("gpu", unit.lower())
        self.assertIn(
            "ExecStart=/usr/bin/python3 /opt/storage-viz-dashboard/deploy/server/storage-viz-proxy-launcher.py",
            unit,
        )
        self.assertNotIn("storage-viz-proxy-launcher.py /opt/storage-viz-dashboard/deploy/direct_proxy.py", unit)
        self.assertNotIn("${STORAGE_VIZ_PROXY_TARGET}", unit)
        self.assertNotIn("--bind", unit)
        self.assertNotIn("--port", unit)
        self.assertNotIn("--upstream", unit)
        self.assertNotIn("ReadWritePaths=/var/lib/storage-viz-dashboard", unit)
        self.assertIn("ReadOnlyPaths=/var/lib/storage-viz-dashboard/activation-state.json", unit)
        self.assertIn("ReadOnlyPaths=/var/lib/storage-viz-dashboard/legacy-proxy", unit)
        self.assertIn("InaccessiblePaths=-/var/lib/storage-viz-dashboard/data", unit)

if __name__ == "__main__":
    unittest.main()
