
import hashlib
import importlib.util
import json
import tempfile
import sys
import unittest
from pathlib import Path
from subprocess import CompletedProcess


REPO_ROOT = Path(__file__).resolve().parents[1]
PULLER_PATH = REPO_ROOT / "apps/gpu-monitor/deploy/server/gpu-monitor-release-puller.py"
SHA1 = "1" * 40
SHA2 = "2" * 40
DIGEST = "a" * 64


def load_puller():
    spec = importlib.util.spec_from_file_location("gpu_monitor_release_puller", PULLER_PATH)
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
        if path == "/repos/IRCVLab/GPU_monitor/git/ref/heads/main":
            return {"object": {"sha": self.sha}}
        if path.startswith("/repos/IRCVLab/GPU_monitor/actions/workflows/ci.yml/runs?"):
            return {"workflow_runs": [{
                "id": 123,
                "name": "ci",
                "event": "push",
                "head_branch": "main",
                "head_sha": self.sha,
                "status": "completed",
                "conclusion": self.conclusion,
                "head_repository": {"full_name": "IRCVLab/GPU_monitor"},
                "path": ".github/workflows/ci.yml",
            }]}
        if path == f"/repos/IRCVLab/GPU_monitor/commits/{self.sha}/check-runs?per_page=100":
            return {"check_runs": [{
                "id": 99,
                "name": "ci/required",
                "head_sha": self.sha,
                "status": "completed",
                "conclusion": self.conclusion,
                "completed_at": "2026-07-25T01:02:03Z",
            }]}
        raise AssertionError(f"unexpected GitHub API path: {path}")


class GpuReleasePullerTest(unittest.TestCase):
    def config(self, tmpdir):
        puller = load_puller()
        return puller.Config(
            repository="IRCVLab/GPU_monitor",
            state_dir=Path(tmpdir) / "state",
            work_dir=Path(tmpdir) / "work",
            authorizer=Path(tmpdir) / "authorize_gpu_release.py",
            activate=Path(tmpdir) / "activate-release.sh",
            build_script="apps/gpu-monitor/deploy/build-release.sh",
            builder_user="gpu-monitor-builder",
            deploy_user="gpu-deploy-live",
            node_prefix=Path("/opt/gpu-monitor/node"),
            repo_url="https://github.com/IRCVLab/GPU_monitor.git",
            timeout_seconds=30,
        )

    def test_fetches_current_main_first_and_exits_cheaply_when_already_current(self):
        puller = load_puller()
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self.config(tmp)
            cfg.state_dir.mkdir(parents=True)
            (cfg.state_dir / "current-live-sha").write_text(SHA1 + "\n", encoding="utf-8")
            github = FakeGitHub(SHA1)

            result = puller.run_once(cfg, get_json=github.get_json, run_command=lambda *a, **k: self.fail("unexpected command"))

        self.assertEqual(result, "already-current")
        self.assertEqual(github.calls, ["/repos/IRCVLab/GPU_monitor/git/ref/heads/main"])

    def test_successful_change_authorizes_builds_validates_and_activates_in_order(self):
        puller = load_puller()
        commands = []
        activated = False
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self.config(tmp)
            github = FakeGitHub(SHA1)

            def run_command(argv, **kwargs):
                nonlocal activated
                commands.append((list(argv), kwargs.get("input")))
                if argv[0] == "runuser" and any(str(part).endswith("build-release.sh") for part in argv):
                    outdir = Path(argv[argv.index("--output-dir") + 1])
                    outdir.mkdir(parents=True, exist_ok=True)
                    artifact = outdir / f"gpu-monitor-{SHA1}.tar.gz"
                    artifact.write_bytes(b"artifact")
                    digest = hashlib.sha256(b"artifact").hexdigest()
                    (outdir / f"gpu-monitor-{SHA1}.sha256").write_text(f"{digest}  gpu-monitor-{SHA1}.tar.gz\n", encoding="utf-8")
                    (outdir / "release-manifest.json").write_text(json.dumps({
                        "application": "gpu-monitor", "artifact": artifact.name,
                        "git_sha": SHA1, "schema": 1, "sha256": digest,
                    }), encoding="utf-8")
                if argv[0] == "python3" and str(cfg.authorizer) in argv:
                    return CompletedProcess(argv, 0, '{"authorized": true, "sha": "' + SHA1 + '", "reason": "authorized"}\n', "")
                if "activate-release.sh" in " ".join(argv) and "activate live" in " ".join(argv):
                    activated = True
                if "activate-release.sh" in " ".join(argv) and "status live" in " ".join(argv):
                    current = f"releases/{SHA1}" if activated else ""
                    return CompletedProcess(argv, 0, json.dumps({"current": current, "environment": "live", "previous": ""}) + "\n", "")
                return CompletedProcess(argv, 0, "", "")

            result = puller.run_once(cfg, get_json=github.get_json, run_command=run_command)
            deployed_state = (cfg.state_dir / "current-live-sha").read_text(encoding="utf-8").strip()

        self.assertEqual(result, "activated")
        rendered = [" ".join(c[0]) for c in commands]
        self.assertLess(rendered.index(next(x for x in rendered if "git clone" in x)), rendered.index(next(x for x in rendered if "build-release.sh" in x)))
        activation_commands = [
            x for x in rendered
            if "activate-release.sh upload live" in x
            or "activate-release.sh activate live" in x
            or "activate-release.sh status live" in x
        ]
        self.assertEqual(activation_commands[0], f"runuser -u gpu-deploy-live -- {cfg.activate} status live")
        self.assertEqual(activation_commands[-3:], [
            f"runuser -u gpu-deploy-live -- {cfg.activate} upload live {SHA1} {hashlib.sha256(b'artifact').hexdigest()}",
            f"runuser -u gpu-deploy-live -- {cfg.activate} activate live {SHA1} {hashlib.sha256(b'artifact').hexdigest()}",
            f"runuser -u gpu-deploy-live -- {cfg.activate} status live",
        ])
        self.assertIn("/repos/IRCVLab/GPU_monitor/git/ref/heads/main", github.calls[0])
        self.assertGreaterEqual(github.calls.count("/repos/IRCVLab/GPU_monitor/git/ref/heads/main"), 2)
        self.assertEqual(deployed_state, SHA1)

    def test_matching_live_digest_records_new_sha_without_upload_or_activation(self):
        puller = load_puller()
        events = []
        live_digest = hashlib.sha256(b"artifact").hexdigest()
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self.config(tmp)
            cfg.state_dir.mkdir(parents=True)
            (cfg.state_dir / "current-live-sha").write_text(SHA2 + "\n", encoding="utf-8")
            (cfg.state_dir / "failed-release.json").write_text(
                json.dumps({"failures": 2, "retry_after": 1.0, "sha": SHA1}) + "\n",
                encoding="utf-8",
            )
            github = FakeGitHub(SHA1)

            def run_command(argv, **kwargs):
                rendered = " ".join(argv)
                if "activate-release.sh status live" in rendered:
                    events.append("status")
                    return CompletedProcess(
                        argv,
                        0,
                        json.dumps({
                            "current": f"releases/{SHA2}",
                            "current_sha256": live_digest,
                            "environment": "live",
                            "previous": "",
                        }) + "\n",
                        "",
                    )
                if argv[0] == "python3" and str(cfg.authorizer) in argv:
                    events.append("authorize")
                    return CompletedProcess(
                        argv,
                        0,
                        json.dumps({"authorized": True, "reason": "authorized", "sha": SHA1}) + "\n",
                        "",
                    )
                if "git clone" in rendered:
                    events.append("checkout")
                if any(str(part).endswith("build-release.sh") for part in argv):
                    events.append("build")
                    outdir = Path(argv[argv.index("--output-dir") + 1])
                    outdir.mkdir(parents=True, exist_ok=True)
                    artifact = outdir / f"gpu-monitor-{SHA1}.tar.gz"
                    artifact.write_bytes(b"artifact")
                    (outdir / f"gpu-monitor-{SHA1}.sha256").write_text(
                        f"{live_digest}  {artifact.name}\n",
                        encoding="utf-8",
                    )
                    (outdir / "release-manifest.json").write_text(
                        json.dumps({
                            "application": "gpu-monitor",
                            "artifact": artifact.name,
                            "git_sha": SHA1,
                            "schema": 1,
                            "sha256": live_digest,
                        }),
                        encoding="utf-8",
                    )
                if "activate-release.sh upload live" in rendered:
                    self.fail("matching live digest must not upload")
                if "activate-release.sh activate live" in rendered:
                    self.fail("matching live digest must not activate")
                return CompletedProcess(argv, 0, "", "")

            result = puller.run_once(cfg, get_json=github.get_json, run_command=run_command)

            self.assertEqual(result, "unchanged-artifact")
            self.assertEqual(
                (cfg.state_dir / "current-live-sha").read_text(encoding="utf-8").strip(),
                SHA1,
            )
            self.assertFalse((cfg.state_dir / "failed-release.json").exists())
            self.assertFalse((cfg.work_dir / "out").exists())

        self.assertEqual(events, ["status", "authorize", "checkout", "build", "authorize", "status"])
        self.assertGreaterEqual(github.calls.count("/repos/IRCVLab/GPU_monitor/git/ref/heads/main"), 3)

    def test_live_status_missing_digest_cannot_noop_and_malformed_digest_fails_closed(self):
        puller = load_puller()
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self.config(tmp)

            def status(payload):
                return puller.live_status(
                    cfg,
                    lambda argv, **kwargs: CompletedProcess(
                        argv, 0, json.dumps(payload) + "\n", ""
                    ),
                )

            self.assertEqual(
                status({
                    "current": f"releases/{SHA2}",
                    "environment": "live",
                    "previous": "",
                })["current_sha256"],
                "",
            )
            with self.assertRaisesRegex(puller.PullError, "malformed live status current_sha256"):
                status({
                    "current": f"releases/{SHA2}",
                    "current_sha256": "not-a-sha256",
                    "environment": "live",
                    "previous": "",
                })

    def test_aborts_without_upload_if_main_advances_before_final_authorization(self):
        puller = load_puller()
        github = FakeGitHub(SHA1)
        main_calls = 0
        commands = []
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self.config(tmp)

            def get_json(path):
                nonlocal main_calls
                if path == "/repos/IRCVLab/GPU_monitor/git/ref/heads/main":
                    main_calls += 1
                    return {"object": {"sha": SHA1 if main_calls < 3 else SHA2}}
                return github.get_json(path)

            def run_command(argv, **kwargs):
                commands.append(list(argv))
                if "activate-release.sh" in " ".join(argv) and "status live" in " ".join(argv):
                    return CompletedProcess(
                        argv,
                        0,
                        json.dumps({"current": "", "environment": "live", "previous": ""}) + "\n",
                        "",
                    )
                if argv[0] == "runuser" and any(str(part).endswith("build-release.sh") for part in argv):
                    outdir = Path(argv[argv.index("--output-dir") + 1]); outdir.mkdir(parents=True)
                    artifact = outdir / f"gpu-monitor-{SHA1}.tar.gz"; artifact.write_bytes(b"artifact")
                    digest = hashlib.sha256(b"artifact").hexdigest()
                    (outdir / f"gpu-monitor-{SHA1}.sha256").write_text(f"{digest}  {artifact.name}\n", encoding="utf-8")
                    (outdir / "release-manifest.json").write_text(json.dumps({"application":"gpu-monitor","artifact":artifact.name,"git_sha":SHA1,"schema":1,"sha256":digest}), encoding="utf-8")
                if argv[0] == "python3" and str(cfg.authorizer) in argv:
                    return CompletedProcess(argv, 0, '{"authorized": true, "sha": "' + SHA1 + '", "reason": "authorized"}\n', "")
                return CompletedProcess(argv, 0, "", "")

            with self.assertRaises(puller.PullError):
                puller.run_once(cfg, get_json=get_json, run_command=run_command)

        self.assertFalse(any("activate-release.sh upload live" in " ".join(c) for c in commands))
        self.assertFalse(any("activate-release.sh discard live" in " ".join(c) for c in commands))
        self.assertFalse(any("activate-release.sh activate live" in " ".join(c) for c in commands))

    def test_default_command_runner_accepts_binary_upload_input(self):
        puller = load_puller()

        result = puller.default_run_command(
            [
                sys.executable,
                "-c",
                "import hashlib,sys; print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())",
            ],
            input=b"artifact-bytes",
            timeout=10,
        )

        self.assertEqual(result.returncode, 0)
        self.assertEqual(
            result.stdout.strip(),
            hashlib.sha256(b"artifact-bytes").hexdigest(),
        )
        self.assertIsInstance(result.stdout, str)
        self.assertIsInstance(result.stderr, str)

    def test_default_command_runner_converts_timeout_to_pull_error(self):
        puller = load_puller()

        with self.assertRaises(puller.PullError):
            puller.default_run_command(
                [sys.executable, "-c", "import time; time.sleep(1)"],
                timeout=0.01,
            )

    def test_upload_streams_artifact_instead_of_loading_it_into_memory(self):
        puller = load_puller()
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self.config(tmp)
            artifact = Path(tmp) / "artifact.tar.gz"
            artifact.write_bytes(b"streamed-artifact")
            seen = {}

            def run_command(argv, **kwargs):
                self.assertNotIn("input", kwargs)
                seen["payload"] = kwargs["stdin"].read()
                return CompletedProcess(argv, 0, "", "")

            puller.upload_artifact(cfg, SHA1, DIGEST, artifact, run_command)

        self.assertEqual(seen["payload"], b"streamed-artifact")

    def test_reconciles_missing_state_when_local_current_already_matches_main(self):
        puller = load_puller()
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self.config(tmp)
            github = FakeGitHub(SHA1)

            def run_command(argv, **kwargs):
                if "activate-release.sh" in " ".join(argv) and "status live" in " ".join(argv):
                    return CompletedProcess(
                        argv,
                        0,
                        json.dumps({
                            "current": f"releases/{SHA1}",
                            "environment": "live",
                            "previous": f"releases/{SHA2}",
                        }) + "\n",
                        "",
                    )
                self.fail(f"unexpected command: {argv}")

            result = puller.run_once(cfg, get_json=github.get_json, run_command=run_command)

            self.assertEqual(result, "reconciled-current")
            self.assertEqual(
                (cfg.state_dir / "current-live-sha").read_text(encoding="utf-8").strip(),
                SHA1,
            )
            self.assertEqual(github.calls, ["/repos/IRCVLab/GPU_monitor/git/ref/heads/main"])

    def test_failed_release_uses_persistent_exponential_backoff_until_main_advances(self):
        puller = load_puller()
        commands = []
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self.config(tmp)
            github = FakeGitHub(SHA1)

            def run_command(argv, **kwargs):
                rendered = " ".join(argv)
                commands.append(rendered)
                if "activate-release.sh status live" in rendered:
                    return CompletedProcess(
                        argv,
                        0,
                        json.dumps({"current": "", "environment": "live", "previous": ""}) + "\n",
                        "",
                    )
                if argv[0] == "python3" and str(cfg.authorizer) in argv:
                    return CompletedProcess(
                        argv,
                        0,
                        json.dumps({
                            "authorized": True,
                            "reason": "authorized",
                            "sha": github.sha,
                        }) + "\n",
                        "",
                    )
                if any(str(part).endswith("build-release.sh") for part in argv):
                    return CompletedProcess(argv, 12, "", "deterministic build failure")
                return CompletedProcess(argv, 0, "", "")

            with self.assertRaises(puller.PullError):
                first_attempt_times = iter((1_000.0, 2_200.0))
                puller.run_once(
                    cfg,
                    get_json=github.get_json,
                    run_command=run_command,
                    now_fn=lambda: next(first_attempt_times),
                )

            failure = json.loads(
                (cfg.state_dir / "failed-release.json").read_text(encoding="utf-8")
            )
            self.assertEqual(failure["sha"], SHA1)
            self.assertEqual(failure["failures"], 1)
            self.assertEqual(failure["retry_after"], 3_100.0)

            command_count = len(commands)
            api_count = len(github.calls)
            result = puller.run_once(
                cfg,
                get_json=github.get_json,
                run_command=run_command,
                now_fn=lambda: 2_300.0,
            )

            self.assertEqual(result, "backoff")
            self.assertEqual(len(commands), command_count)
            self.assertEqual(
                github.calls[api_count:],
                ["/repos/IRCVLab/GPU_monitor/git/ref/heads/main"],
            )

            with self.assertRaises(puller.PullError):
                second_attempt_times = iter((3_100.0, 4_300.0))
                puller.run_once(
                    cfg,
                    get_json=github.get_json,
                    run_command=run_command,
                    now_fn=lambda: next(second_attempt_times),
                )
            second_failure = json.loads(
                (cfg.state_dir / "failed-release.json").read_text(encoding="utf-8")
            )
            self.assertEqual(second_failure["failures"], 2)
            self.assertEqual(second_failure["retry_after"], 6_100.0)

            github.sha = SHA2
            with self.assertRaises(puller.PullError):
                advanced_attempt_times = iter((4_400.0, 5_600.0))
                puller.run_once(
                    cfg,
                    get_json=github.get_json,
                    run_command=run_command,
                    now_fn=lambda: next(advanced_attempt_times),
                )
            advanced_failure = json.loads(
                (cfg.state_dir / "failed-release.json").read_text(encoding="utf-8")
            )
            self.assertEqual(advanced_failure["sha"], SHA2)
            self.assertEqual(advanced_failure["failures"], 1)

    def test_rejects_successful_activation_when_status_does_not_report_expected_sha(self):
        puller = load_puller()
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self.config(tmp)

            def run_command(argv, **kwargs):
                rendered = " ".join(argv)
                if "activate-release.sh activate live" in rendered:
                    return CompletedProcess(argv, 0, "", "")
                if "activate-release.sh status live" in rendered:
                    return CompletedProcess(
                        argv,
                        0,
                        json.dumps({"current": f"releases/{SHA2}", "environment": "live", "previous": ""}) + "\n",
                        "",
                    )
                self.fail(f"unexpected command: {argv}")

            with self.assertRaises(puller.PullError):
                puller.activate_uploaded(cfg, SHA1, DIGEST, run_command)

    def test_fetches_all_check_run_pages_fail_closed_on_incomplete_total(self):
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

            checks = puller.check_runs_for_sha(cfg, SHA1, get_json=get_json)
            self.assertEqual(len(checks), 101)
            self.assertEqual(len(calls), 2)

            def incomplete(path):
                if path.endswith("check-runs?per_page=100"):
                    return {"total_count": 101, "check_runs": [{"id": index} for index in range(99)]}
                raise AssertionError(path)

            with self.assertRaises(puller.PullError):
                puller.check_runs_for_sha(cfg, SHA1, get_json=incomplete)

    def test_failed_ci_missing_checks_or_bad_manifest_do_not_deploy(self):
        puller = load_puller()
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self.config(tmp)

            def status_only(argv, **kwargs):
                if "activate-release.sh" in " ".join(argv) and "status live" in " ".join(argv):
                    return CompletedProcess(
                        argv,
                        0,
                        json.dumps({"current": "", "environment": "live", "previous": ""}) + "\n",
                        "",
                    )
                self.fail(f"unexpected command after failed evidence: {argv}")

            with self.assertRaises(puller.PullError):
                puller.run_once(
                    cfg,
                    get_json=FakeGitHub(SHA1, conclusion="failure").get_json,
                    run_command=status_only,
                )

            def missing_checks(path):
                if path.endswith("/check-runs?per_page=100"):
                    return {"check_runs": []}
                return FakeGitHub(SHA1).get_json(path)
            with self.assertRaises(puller.PullError):
                puller.run_once(cfg, get_json=missing_checks, run_command=status_only)



    def test_builder_creates_output_dir_and_uses_checkout_absolute_build_script(self):
        puller = load_puller()
        commands = []
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self.config(tmp)
            checkout = Path(tmp) / "work" / "checkout"
            outdir = Path(tmp) / "work" / "out" / SHA1

            def run_command(argv, **kwargs):
                commands.append(list(argv))
                return CompletedProcess(argv, 0, "", "")

            result = puller.build_release(cfg, checkout, SHA1, run_command=run_command)

        self.assertEqual(result, outdir)
        rendered = [" ".join(command) for command in commands]
        self.assertTrue(any("runuser -u gpu-monitor-builder" in command and " mkdir -p " in command and str(outdir) in command for command in rendered))
        self.assertTrue(any(str(checkout / cfg.build_script) in command for command in rendered))
        self.assertFalse(any(" apps/gpu-monitor/deploy/build-release.sh " in command for command in rendered))


    def test_builder_output_cleanup_failure_fails_closed(self):
        puller = load_puller()
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self.config(tmp)
            checkout = Path(tmp) / "work" / "checkout"

            def run_command(argv, **kwargs):
                if "rm" in argv and "-rf" in argv:
                    return CompletedProcess(argv, 77, "", "rm denied")
                return CompletedProcess(argv, 0, "", "")

            with self.assertRaises(puller.PullError):
                puller.build_release(cfg, checkout, SHA1, run_command=run_command)

    def test_uses_builder_identity_without_live_env_and_independent_lock(self):
        puller = load_puller()
        source = PULLER_PATH.read_text(encoding="utf-8")
        self.assertIn("fcntl.flock", source)
        self.assertIn("puller.lock", source)
        self.assertIn("runuser", source)
        self.assertIn("gpu-monitor-builder", source)
        self.assertIn("/etc/gpu-monitor/live.env", source)
        self.assertIn("env -i", source)

if __name__ == "__main__":
    unittest.main()
