import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from subprocess import CompletedProcess


REPO_ROOT = Path(__file__).resolve().parents[1]
PULLER_PATH = REPO_ROOT / "apps/storage-monitor/deploy/server/storage-monitor-release-puller.py"
SHA1 = "1" * 40
SHA2 = "2" * 40
DIGEST = "a" * 64
REPOSITORY = "IRCVLab/GPU_monitor"
REPO_URL = "https://github.com/IRCVLab/GPU_monitor.git"


def load_puller():
    spec = importlib.util.spec_from_file_location("storage_monitor_release_puller", PULLER_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FakeGitHub:
    def __init__(self, sha=SHA1, conclusion="success"):
        self.sha = sha
        self.conclusion = conclusion
        self.calls = []

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
        raise AssertionError(f"unexpected GitHub API path: {path}")


class StorageReleasePullerTest(unittest.TestCase):
    maxDiff = None

    def config(self, tmpdir):
        puller = load_puller()
        return puller.Config(
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

    def write_artifact(self, outdir, sha=SHA1, content=b"artifact"):
        outdir.mkdir(parents=True, exist_ok=True)
        archive = outdir / f"storage-monitor-dashboard-{sha}.tar.gz"
        archive.write_bytes(content)
        digest = hashlib.sha256(content).hexdigest()
        (outdir / f"storage-monitor-dashboard-{sha}.sha256.json").write_text(json.dumps({
            "application_name": "storage-monitor",
            "archive": archive.name,
            "artifact_format_version": 1,
            "schema_version": 1,
            "sha256": digest,
            "source_sha": sha,
        }, sort_keys=True) + "\n", encoding="utf-8")
        return archive, digest

    def write_activation_state(self, path, *, sha="", digest=""):
        if sha:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({
                "status": "active",
                "source_sha": sha,
                "archive_digest": digest,
                "release": f"/srv/storage-viz-dashboard/releases/{sha}/storage-monitor",
            }) + "\n", encoding="utf-8")

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
                        "release": f"/srv/storage-viz-dashboard/releases/{SHA1}/storage-monitor",
                    }) + "\n", "")
                if argv[0] == "python3" and str(cfg.authorizer) in argv:
                    return CompletedProcess(argv, 0, json.dumps({"authorized": True, "reason": "authorized", "sha": SHA1}) + "\n", "")
                if any(str(part).endswith("build-dashboard-release.py") for part in argv):
                    outdir = Path(argv[argv.index("--output-dir") + 1])
                    self.write_artifact(outdir)
                return CompletedProcess(argv, 0, "", "")

            result = puller.run_once(cfg, get_json=github.get_json, run_command=run_command)
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
        live_digest = hashlib.sha256(b"artifact").hexdigest()
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self.config(tmp)
            cfg.state_dir.mkdir(parents=True)
            (cfg.state_dir / "current-live-sha").write_text(SHA2 + "\n", encoding="utf-8")
            (cfg.state_dir / "failed-release.json").write_text(json.dumps({"failures": 2, "retry_after": 1.0, "sha": SHA1}) + "\n", encoding="utf-8")
            self.write_activation_state(cfg.activation_state_path, sha=SHA2, digest=live_digest)
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
                    self.write_artifact(Path(argv[argv.index("--output-dir") + 1]))
                return CompletedProcess(argv, 0, "", "")

            result = puller.run_once(cfg, get_json=github.get_json, run_command=run_command)
            self.assertEqual(result, "unchanged-artifact")
            self.assertEqual((cfg.state_dir / "current-live-sha").read_text(encoding="utf-8").strip(), SHA1)
            self.assertFalse((cfg.state_dir / "failed-release.json").exists())
            self.assertFalse((cfg.work_dir / "out").exists())
        self.assertEqual(events, ["authorize", "checkout", "build", "authorize"])

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
                    self.write_artifact(Path(argv[argv.index("--output-dir") + 1]))
                return CompletedProcess(argv, 0, "", "")

            with self.assertRaisesRegex(puller.PullError, "current main changed"):
                puller.run_once(cfg, get_json=get_json, run_command=run_command)
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
        self.assertTrue(all(c[:7] == ["runuser", "-u", "storage-viz-builder", "--", "env", "-i", "HOME=/var/lib/storage-viz-dashboard/builder"] for c in commands))
        self.assertTrue(any("git clone --no-checkout --filter=blob:none" in r for r in rendered))
        self.assertTrue(any(f"git -C {checkout} fetch --depth 1 origin {SHA1}" in r for r in rendered))
        self.assertTrue(any(f"git -C {checkout} checkout --detach {SHA1}" in r for r in rendered))
        self.assertTrue(any(f"git -C {checkout} clean -xffd" in r for r in rendered))
        self.assertFalse(any(" reset " in f" {r} " for r in rendered))

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

    def test_reconciles_missing_state_when_live_current_already_matches_main(self):
        puller = load_puller()
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self.config(tmp)
            github = FakeGitHub(SHA1)

            self.write_activation_state(cfg.activation_state_path, sha=SHA1, digest="")
            result = puller.run_once(cfg, get_json=github.get_json, run_command=lambda *a, **k: self.fail("unexpected command"))
            self.assertEqual(result, "reconciled-current")
            self.assertEqual((cfg.state_dir / "current-live-sha").read_text(encoding="utf-8").strip(), SHA1)

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
                puller.run_once(cfg, get_json=github.get_json, run_command=run_command, now_fn=iter((1_000.0, 2_200.0)).__next__)
            failure = json.loads((cfg.state_dir / "failed-release.json").read_text(encoding="utf-8"))
            self.assertEqual((failure["sha"], failure["failures"], failure["retry_after"]), (SHA1, 1, 3_100.0))
            command_count = len(commands)
            api_count = len(github.calls)
            result = puller.run_once(cfg, get_json=github.get_json, run_command=run_command, now_fn=lambda: 2_300.0)
            self.assertEqual(result, "backoff")
            self.assertEqual(len(commands), command_count)
            self.assertEqual(github.calls[api_count:], [f"/repos/{REPOSITORY}/git/ref/heads/main"])
            with self.assertRaises(puller.PullError):
                puller.run_once(cfg, get_json=github.get_json, run_command=run_command, now_fn=iter((3_100.0, 4_300.0)).__next__)
            second = json.loads((cfg.state_dir / "failed-release.json").read_text(encoding="utf-8"))
            self.assertEqual(second["failures"], 2)
            self.assertEqual(second["retry_after"], 6_100.0)
            github.sha = SHA2
            with self.assertRaises(puller.PullError):
                puller.run_once(cfg, get_json=github.get_json, run_command=run_command, now_fn=iter((4_400.0, 5_600.0)).__next__)
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


if __name__ == "__main__":
    unittest.main()
