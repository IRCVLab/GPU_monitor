import hashlib
import importlib.util
import io
import json
import os
import shutil
import sys
import tarfile
import tempfile
import unittest
import stat
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from subprocess import CompletedProcess, run
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
PULLER_PATH = REPO_ROOT / "apps/storage-monitor/deploy/server/storage-monitor-release-puller.py"
SHA1 = "1" * 40
SHA2 = "2" * 40
DIGEST = "a" * 64
REPOSITORY = "IRCVLab/GPU_monitor"
REPO_URL = "https://github.com/IRCVLab/GPU_monitor.git"
AGENT_SHA = "3" * 40
STORAGE_APP = REPO_ROOT / "apps/storage-monitor"
AGENT_RUN_ID = 777
AGENT_ARTIFACT_ID = 4242
AGENT_BUNDLE_NAME = "storage-viz-agent.tar.gz"


def load_agent_fixtures():
    with mock.patch.object(sys, "path", [str(STORAGE_APP), *sys.path]):
        from agent.bootstrap import fixtures
    return fixtures


def agent_files(bundle_bytes=None, signature=b"-----BEGIN SSH SIGNATURE-----\nfake\n", **metadata_overrides):
    """The three files agent-bundle.yml uploads, keyed by artifact file name."""
    bundle_bytes = bundle_bytes if bundle_bytes is not None else load_agent_fixtures().bundle(AGENT_SHA, release_seq=120, floor=100)
    metadata = {
        "application": "storage-viz-agent", "archive": "storage-viz-agent.tar.gz", "bootstrap_version": 1,
        "release_seq": 120, "rollback_floor_seq": 100, "source_sha": AGENT_SHA,
        "sha256": hashlib.sha256(bundle_bytes).hexdigest(), "size": len(bundle_bytes),
    }
    metadata.update(metadata_overrides)
    return {
        "storage-viz-agent.tar.gz": bundle_bytes,
        "storage-viz-agent.tar.gz.sig": signature,
        "storage-viz-agent.json": json.dumps(metadata, sort_keys=True).encode(),
    }


def zip_bytes(files):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


AGENT_FILES = agent_files()


def load_puller():
    spec = importlib.util.spec_from_file_location("storage_monitor_release_puller", PULLER_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    with mock.patch.object(sys, "path", [str(PULLER_PATH.parent), *sys.path]):
        spec.loader.exec_module(module)
    return module


class FakeGitHub:
    def __init__(self, sha=SHA1, conclusion="success", agent_files=None, agent_runs=None):
        self.sha = sha
        self.conclusion = conclusion
        self.calls = []
        self.agent_files = AGENT_FILES if agent_files is None else agent_files
        self.agent_runs = agent_runs
        self.downloads = []

    def download(self, path):
        self.downloads.append(path)
        assert path == f"/repos/{REPOSITORY}/actions/artifacts/{AGENT_ARTIFACT_ID}/zip", path
        return zip_bytes(self.agent_files)

    def get_json(self, path):
        self.calls.append(path)
        if path == f"/repos/{REPOSITORY}/git/ref/heads/main":
            return {"object": {"sha": self.sha}}
        if path.startswith(f"/repos/{REPOSITORY}/actions/workflows/ci.yml/runs?"):
            return {"workflow_runs": [{
                "id": 123,
                "name": "ci",
                "event": "push",
                "head_branch": "main",
                "head_sha": self.sha,
                "status": "completed",
                "conclusion": self.conclusion,
                "head_repository": {"full_name": REPOSITORY},
                "path": ".github/workflows/ci.yml",
            }]}
        if path == f"/repos/{REPOSITORY}/commits/{self.sha}/check-runs?per_page=100":
            return {"check_runs": [{
                "id": 99,
                "name": "ci/required",
                "head_sha": self.sha,
                "status": "completed",
                "conclusion": self.conclusion,
                "completed_at": "2026-07-25T01:02:03Z",
            }]}
        if path.startswith(f"/repos/{REPOSITORY}/actions/workflows/agent-bundle.yml/runs?"):
            assert f"head_sha={self.sha}" in path, path
            runs = self.agent_runs if self.agent_runs is not None else [{
                "id": AGENT_RUN_ID, "event": "workflow_run", "head_branch": "main", "head_sha": self.sha,
                "status": "completed", "conclusion": "success",
            }]
            return {"workflow_runs": runs}
        if path == f"/repos/{REPOSITORY}/actions/runs/{AGENT_RUN_ID}/artifacts?per_page=100":
            return {"artifacts": [{"id": AGENT_ARTIFACT_ID, "name": f"viz-agent-bundle-{self.sha}", "expired": False}]}
        raise AssertionError(f"unexpected GitHub API path: {path}")


class StorageReleasePullerTest(unittest.TestCase):
    maxDiff = None

    def config(self, tmpdir):
        puller = load_puller()
        signers = Path(tmpdir) / "agent-allowed-signers"
        signers.write_text('storage-viz-release namespaces="storage-viz-agent" ssh-ed25519 AAAAfake\n')
        token = Path(tmpdir) / "github-token"
        token.write_text("ghs_fake\n")
        return puller.Config(
            agent_allowed_signers=signers,
            github_token_file=token,
            agent_staging_dir=Path(tmpdir) / "agent-staging",
            release_root=Path(tmpdir) / "releases",
            repository=REPOSITORY,
            state_dir=Path(tmpdir) / "state",
            work_dir=Path(tmpdir) / "builder",
            authorizer=Path(tmpdir) / "storage-release-authorizer.py",
            activate=Path(tmpdir) / "storage-dashboard-activate.py",
            activation_state_path=Path(tmpdir) / "activation-state.json",
            build_script="apps/storage-monitor/deploy/build-dashboard-release.py",
            builder_user="storage-viz-builder",
            node_prefix=Path("/usr/local"),
            repo_url=REPO_URL,
            timeout_seconds=30,
        )

    def write_build_output(self, argv, sha=SHA1):
        """What build-dashboard-release.py writes for this argv, agent bundle included."""
        agent_dir = Path(argv[argv.index("--agent-bundle-dir") + 1]) if "--agent-bundle-dir" in argv else None
        agent = {f"agent/{p.name}": p.read_bytes() for p in agent_dir.iterdir()} if agent_dir else {}
        return self.write_artifact(Path(argv[argv.index("--output-dir") + 1]), sha, agent=agent)

    def write_artifact(self, outdir, sha=SHA1, *, missing=None, agent=None):
        outdir.mkdir(parents=True, exist_ok=True)
        archive = outdir / f"storage-monitor-dashboard-{sha}.tar.gz"
        with mock.patch.object(sys, "path", [str(PULLER_PATH.parent), *sys.path]):
            from storage_dashboard_release import REQUIRED_RELEASE_FILES

        files = {path: path.encode() for path in REQUIRED_RELEASE_FILES if path != missing}
        files.update({f"agent/{name}": data for name, data in AGENT_FILES.items()} if agent is None else agent)
        manifest = {
            "application_name": "storage-monitor",
            "archive": archive.name,
            "artifact_format_version": 1,
            "schema_version": 1,
            "source_sha": sha,
            "included_paths": sorted(files),
            "files": {path: hashlib.sha256(content).hexdigest() for path, content in files.items()},
        }
        with tarfile.open(archive, "w:gz") as tar:
            for path, content in {**files, "RELEASE-MANIFEST.json": json.dumps(manifest).encode()}.items():
                member = tarfile.TarInfo(f"storage-monitor/{path}")
                member.size = len(content)
                tar.addfile(member, io.BytesIO(content))
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        (outdir / f"storage-monitor-dashboard-{sha}.sha256.json").write_text(json.dumps({
            "application_name": "storage-monitor",
            "archive": archive.name,
            "artifact_format_version": 1,
            "schema_version": 1,
            "sha256": digest,
            "source_sha": sha,
        }, sort_keys=True) + "\n", encoding="utf-8")
        return archive, digest

    def write_activation_state(self, path, *, sha="", digest="", runtime_digest=""):
        if sha:
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "status": "active",
                "source_sha": sha,
                "archive_digest": digest,
                "release": f"/srv/storage-viz-dashboard/releases/{sha}/storage-monitor",
            }
            if runtime_digest:
                payload["runtime_digest"] = runtime_digest
            path.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    def test_fetches_current_main_first_and_exits_cheaply_when_already_current(self):
        puller = load_puller()
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self.config(tmp)
            cfg.state_dir.mkdir(parents=True)
            (cfg.state_dir / "current-live-sha").write_text(SHA1 + "\n", encoding="utf-8")
            github = FakeGitHub(SHA1)
            result = puller.run_once(cfg, get_json=github.get_json, run_command=lambda *a, **k: self.fail("unexpected command"))
        self.assertEqual(result, "already-current")
        self.assertEqual(github.calls, [f"/repos/{REPOSITORY}/git/ref/heads/main"])

    def test_successful_change_authorizes_builds_validates_and_invokes_option_style_activator_once(self):
        puller = load_puller()
        commands = []
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self.config(tmp)
            github = FakeGitHub(SHA1)

            def run_command(argv, **kwargs):
                commands.append((list(argv), kwargs))
                if argv[0] == str(cfg.activate):
                    forbidden = {"upload", "status", "activate"}
                    self.assertFalse(set(argv[1:]) & forbidden, f"legacy subcommand used: {argv}")
                    self.assertIn("--sha", argv)
                    self.assertEqual(argv[argv.index("--sha") + 1], SHA1)
                    self.assertIn("--expected-digest", argv)
                    self.assertIn("--artifact-stdin", argv)
                    self.assertIn("--metadata", argv)
                    self.assertIn("--restart-argv", argv)
                    self.assertIn("storage-viz-dashboard.service", argv)
                    self.assertIn("storage-viz-proxy.service", argv)
                    self.assertIn("--health-argv", argv)
                    self.assertNotIn("input", kwargs)
                    artifact_bytes = kwargs["stdin"].read()
                    digest = hashlib.sha256(artifact_bytes).hexdigest()
                    self.assertEqual(digest, argv[argv.index("--expected-digest") + 1])
                    return CompletedProcess(argv, 0, json.dumps({
                        "status": "active",
                        "source_sha": SHA1,
                        "archive_digest": digest,
                        "runtime_digest": puller.validate_artifact(Path(argv[argv.index("--metadata") + 1]).parent, SHA1)[2],
                        "release": f"/srv/storage-viz-dashboard/releases/{SHA1}/storage-monitor",
                    }) + "\n", "")
                if argv[0] == "python3" and str(cfg.authorizer) in argv:
                    return CompletedProcess(argv, 0, json.dumps({"authorized": True, "reason": "authorized", "sha": SHA1}) + "\n", "")
                if any(str(part).endswith("build-dashboard-release.py") for part in argv):
                    self.write_build_output(argv)
                return CompletedProcess(argv, 0, "", "")

            result = puller.run_once(cfg, get_json=github.get_json, run_command=run_command, download=github.download)
            deployed_state = (cfg.state_dir / "current-live-sha").read_text(encoding="utf-8").strip()

        rendered = [" ".join(str(x) for x in c[0]) for c in commands]
        self.assertEqual(result, "activated")
        self.assertLess(rendered.index(next(x for x in rendered if "git clone" in x)), rendered.index(next(x for x in rendered if "build-dashboard-release.py" in x)))
        activation_commands = [x for x in commands if x[0][0] == str(cfg.activate)]
        self.assertEqual(len(activation_commands), 1)
        self.assertGreaterEqual(github.calls.count(f"/repos/{REPOSITORY}/git/ref/heads/main"), 2)
        self.assertEqual(deployed_state, SHA1)

    def test_authorizer_receives_successful_workflow_and_required_check_for_exact_sha(self):
        puller = load_puller()
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self.config(tmp)
            workflow, checks, _ = puller.fetch_evidence(cfg, SHA1, FakeGitHub(SHA1).get_json)
            seen = {}

            def run_command(argv, **kwargs):
                seen["argv"] = argv
                workflow_file = Path(argv[argv.index("--workflow-run-file") + 1])
                checks_file = Path(argv[argv.index("--checks-file") + 1])
                seen["workflow"] = json.loads(workflow_file.read_text(encoding="utf-8"))
                seen["checks"] = json.loads(checks_file.read_text(encoding="utf-8"))
                return CompletedProcess(argv, 0, json.dumps({"authorized": True, "reason": "authorized", "sha": SHA1}) + "\n", "")

            puller.authorize(cfg, SHA1, workflow, checks, run_command)

        self.assertEqual(seen["argv"][:2], ["python3", str(cfg.authorizer)])
        self.assertIn("--required-check", seen["argv"])
        self.assertIn("ci/required", seen["argv"])
        self.assertEqual(seen["workflow"]["workflow_run"]["head_sha"], SHA1)
        self.assertEqual(seen["workflow"]["workflow_run"]["conclusion"], "success")
        self.assertEqual(seen["checks"]["check_runs"][0]["name"], "ci/required")
        self.assertEqual(seen["checks"]["check_runs"][0]["head_sha"], SHA1)

    def test_build_release_invokes_tracked_python_script_with_python3_under_builder_limits(self):
        puller = load_puller()
        commands = []
        build_kwargs = []
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self.config(tmp)
            checkout = Path(tmp) / "checkout"

            def run_command(argv, **kwargs):
                commands.append(list(argv))
                if any(str(part).endswith("build-dashboard-release.py") for part in argv):
                    build_kwargs.append(dict(kwargs))
                return CompletedProcess(argv, 0, "", "")

            outdir = puller.build_release(cfg, checkout, SHA1, run_command)

        script = checkout / cfg.build_script
        build_commands = [command for command in commands if str(script) in command]
        self.assertEqual(len(build_commands), 1)
        self.assertEqual(build_kwargs, [{"cwd": checkout, "timeout": cfg.timeout_seconds}])
        build_command = build_commands[0]
        self.assertEqual(
            build_command,
            [
                "/usr/sbin/runuser", "-u", "storage-viz-builder", "--",
                "env", "-i",
                "HOME=/var/lib/storage-viz-dashboard/builder",
                "PATH=/usr/local/bin:/usr/local/bin:/usr/bin:/bin",
                "GIT_CONFIG_NOSYSTEM=1",
                "nice", "-n", "10", "ionice", "-c", "3",
                "python3", str(script),
                "--sha", SHA1,
                "--output-dir", str(outdir),
            ],
        )

    def test_fetch_evidence_defers_required_check_policy_to_shared_authorizer_like_gpu(self):
        puller = load_puller()
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self.config(tmp)
            github = FakeGitHub(SHA1)

            def get_json(path):
                payload = github.get_json(path)
                if path == f"/repos/{REPOSITORY}/commits/{SHA1}/check-runs?per_page=100":
                    return {"check_runs": [{
                        "id": 100,
                        "name": "ci/storage",
                        "head_sha": SHA1,
                        "status": "completed",
                        "conclusion": "success",
                    }]}
                return payload

            workflow, checks, current = puller.fetch_evidence(cfg, SHA1, get_json)

        self.assertEqual(workflow["head_sha"], SHA1)
        self.assertEqual(checks[0]["name"], "ci/storage")
        self.assertEqual(current, SHA1)

    def test_matching_live_digest_records_new_sha_without_invoking_activator(self):
        puller = load_puller()
        events = []
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self.config(tmp)
            cfg.state_dir.mkdir(parents=True)
            (cfg.state_dir / "current-live-sha").write_text(SHA2 + "\n", encoding="utf-8")
            (cfg.state_dir / "failed-release.json").write_text(json.dumps({"failures": 2, "retry_after": 1.0, "sha": SHA1}) + "\n", encoding="utf-8")
            outdir = Path(tmp) / "previous"
            self.write_artifact(outdir, sha=SHA2)
            _, live_digest, runtime_digest = puller.validate_artifact(outdir, SHA2)
            self.write_activation_state(cfg.activation_state_path, sha=SHA2, digest=live_digest, runtime_digest=runtime_digest)
            github = FakeGitHub(SHA1)

            def run_command(argv, **kwargs):
                rendered = " ".join(str(x) for x in argv)
                if argv[0] == str(cfg.activate):
                    self.fail("matching live digest must not invoke activator CLI")
                if argv[0] == "python3" and str(cfg.authorizer) in argv:
                    events.append("authorize")
                    return CompletedProcess(argv, 0, json.dumps({"authorized": True, "reason": "authorized", "sha": SHA1}) + "\n", "")
                if "git clone" in rendered:
                    events.append("checkout")
                if any(str(part).endswith("build-dashboard-release.py") for part in argv):
                    events.append("build")
                    self.write_build_output(argv)
                return CompletedProcess(argv, 0, "", "")

            result = puller.run_once(cfg, get_json=github.get_json, run_command=run_command, download=github.download)
            self.assertEqual(result, "unchanged-runtime")
            self.assertEqual((cfg.state_dir / "current-live-sha").read_text(encoding="utf-8").strip(), SHA1)
            self.assertFalse((cfg.state_dir / "failed-release.json").exists())
            self.assertFalse((cfg.work_dir / "out").exists())
        self.assertEqual(events, ["authorize", "checkout", "build", "authorize"])

    def test_real_builder_distinguishes_runtime_changes_from_empty_and_documentation_commits(self):
        puller = load_puller()
        with mock.patch.object(sys, "path", [str(PULLER_PATH.parent), *sys.path]):
            from storage_dashboard_release import BUILD_RELEASE_FILES

        app_path = Path("apps/storage-monitor")
        builder = REPO_ROOT / app_path / "deploy/build-dashboard-release.py"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            cfg = self.config(tmp)
            for relative in BUILD_RELEASE_FILES:
                destination = repo / app_path / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(REPO_ROOT / app_path / relative, destination)

            def git(*args):
                return run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()

            def commit(message):
                git("add", ".")
                git("commit", "--allow-empty", "-qm", message)
                return git("rev-parse", "HEAD")

            git("init", "-q")
            git("config", "user.name", "Release Test")
            git("config", "user.email", "release-test@example.invalid")
            git("config", "core.filemode", "true")
            baseline = commit("original Storage runtime")
            self.write_activation_state(cfg.activation_state_path, sha=baseline, digest=DIGEST)

            github = FakeGitHub()
            validated = []
            activation_calls = []
            missing_file = None

            def run_command(argv, **kwargs):
                if argv[0] == "python3" and str(cfg.authorizer) in argv:
                    return CompletedProcess(argv, 0, json.dumps({"authorized": True, "reason": "authorized", "sha": github.sha}), "")
                if argv[0] == str(cfg.activate):
                    metadata = Path(argv[argv.index("--metadata") + 1])
                    _, digest, runtime_digest = puller.validate_artifact(metadata.parent, github.sha)
                    self.assertEqual(hashlib.sha256(kwargs["stdin"].read()).hexdigest(), digest)
                    activation_calls.append(github.sha)
                    self.write_activation_state(
                        cfg.activation_state_path, sha=github.sha, digest=digest, runtime_digest=runtime_digest,
                    )
                    return CompletedProcess(argv, 0, cfg.activation_state_path.read_text(encoding="utf-8"), "")
                if any(str(part).endswith("build-dashboard-release.py") for part in argv):
                    output = Path(argv[argv.index("--output-dir") + 1])
                    if missing_file:
                        self.write_artifact(output, github.sha, missing=missing_file)
                    else:
                        agent_dir = argv[argv.index("--agent-bundle-dir") + 1]
                        result = run(
                            [sys.executable, str(builder), "--sha", github.sha, "--output-dir", str(output), "--agent-bundle-dir", agent_dir],
                            cwd=repo, check=True, capture_output=True, text=True, timeout=30,
                        )
                        self.assertIn(github.sha, result.stdout)
                        _, digest, runtime_digest = puller.validate_artifact(output, github.sha)
                        validated.append((digest, runtime_digest))
                return CompletedProcess(argv, 0, "", "")

            def pull(message, expected, activations):
                github.sha = commit(message)
                before = len(activation_calls)
                self.assertEqual(puller.run_once(cfg, get_json=github.get_json, run_command=run_command, download=github.download), expected)
                self.assertEqual(len(activation_calls) - before, activations)
                self.assertEqual(puller.read_state_sha(cfg), github.sha)

            pull("first candidate with pre-upgrade activation state", "activated", 1)
            pull("empty commit", "unchanged-runtime", 0)
            (repo / app_path / "docs/schema-v1.md").write_text("# Revised schema explanation\n", encoding="utf-8")
            pull("packaged documentation only", "unchanged-runtime", 0)
            script = repo / app_path / "viewer/app.js"
            script.chmod(script.stat().st_mode ^ 0o100)
            pull("mode-only change", "activated", 1)
            with script.open("a", encoding="utf-8") as handle:
                handle.write("\n// Updated runtime file\n")
            pull("runtime content change", "activated", 1)

            self.assertEqual(len({archive for archive, _ in validated}), 5)
            self.assertEqual(len({runtime for _, runtime in validated[:3]}), 1)
            self.assertEqual(len({runtime for _, runtime in validated[2:]}), 3)

            missing_file = "collector/scan_telemetry.py"
            github.sha = commit("candidate with incomplete artifact")
            with self.assertRaisesRegex(puller.PullError, "missing required runtime files: collector/scan_telemetry.py"):
                puller.run_once(cfg, get_json=github.get_json, run_command=run_command, download=github.download)
            self.assertEqual(len(activation_calls), 3)

    def test_aborts_without_activation_if_main_advances_before_final_authorization(self):
        puller = load_puller()
        github = FakeGitHub(SHA1)
        main_calls = 0
        commands = []
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self.config(tmp)

            def get_json(path):
                nonlocal main_calls
                if path == f"/repos/{REPOSITORY}/git/ref/heads/main":
                    main_calls += 1
                    return {"object": {"sha": SHA1 if main_calls < 3 else SHA2}}
                return github.get_json(path)

            def run_command(argv, **kwargs):
                commands.append(list(argv))
                if argv[0] == str(cfg.activate):
                    self.fail("activator must not run after main advances before final authorization")
                if argv[0] == "python3" and str(cfg.authorizer) in argv:
                    return CompletedProcess(argv, 0, json.dumps({"authorized": True, "reason": "authorized", "sha": SHA1}) + "\n", "")
                if any(str(part).endswith("build-dashboard-release.py") for part in argv):
                    self.write_build_output(argv)
                return CompletedProcess(argv, 0, "", "")

            with self.assertRaisesRegex(puller.PullError, "current main changed"):
                puller.run_once(cfg, get_json=get_json, run_command=run_command, download=github.download)
        self.assertFalse(any(str(cfg.activate) in " ".join(str(x) for x in c) for c in commands))

    def test_clean_checkout_uses_unprivileged_storage_builder_and_exact_sha_without_reset(self):
        puller = load_puller()
        commands = []
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self.config(tmp)

            def run_command(argv, **kwargs):
                commands.append(list(argv))
                return CompletedProcess(argv, 0, "", "")

            checkout = puller.clean_checkout(cfg, SHA1, run_command)
        self.assertEqual(checkout, cfg.work_dir / "checkout")
        rendered = [" ".join(str(x) for x in c) for c in commands]
        self.assertTrue(all(c[:7] == ["/usr/sbin/runuser", "-u", "storage-viz-builder", "--", "env", "-i", "HOME=/var/lib/storage-viz-dashboard/builder"] for c in commands))
        self.assertTrue(any("git clone --no-checkout --filter=blob:none" in r for r in rendered))
        self.assertTrue(any(f"git -C {checkout} fetch --depth 1 origin {SHA1}" in r for r in rendered))
        self.assertTrue(any(f"git -C {checkout} checkout --detach {SHA1}" in r for r in rendered))
        self.assertTrue(any(f"git -C {checkout} clean -xffd" in r for r in rendered))
        self.assertFalse(any(" reset " in f" {r} " for r in rendered))

    def test_managed_rollback_retains_validated_runtime_identity(self):
        puller = load_puller()
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self.config(tmp)
            self.write_activation_state(cfg.activation_state_path, sha=SHA1, digest=DIGEST, runtime_digest=DIGEST)
            payload = json.loads(cfg.activation_state_path.read_text(encoding="utf-8"))
            payload["status"] = "rolled_back"
            cfg.activation_state_path.write_text(json.dumps(payload), encoding="utf-8")
            self.assertEqual(puller.read_activation_state(cfg), {"current": SHA1, "runtime_digest": DIGEST})

    def test_manifest_validation_rejects_metadata_mismatch(self):
        puller = load_puller()
        with tempfile.TemporaryDirectory() as tmp:
            outdir = Path(tmp)
            self.write_artifact(outdir)
            metadata = outdir / f"storage-monitor-dashboard-{SHA1}.sha256.json"
            payload = json.loads(metadata.read_text(encoding="utf-8"))
            for key, value in [
                ("application_name", "wrong"),
                ("schema_version", 2),
                ("source_sha", SHA2),
                ("archive", "wrong.tar.gz"),
                ("sha256", DIGEST),
            ]:
                self.write_artifact(outdir)
                payload = json.loads(metadata.read_text(encoding="utf-8"))
                payload[key] = value
                metadata.write_text(json.dumps(payload), encoding="utf-8")
                with self.assertRaises(puller.PullError, msg=key):
                    puller.validate_artifact(outdir, SHA1)

    def test_activate_release_streams_artifact_to_option_style_cli_without_shell_or_buffering(self):
        puller = load_puller()
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self.config(tmp)
            artifact = Path(tmp) / f"storage-monitor-dashboard-{SHA1}.tar.gz"
            artifact.write_bytes(b"streamed-artifact")
            metadata = artifact.with_name(f"storage-monitor-dashboard-{SHA1}.sha256.json")
            metadata.write_text("{}", encoding="utf-8")
            digest = hashlib.sha256(b"streamed-artifact").hexdigest()
            seen = {}

            def run_command(argv, **kwargs):
                forbidden = {"upload", "status", "activate"}
                self.assertFalse(set(argv[1:]) & forbidden, f"legacy subcommand used: {argv}")
                self.assertIn("--artifact-stdin", argv)
                self.assertIn("--metadata", argv)
                self.assertEqual(argv[argv.index("--metadata") + 1], str(metadata))
                self.assertNotIn("input", kwargs)
                self.assertFalse(kwargs.get("shell", False))
                seen["payload"] = kwargs["stdin"].read()
                return CompletedProcess(argv, 0, json.dumps({
                    "status": "active",
                    "source_sha": SHA1,
                    "archive_digest": digest,
                    "release": f"/srv/storage-viz-dashboard/releases/{SHA1}/storage-monitor",
                }) + "\n", "")

            puller.activate_release(cfg, SHA1, digest, artifact, run_command)
        self.assertEqual(seen["payload"], b"streamed-artifact")

    def test_activate_release_reports_bounded_activator_error(self):
        puller = load_puller()
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self.config(tmp)
            artifact = Path(tmp) / f"storage-monitor-dashboard-{SHA1}.tar.gz"
            artifact.write_bytes(b"streamed-artifact")
            artifact.with_name(f"storage-monitor-dashboard-{SHA1}.sha256.json").write_text("{}", encoding="utf-8")
            digest = hashlib.sha256(b"streamed-artifact").hexdigest()

            def run_command(argv, **kwargs):
                return CompletedProcess(argv, 1, "", json.dumps({
                    "status": "error",
                    "error": "health check failed after activation",
                }) + "\n")

            with self.assertRaisesRegex(puller.PullError, "health check failed after activation"):
                puller.activate_release(cfg, SHA1, digest, artifact, run_command)

    def test_reconciles_missing_state_when_live_current_already_matches_main(self):
        puller = load_puller()
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self.config(tmp)
            github = FakeGitHub(SHA1)

            self.write_activation_state(cfg.activation_state_path, sha=SHA1, digest="")
            result = puller.run_once(cfg, get_json=github.get_json, run_command=lambda *a, **k: self.fail("unexpected command"))
            self.assertEqual(result, "reconciled-current")
            self.assertEqual((cfg.state_dir / "current-live-sha").read_text(encoding="utf-8").strip(), SHA1)

    def test_live_activation_reconciles_before_failed_release_backoff(self):
        puller = load_puller()
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self.config(tmp)
            github = FakeGitHub(SHA1)
            cfg.state_dir.mkdir(parents=True)
            self.write_activation_state(cfg.activation_state_path, sha=SHA1, digest=DIGEST)
            (cfg.state_dir / "failed-release.json").write_text(json.dumps({
                "sha": SHA1,
                "failures": 1,
                "retry_after": 9_999.0,
            }), encoding="utf-8")

            result = puller.run_once(
                cfg,
                get_json=github.get_json,
                run_command=lambda *a, **k: self.fail("already-active release must not rebuild"),
                now_fn=lambda: 1_000.0,
            )

            self.assertEqual(result, "reconciled-current")
            self.assertEqual((cfg.state_dir / "current-live-sha").read_text(encoding="utf-8").strip(), SHA1)
            self.assertFalse((cfg.state_dir / "failed-release.json").exists())

    def test_failed_release_uses_persistent_backoff_until_main_advances(self):
        puller = load_puller()
        commands = []
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self.config(tmp)
            github = FakeGitHub(SHA1)

            def run_command(argv, **kwargs):
                rendered = " ".join(str(x) for x in argv)
                commands.append(rendered)
                if argv[0] == str(cfg.activate):
                    self.fail("activator must not run after main advances before final authorization")
                if argv[0] == "python3" and str(cfg.authorizer) in argv:
                    return CompletedProcess(argv, 0, json.dumps({"authorized": True, "reason": "authorized", "sha": github.sha}) + "\n", "")
                if any(str(part).endswith("build-dashboard-release.py") for part in argv):
                    return CompletedProcess(argv, 12, "", "deterministic build failure")
                return CompletedProcess(argv, 0, "", "")

            with self.assertRaises(puller.PullError):
                puller.run_once(cfg, get_json=github.get_json, run_command=run_command, download=github.download, now_fn=iter((1_000.0, 2_200.0)).__next__)
            failure = json.loads((cfg.state_dir / "failed-release.json").read_text(encoding="utf-8"))
            self.assertEqual((failure["sha"], failure["failures"], failure["retry_after"]), (SHA1, 1, 3_100.0))
            command_count = len(commands)
            api_count = len(github.calls)
            result = puller.run_once(cfg, get_json=github.get_json, run_command=run_command, download=github.download, now_fn=lambda: 2_300.0)
            self.assertEqual(result, "backoff")
            self.assertEqual(len(commands), command_count)
            self.assertEqual(github.calls[api_count:], [f"/repos/{REPOSITORY}/git/ref/heads/main"])
            with self.assertRaises(puller.PullError):
                puller.run_once(cfg, get_json=github.get_json, run_command=run_command, download=github.download, now_fn=iter((3_100.0, 4_300.0)).__next__)
            second = json.loads((cfg.state_dir / "failed-release.json").read_text(encoding="utf-8"))
            self.assertEqual(second["failures"], 2)
            self.assertEqual(second["retry_after"], 6_100.0)
            github.sha = SHA2
            with self.assertRaises(puller.PullError):
                puller.run_once(cfg, get_json=github.get_json, run_command=run_command, download=github.download, now_fn=iter((4_400.0, 5_600.0)).__next__)
            advanced = json.loads((cfg.state_dir / "failed-release.json").read_text(encoding="utf-8"))
            self.assertEqual((advanced["sha"], advanced["failures"]), (SHA2, 1))

    def test_lock_returns_locked_without_doing_work(self):
        puller = load_puller()
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self.config(tmp)
            cfg.state_dir.mkdir(parents=True)
            lock = (cfg.state_dir / "puller.lock").open("a+")
            self.addCleanup(lock.close)
            puller.fcntl.flock(lock.fileno(), puller.fcntl.LOCK_EX | puller.fcntl.LOCK_NB)
            result = puller.run_once(cfg, get_json=lambda p: self.fail("no api"), run_command=lambda *a, **k: self.fail("no command"))
        self.assertEqual(result, "locked")

    def test_malformed_evidence_authorizer_output_and_timeout_fail_closed(self):
        puller = load_puller()
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self.config(tmp)
            with self.assertRaisesRegex(puller.PullError, "malformed current main"):
                puller.current_main_sha(cfg, lambda path: {"object": {"sha": "bad"}})
            with self.assertRaisesRegex(puller.PullError, "workflow"):
                puller.workflow_run_for_sha(cfg, SHA1, lambda path: {"workflow_runs": [{}]})
            workflow, checks, _ = puller.fetch_evidence(cfg, SHA1, FakeGitHub(SHA1).get_json)
            with self.assertRaisesRegex(puller.PullError, "malformed JSON"):
                puller.authorize(cfg, SHA1, workflow, checks, lambda argv, **kwargs: CompletedProcess(argv, 0, "not-json", ""))
            with self.assertRaisesRegex(puller.PullError, "unexpected authorization"):
                puller.authorize(cfg, SHA1, workflow, checks, lambda argv, **kwargs: CompletedProcess(argv, 0, json.dumps({"authorized": False}) + "\n", ""))
            with self.assertRaises(puller.PullError):
                puller.default_run_command([sys.executable, "-c", "import time; time.sleep(1)"], timeout=0.01)

    def test_fetches_all_check_run_pages_and_fails_closed_on_incomplete_total(self):
        puller = load_puller()
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self.config(tmp)
            calls = []

            def get_json(path):
                calls.append(path)
                if path.endswith("check-runs?per_page=100"):
                    return {"total_count": 101, "check_runs": [{"id": index} for index in range(100)]}
                if path.endswith("check-runs?per_page=100&page=2"):
                    return {"total_count": 101, "check_runs": [{"id": 101}]}
                raise AssertionError(path)

            self.assertEqual(len(puller.check_runs_for_sha(cfg, SHA1, get_json=get_json)), 101)
            self.assertEqual(len(calls), 2)
            with self.assertRaises(puller.PullError):
                puller.check_runs_for_sha(cfg, SHA1, get_json=lambda path: {"total_count": 101, "check_runs": [{"id": index} for index in range(99)]})

    def test_default_config_points_to_actual_monorepo_remote(self):
        puller = load_puller()
        cfg = puller.Config()
        self.assertEqual(cfg.repository, REPOSITORY)
        self.assertEqual(cfg.repo_url, REPO_URL)

    def test_default_config_uses_storage_owned_paths_without_gpu_runtime_coupling(self):
        puller = load_puller()
        cfg = puller.Config()
        self.assertEqual(cfg.state_dir, Path("/var/lib/storage-viz-dashboard/puller"))
        self.assertEqual(cfg.work_dir, Path("/var/lib/storage-viz-dashboard/builder"))
        self.assertEqual(cfg.authorizer, Path("/usr/local/libexec/storage-release-authorizer.py"))
        self.assertEqual(cfg.activate, Path("/usr/local/libexec/storage-dashboard-activate.py"))
        source = PULLER_PATH.read_text(encoding="utf-8").lower()
        forbidden = [
            "/opt/gpu-monitor",
            "/var/lib/gpu-monitor",
            "/etc/gpu-monitor",
            "/var/lock/gpu-monitor",
            "/srv/gpu-monitor",
            "/usr/local/libexec/gpu-monitor",
            "apps/gpu-monitor/deploy/server/gpu-monitor-release-puller.py",
            "gpu_monitor_release_puller",
            "gpu-monitor-release-puller.py",
            "gpu-monitor-release-puller.service",
            "gpu-monitor-release-puller.timer",
            "gpu-monitor-backend",
            "gpu-monitor-frontend",
            "gpu-monitor-bridge",
            "gpu-monitor-builder",
            "gpu-deploy-live",
            "gpu-deploy-dev",
            "gpu_monitor_backend_port",
            "gpu_monitor_bridge_port",
            "gpu_monitor_shared_dir",
            "5173",
            "5174",
            "8000",
            "8001",
            "8100",
            "8101",
        ]
        for needle in forbidden:
            self.assertNotIn(needle, source)
        self.assertIn("fcntl.flock", source)
        self.assertIn("storage-viz-builder", source)
        self.assertNotIn("shell=True", source)


@unittest.skipUnless(shutil.which("ssh-keygen"), "ssh-keygen is required")
class AgentBundleTest(unittest.TestCase):
    """Which agent bundle a dashboard release carries, and that only a verified one gets in."""

    def setUp(self):
        self.puller = load_puller()
        self.tmp = Path(tempfile.mkdtemp(prefix="storage-agent-fetch."))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        fixtures = load_agent_fixtures()
        self.fixtures = fixtures
        self.key = fixtures.SigningKey(self.tmp)
        self.signers = self.key.write_allowed_signers(self.tmp / "agent-allowed-signers")
        self.token = self.tmp / "github-token"
        self.token.write_text("ghs_test\n")
        self.bundle = fixtures.bundle(AGENT_SHA, release_seq=120, floor=100)
        self.cfg = self.puller.Config(
            repository=REPOSITORY, state_dir=self.tmp / "state", work_dir=self.tmp / "builder",
            authorizer=self.tmp / "authorizer.py", activate=self.tmp / "activate.py",
            activation_state_path=self.tmp / "activation-state.json", agent_allowed_signers=self.signers,
            github_token_file=self.token, agent_staging_dir=self.tmp / "agent-staging", release_root=self.tmp / "releases",
        )
        self.cfg.state_dir.mkdir()
        self.cfg.work_dir.mkdir()
        self.builds = []
        self.activations = []

    def signed(self, bundle=None, key=None, **overrides):
        bundle = bundle if bundle is not None else self.bundle
        return agent_files(bundle, (key or self.key).sign(bundle), **overrides)

    def fetch(self, files):
        return self.puller.fetch_agent_artifact(self.cfg, AGENT_ARTIFACT_ID, FakeGitHub(SHA1, agent_files=files).download,
                                                self.puller.default_run_command)

    def run_command(self, argv, **kwargs):
        if argv[0] == "ssh-keygen":
            return self.puller.default_run_command(argv, **kwargs)
        if argv[0] == "python3" and str(self.cfg.authorizer) in argv:
            return CompletedProcess(argv, 0, json.dumps({"authorized": True, "reason": "authorized", "sha": SHA1}), "")
        if any(str(part).endswith("build-dashboard-release.py") for part in argv):
            self.builds.append(list(argv))
            StorageReleasePullerTest().write_build_output(argv)
        if argv[0] == str(self.cfg.activate):
            outdir = Path(argv[argv.index("--metadata") + 1]).parent
            archive = next(outdir.glob("*.tar.gz"))
            with tarfile.open(archive) as tar:
                self.activations.append({m.name[len("storage-monitor/agent/"):]: tar.extractfile(m).read()
                                         for m in tar.getmembers() if m.name.startswith("storage-monitor/agent/")})
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            return CompletedProcess(argv, 0, json.dumps({
                "status": "active", "source_sha": SHA1, "archive_digest": digest,
                "runtime_digest": self.puller.validate_artifact(outdir, SHA1)[2],
                "release": f"/srv/storage-viz-dashboard/releases/{SHA1}/storage-monitor"}), "")
        return CompletedProcess(argv, 0, "", "")

    def pull(self, github):
        return self.puller.run_once(self.cfg, get_json=github.get_json, run_command=self.run_command, download=github.download)

    def install_previous_release(self, files):
        release = self.cfg.release_root / SHA2 / "storage-monitor"
        (release / "agent").mkdir(parents=True)
        for name, content in files.items():
            (release / "agent" / name).write_bytes(content)
        self.cfg.activation_state_path.write_text(json.dumps({"status": "active", "source_sha": SHA2, "release": str(release)}))

    # -- verification of a fresh bundle ------------------------------------------------

    def test_signed_bundle_is_returned_in_memory(self):
        files = self.signed()
        self.assertEqual(self.fetch(files), files)

    def test_tampered_or_foreign_bundles_are_refused(self):
        stranger = self.fixtures.SigningKey(self.tmp, "stranger")
        tampered = bytearray(self.bundle)
        tampered[len(tampered) // 2] ^= 1
        cases = {
            "tampered": agent_files(bytes(tampered), self.key.sign(self.bundle)),
            "stranger key": self.signed(key=stranger),
            "other namespace": agent_files(self.bundle, self.key.sign(self.bundle, namespace="file")),
        }
        for label, files in cases.items():
            with self.subTest(label):
                with self.assertRaisesRegex(self.puller.PullError, "signature|does not match|unreadable"):
                    self.fetch(files)

    def test_metadata_must_match_the_signed_manifest(self):
        for override in ({"source_sha": "4" * 40}, {"release_seq": 999}, {"rollback_floor_seq": 1}, {"size": 1}, {"sha256": "0" * 64}):
            with self.subTest(override):
                with self.assertRaisesRegex(self.puller.PullError, "does not match"):
                    self.fetch(self.signed(**override))

    def test_artifact_with_unexpected_files_is_refused(self):
        with self.assertRaisesRegex(self.puller.PullError, "unexpected files"):
            self.fetch(dict(self.signed(), **{"../escape": b"x"}))

    # -- which bundle a release carries ----------------------------------------------

    def test_fresh_signed_bundle_goes_into_the_release(self):
        files = self.signed()
        self.assertEqual(self.pull(FakeGitHub(SHA1, agent_files=files)), "activated")
        self.assertEqual(self.activations, [files])
        self.assertIn(str(self.cfg.agent_staging_dir), self.builds[0])

    def test_invalid_fresh_bundle_refuses_the_release(self):
        github = FakeGitHub(SHA1, agent_files=self.signed(key=self.fixtures.SigningKey(self.tmp, "stranger")))
        with self.assertRaisesRegex(self.puller.PullError, "signature"):
            self.pull(github)
        self.assertEqual((self.builds, self.activations), ([], []))
        self.assertTrue((self.cfg.state_dir / "failed-release.json").exists(), "an attack signal backs off like a failure")

    def test_no_pinned_key_or_no_token_releases_the_dashboard_without_a_bundle(self):
        for label, change in (("comment-only signers", lambda: self.signers.write_text("# no key yet\n")),
                              ("missing signers", lambda: self.signers.unlink()),
                              ("empty token", lambda: self.token.write_text(""))):
            with self.subTest(label):
                self.setUp()
                change()
                github = FakeGitHub(SHA1)
                self.assertEqual(self.pull(github), "activated")
                self.assertEqual(self.activations, [{}])
                self.assertEqual(github.downloads, [])
                self.assertFalse(any(path.startswith(f"/repos/{REPOSITORY}/actions/workflows/agent-bundle.yml") for path in github.calls))

    def test_running_signing_job_waits_without_backoff_or_build(self):
        github = FakeGitHub(SHA1, agent_runs=[{"id": 1, "event": "workflow_run", "head_branch": "main",
                                               "head_sha": SHA1, "status": "in_progress", "conclusion": None}])
        with self.assertRaisesRegex(self.puller.PullError, "still running"):
            self.pull(github)
        self.assertFalse((self.cfg.state_dir / "failed-release.json").exists())
        self.assertEqual(self.builds, [])

    def test_missing_signing_run_waits_for_a_grace_period_then_keeps_the_previous_bundle(self):
        previous = self.signed()
        self.install_previous_release(previous)
        github = FakeGitHub(SHA1, agent_runs=[])
        ci_runs = github.get_json

        def get_json(path, updated="2026-11-27T10:00:00Z"):
            payload = ci_runs(path)
            if "/actions/workflows/ci.yml/runs?" in path:
                payload["workflow_runs"][0]["updated_at"] = updated
            return payload

        ci_done = datetime(2026, 11, 27, 10, 0, tzinfo=timezone.utc).timestamp()
        with self.assertRaisesRegex(self.puller.PullError, "has not started yet"):
            self.puller.run_once(self.cfg, get_json=get_json, run_command=self.run_command,
                                 download=github.download, now_fn=lambda: ci_done + 60)
        self.assertEqual(self.builds, [])
        result = self.puller.run_once(self.cfg, get_json=get_json, run_command=self.run_command,
                                      download=github.download, now_fn=lambda: ci_done + 3600)
        self.assertEqual(result, "activated")
        self.assertEqual(self.activations, [previous])

    def test_failed_or_bundleless_signing_run_keeps_the_previous_verified_bundle(self):
        failed = [{"id": 5, "event": "workflow_run", "head_branch": "main", "head_sha": SHA1,
                   "status": "completed", "conclusion": "failure"}]
        for label, github in (("failed run", FakeGitHub(SHA1, agent_runs=failed)),
                              ("no artifact", FakeGitHub(SHA1, agent_files={}))):
            with self.subTest(label):
                self.setUp()
                previous = self.signed()
                self.install_previous_release(previous)
                if label == "no artifact":
                    github.get_json = self.without_artifacts(github.get_json)
                self.assertEqual(self.pull(github), "activated")
                self.assertEqual(self.activations, [previous])
                self.assertEqual(github.downloads, [])

    def without_artifacts(self, get_json):
        def wrapped(path):
            if path.endswith("/artifacts?per_page=100"):
                return {"artifacts": []}
            return get_json(path)
        return wrapped

    def test_previous_bundle_that_no_longer_verifies_is_dropped(self):
        stranger = self.fixtures.SigningKey(self.tmp, "stranger")
        self.install_previous_release(self.signed(key=stranger))
        github = FakeGitHub(SHA1, agent_runs=[{"id": 5, "event": "workflow_run", "head_branch": "main",
                                               "head_sha": SHA1, "status": "completed", "conclusion": "failure"}])
        self.assertEqual(self.pull(github), "activated")
        self.assertEqual(self.activations, [{}])

    def test_release_with_agent_files_nobody_verified_is_refused(self):
        self.signers.write_text("# no key\n")
        harness = StorageReleasePullerTest()

        def run_command(argv, **kwargs):
            if any(str(part).endswith("build-dashboard-release.py") for part in argv):
                harness.write_artifact(Path(argv[argv.index("--output-dir") + 1]), SHA1)  # carries agent files
                return CompletedProcess(argv, 0, "", "")
            return self.run_command(argv, **kwargs)

        github = FakeGitHub(SHA1)
        with self.assertRaisesRegex(self.puller.PullError, "exactly the verified agent bundle"):
            self.puller.run_once(self.cfg, get_json=github.get_json, run_command=run_command, download=github.download)

    # -- staging (review proof p6) ----------------------------------------------------

    def test_staging_never_follows_a_builder_planted_link(self):
        victim = self.tmp / "victim"
        victim.mkdir(mode=0o700)
        os.chmod(victim, 0o700)
        os.symlink(victim, self.cfg.work_dir / "agent-bundle")  # proof p6: the old staging path
        os.symlink(victim, self.cfg.agent_staging_dir)           # even at the new path
        staged = self.puller.stage_agent_bundle(self.cfg, self.signed())
        self.assertEqual(staged, self.cfg.agent_staging_dir)
        self.assertFalse(staged.is_symlink())
        self.assertEqual((stat.S_IMODE(victim.stat().st_mode), list(victim.iterdir())), (0o700, []))
        self.assertFalse(str(staged).startswith(str(self.cfg.work_dir)))
        self.assertEqual(sorted(p.name for p in staged.iterdir()), sorted(AGENT_FILES))
        self.assertTrue(all(stat.S_IMODE(p.stat().st_mode) == 0o644 for p in staged.iterdir()))

    def test_staging_refuses_a_writable_parent(self):
        os.chmod(self.tmp, 0o777)
        self.addCleanup(os.chmod, self.tmp, 0o700)
        with self.assertRaisesRegex(self.puller.PullError, "not group or world writable"):
            self.puller.stage_agent_bundle(self.cfg, self.signed())

    def test_build_output_is_checked_against_the_verified_bytes_not_the_staged_files(self):
        files = self.signed()
        original = self.run_command

        def run_command(argv, **kwargs):
            if any(str(part).endswith("build-dashboard-release.py") for part in argv):
                staged = Path(argv[argv.index("--agent-bundle-dir") + 1])
                (staged / AGENT_BUNDLE_NAME).write_bytes(b"swapped after staging")
            return original(argv, **kwargs)

        github = FakeGitHub(SHA1, agent_files=files)
        with self.assertRaisesRegex(self.puller.PullError, "exactly the verified agent bundle"):
            self.puller.run_once(self.cfg, get_json=github.get_json, run_command=run_command, download=github.download)
        self.assertEqual(self.activations, [])

    def test_default_download_sends_the_token_only_to_the_api(self):
        token_file = self.tmp / "token"
        token_file.write_text("ghs_test_token\n")
        seen = []

        class Opener:
            def open(self, request, timeout):
                seen.append(("api", request.full_url, request.get_header("Authorization")))
                raise self_error(request.full_url)

        def self_error(url):
            return self.puller.urllib.error.HTTPError(url, 302, "Found", {"Location": "https://blob.example.invalid/x?sig=1"}, None)

        class Response(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def urlopen(request, timeout):
            seen.append(("blob", request.full_url, request.get_header("Authorization")))
            return Response(b"zip-bytes")

        with mock.patch.object(self.puller.urllib.request, "build_opener", lambda *handlers: Opener()), \
                mock.patch.object(self.puller.urllib.request, "urlopen", urlopen):
            data = self.puller.default_download(f"/repos/{REPOSITORY}/actions/artifacts/1/zip", token_file)
        self.assertEqual(data, b"zip-bytes")
        self.assertEqual(seen, [
            ("api", f"https://api.github.com/repos/{REPOSITORY}/actions/artifacts/1/zip", "Bearer ghs_test_token"),
            ("blob", "https://blob.example.invalid/x?sig=1", None),
        ])
        with self.assertRaisesRegex(self.puller.PullError, "token file"):
            self.puller.default_download("/x", self.tmp / "absent")


if __name__ == "__main__":
    unittest.main()
