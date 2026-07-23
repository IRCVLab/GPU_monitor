import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts import check_deploy_prerequisites


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "check_deploy_prerequisites.py"


class DeployPrerequisitesTest(unittest.TestCase):
    def evaluate(self, metadata: dict, *args: str) -> tuple[int, dict, str]:
        with tempfile.TemporaryDirectory() as tempdir:
            metadata_file = Path(tempdir) / "metadata.json"
            metadata_file.write_text(json.dumps(metadata), encoding="utf-8")
            result = subprocess.run(
                ["python3.12", str(SCRIPT), "--metadata-file", str(metadata_file), *args],
                cwd=REPO_ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        self.assertNotIn("Traceback", result.stdout + result.stderr)
        report = json.loads(result.stdout)
        return result.returncode, report, result.stderr

    def test_private_repository_without_branch_protection_is_blocked(self):
        code, report, _stderr = self.evaluate(
            {
                "repository": {"nameWithOwner": "IRCVLab/GPU_monitor", "isPrivate": True},
                "defaultBranchRef": {"name": "main"},
                "branchProtectionRule": None,
                "codeowners": {"present": True, "reviewRequired": False},
                "runnerAvailability": {"runners": [{"name": "prod-1", "status": "online", "scope": "repo"}]},
                "serverReachability": {"status": "unknown", "evidence": "host check not requested"},
            }
        )

        self.assertEqual(code, 1)
        self.assertEqual(report["overall"], "BLOCKED")
        self.assertEqual(report["checks"]["protected_main"]["status"], "BLOCKED")
        self.assertIn("private-plan branch protection unavailable", report["checks"]["protected_main"]["evidence"])
        self.assertEqual(report["checks"]["codeowner_enforcement"]["status"], "BLOCKED")

    def test_protected_main_with_required_ci_and_code_owner_review_is_ready_for_runner_registration(self):
        code, report, _stderr = self.evaluate(
            {
                "repository": {"nameWithOwner": "IRCVLab/GPU_monitor", "isPrivate": True},
                "defaultBranchRef": {"name": "main"},
                "branchProtectionRule": {
                    "requiredStatusCheckContexts": ["ci/required"],
                    "requiresApprovingReviews": True,
                    "requiresCodeOwnerReviews": True,
                    "isAdminEnforced": True,
                    "allowsForcePushes": False,
                    "adminBypassAllowed": False,
                },
                "codeowners": {"present": True, "reviewRequired": True},
                "runnerAvailability": {"runners": [{"name": "prod-1", "status": "online", "scope": "repo"}]},
                "serverReachability": {"status": "unknown", "evidence": "host check not requested"},
            }
        )

        self.assertEqual(code, 0)
        self.assertEqual(report["overall"], "READY")
        self.assertEqual(report["ci_publication"], "READY")
        self.assertEqual(report["runner_registration"], "READY")
        self.assertEqual(report["cutover"], "UNKNOWN")
        self.assertEqual(report["checks"]["protected_main"]["status"], "READY")
        self.assertEqual(report["checks"]["codeowner_enforcement"]["status"], "READY")

    def test_missing_server_reachability_blocks_cutover_but_not_ci_publication(self):
        code, report, _stderr = self.evaluate(
            {
                "repository": {"nameWithOwner": "IRCVLab/GPU_monitor", "isPrivate": True},
                "defaultBranchRef": {"name": "main"},
                "branchProtectionRule": {
                    "requiredStatusCheckContexts": ["ci/required"],
                    "requiresApprovingReviews": True,
                    "requiresCodeOwnerReviews": True,
                    "isAdminEnforced": True,
                    "allowsForcePushes": False,
                    "adminBypassAllowed": False,
                },
                "codeowners": {"present": True, "reviewRequired": True},
                "runnerAvailability": {"runners": [{"name": "prod-1", "status": "online", "scope": "repo"}]},
                "serverReachability": {"status": "blocked", "evidence": "ssh timed out"},
            },
            "--require-host-for-cutover",
        )

        self.assertEqual(code, 1)
        self.assertEqual(report["overall"], "BLOCKED")
        self.assertEqual(report["ci_publication"], "READY")
        self.assertEqual(report["runner_registration"], "READY")
        self.assertEqual(report["cutover"], "BLOCKED")
        self.assertEqual(report["checks"]["server_reachability"]["status"], "BLOCKED")
        self.assertIn("ssh timed out", report["checks"]["server_reachability"]["evidence"])

    def test_missing_org_runner_group_permission_is_unknown_not_silently_ready(self):
        report = check_deploy_prerequisites.evaluate_metadata(
            {
                "repository": {"nameWithOwner": "IRCVLab/GPU_monitor", "isPrivate": True},
                "defaultBranchRef": {"name": "main"},
                "branchProtectionRule": {
                    "requiredStatusCheckContexts": ["ci/required"],
                    "requiresApprovingReviews": True,
                    "requiresCodeOwnerReviews": True,
                    "isAdminEnforced": True,
                    "allowsForcePushes": False,
                    "adminBypassAllowed": False,
                },
                "codeowners": {"present": True, "reviewRequired": True},
                "runnerAvailability": {
                    "status": "unknown",
                    "evidence": "gh api /orgs/IRCVLab/actions/runner-groups returned 403",
                },
                "serverReachability": {"status": "unknown", "evidence": "host check not requested"},
            }
        )

        self.assertEqual(report["overall"], "UNKNOWN")
        self.assertEqual(report["runner_registration"], "UNKNOWN")
        self.assertEqual(report["checks"]["runner_availability"]["status"], "UNKNOWN")
        self.assertIn("403", report["checks"]["runner_availability"]["evidence"])

    def test_host_check_parses_explicit_non_default_ssh_port(self):
        self.assertEqual(
            check_deploy_prerequisites.parse_host_target("166.104.167.11:2200"),
            ("166.104.167.11", 2200),
        )


    def test_explicit_host_check_uses_bounded_read_only_ssh_probe(self):
        calls = []

        def fake_run(argv, **kwargs):
            calls.append(argv)
            return subprocess.CompletedProcess(argv, 124, "", "ssh: connect to host timed out")

        report = check_deploy_prerequisites.inspect_host("166.104.167.11:2200", runner=fake_run)

        self.assertEqual(report["status"], "blocked")
        self.assertIn("timed out", report["evidence"])
        self.assertEqual(calls[0][-1], "true")
        self.assertIn("BatchMode=yes", calls[0])
        self.assertIn("ConnectTimeout=5", calls[0])
        self.assertIn("-p", calls[0])
        self.assertIn("2200", calls[0])

    def test_unknown_prerequisite_exits_nonzero_and_is_never_ready(self):
        code, report, _stderr = self.evaluate(
            {
                "repository": {"nameWithOwner": "IRCVLab/GPU_monitor", "isPrivate": True},
                "defaultBranchRef": {"name": "main"},
                "branchProtectionRule": {
                    "requiredStatusCheckContexts": ["ci/required"],
                    "requiresApprovingReviews": True,
                    "requiresCodeOwnerReviews": True,
                    "isAdminEnforced": True,
                    "allowsForcePushes": False,
                    "adminBypassAllowed": False,
                },
                "codeowners": {"present": True, "reviewRequired": True},
                "runnerAvailability": {"status": "unknown", "evidence": "runner API unavailable"},
                "serverReachability": {"status": "unknown", "evidence": "host check not requested"},
            }
        )

        self.assertEqual(code, 1)
        self.assertEqual(report["overall"], "UNKNOWN")
        self.assertEqual(report["runner_registration"], "UNKNOWN")

    def test_required_status_checks_accept_legacy_contexts_and_modern_checks_shape(self):
        base = {
            "repository": {"nameWithOwner": "IRCVLab/GPU_monitor", "isPrivate": True},
            "defaultBranchRef": {"name": "main"},
            "codeowners": {"present": True, "reviewRequired": True},
            "runnerAvailability": {"runners": [{"name": "prod-1", "status": "online", "scope": "repo"}]},
            "serverReachability": {"status": "unknown", "evidence": "host check not requested"},
        }
        for status_shape in (
            {"contexts": ["ci/required"]},
            {"checks": [{"context": "ci/required"}]},
        ):
            with self.subTest(status_shape=status_shape):
                metadata = dict(base)
                metadata["branchProtectionRule"] = {
                    "requiredStatusChecks": status_shape,
                    "requiredApprovingReviewCount": 1,
                    "requiresCodeOwnerReviews": True,
                    "isAdminEnforced": True,
                    "allowsForcePushes": False,
                    "adminBypassAllowed": False,
                }
                report = check_deploy_prerequisites.evaluate_metadata(metadata)
                self.assertEqual(report["checks"]["protected_main"]["status"], "READY")

    def test_null_required_status_checks_is_blocked(self):
        report = check_deploy_prerequisites.evaluate_metadata(
            {
                "repository": {"nameWithOwner": "IRCVLab/GPU_monitor", "isPrivate": True},
                "defaultBranchRef": {"name": "main"},
                "branchProtectionRule": {
                    "requiredStatusChecks": None,
                    "requiredApprovingReviewCount": 1,
                    "requiresCodeOwnerReviews": True,
                    "isAdminEnforced": True,
                    "allowsForcePushes": False,
                    "adminBypassAllowed": False,
                },
                "codeowners": {"present": True, "reviewRequired": True},
                "runnerAvailability": {"runners": [{"name": "prod-1", "status": "online", "scope": "repo"}]},
                "serverReachability": {"status": "unknown", "evidence": "host check not requested"},
            }
        )

        self.assertEqual(report["checks"]["protected_main"]["status"], "BLOCKED")
        self.assertIn("ci/required", report["checks"]["protected_main"]["evidence"])

    def test_codeowner_review_false_or_zero_approval_is_blocked(self):
        for codeowner_required, approving_count in ((False, 1), (True, 0)):
            with self.subTest(codeowner_required=codeowner_required, approving_count=approving_count):
                report = check_deploy_prerequisites.evaluate_metadata(
                    {
                        "repository": {"nameWithOwner": "IRCVLab/GPU_monitor", "isPrivate": True},
                        "defaultBranchRef": {"name": "main"},
                        "branchProtectionRule": {
                            "requiredStatusCheckContexts": ["ci/required"],
                            "requiredApprovingReviewCount": approving_count,
                            "requiresCodeOwnerReviews": codeowner_required,
                        },
                        "codeowners": {"present": True, "reviewRequired": codeowner_required},
                        "runnerAvailability": {"runners": [{"name": "prod-1", "status": "online", "scope": "repo"}]},
                        "serverReachability": {"status": "unknown", "evidence": "host check not requested"},
                    }
                )
                self.assertEqual(report["checks"]["codeowner_enforcement"]["status"], "BLOCKED")

    def test_upgrade_plan_branch_protection_403_is_blocked_but_generic_auth_errors_unknown(self):
        upgrade = check_deploy_prerequisites.classify_gh_api_failure(
            "repos/IRCVLab/GPU_monitor/branches/main/protection",
            1,
            "Upgrade to GitHub Pro or make this repository public to enable protected branches. (HTTP 403)",
        )
        auth = check_deploy_prerequisites.classify_gh_api_failure(
            "repos/IRCVLab/GPU_monitor/actions/runners",
            1,
            "Bad credentials (HTTP 401)",
        )

        self.assertEqual(upgrade["status"], "blocked")
        self.assertIn("Upgrade", upgrade["evidence"])
        self.assertEqual(auth["status"], "unknown")

    def test_missing_gh_binary_is_unknown_not_ready(self):
        failure = check_deploy_prerequisites.classify_gh_api_failure("repos/IRCVLab/GPU_monitor", 127, "gh: command not found")

        self.assertEqual(failure["status"], "unknown")
        self.assertIn("gh: command not found", failure["evidence"])

    def test_offline_runner_is_not_available(self):
        report = check_deploy_prerequisites.evaluate_metadata(
            {
                "repository": {"nameWithOwner": "IRCVLab/GPU_monitor", "isPrivate": True},
                "defaultBranchRef": {"name": "main"},
                "branchProtectionRule": {
                    "requiredStatusCheckContexts": ["ci/required"],
                    "requiredApprovingReviewCount": 1,
                    "requiresCodeOwnerReviews": True,
                    "isAdminEnforced": True,
                    "allowsForcePushes": False,
                    "adminBypassAllowed": False,
                },
                "codeowners": {"present": True, "reviewRequired": True},
                "runnerAvailability": {"runners": [{"name": "prod-1", "status": "offline"}]},
                "serverReachability": {"status": "unknown", "evidence": "host check not requested"},
            }
        )

        self.assertEqual(report["checks"]["runner_availability"]["status"], "BLOCKED")
        self.assertIn("offline", report["checks"]["runner_availability"]["evidence"])

    def test_malformed_metadata_file_exits_nonzero_without_live_fallback(self):
        with tempfile.TemporaryDirectory() as tempdir:
            metadata_file = Path(tempdir) / "metadata.json"
            metadata_file.write_text("{not-json", encoding="utf-8")
            result = subprocess.run(
                ["python3.12", str(SCRIPT), "--metadata-file", str(metadata_file)],
                cwd=REPO_ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("invalid metadata", result.stderr)
        self.assertNotIn("gh api", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_repo_argument_requires_owner_and_name(self):
        for bad_repo in ("IRCVLab", "IRCVLab/", "/GPU_monitor", "IRCVLab/GPU_monitor/extra", "IRCV Lab/GPU_monitor"):
            with self.subTest(bad_repo=bad_repo):
                result = subprocess.run(
                    ["python3.12", str(SCRIPT), "--repo", bad_repo],
                    cwd=REPO_ROOT,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                self.assertEqual(result.returncode, 2)
                self.assertIn("OWNER/REPO", result.stderr)

    def test_live_metadata_does_not_probe_host_without_explicit_check_host(self):
        calls = []
        original = check_deploy_prerequisites.inspect_host
        try:
            check_deploy_prerequisites.inspect_host = lambda host: calls.append(host) or {"status": "blocked", "evidence": "called"}
            metadata = check_deploy_prerequisites.metadata_from_api_payloads(
                repo="IRCVLab/GPU_monitor",
                repository={"full_name": "IRCVLab/GPU_monitor", "private": True, "default_branch": "main"},
                protection_error="Upgrade plan required (HTTP 403)",
                codeowners={"present": True, "reviewRequired": False},
                runner={"status": "unknown", "evidence": "runner permission unavailable"},
                check_host=None,
            )
        finally:
            check_deploy_prerequisites.inspect_host = original

        self.assertEqual(calls, [])
        self.assertEqual(metadata["serverReachability"]["status"], "unknown")

    def test_force_push_and_admin_bypass_are_not_ready(self):
        for field in ("allowsForcePushes", "adminBypassAllowed"):
            with self.subTest(field=field):
                rule = {
                    "requiredStatusCheckContexts": ["ci/required"],
                    "requiredApprovingReviewCount": 1,
                    "requiresCodeOwnerReviews": True,
                    "isAdminEnforced": True,
                    "allowsForcePushes": False,
                    "adminBypassAllowed": False,
                }
                rule[field] = True
                report = check_deploy_prerequisites.evaluate_metadata(
                    {
                        "repository": {"nameWithOwner": "IRCVLab/GPU_monitor", "isPrivate": True},
                        "defaultBranchRef": {"name": "main"},
                        "branchProtectionRule": rule,
                        "codeowners": {"present": True, "reviewRequired": True},
                        "runnerAvailability": {"runners": [{"name": "prod-1", "status": "online", "scope": "repo"}]},
                        "serverReachability": {"status": "unknown", "evidence": "host check not requested"},
                    }
                )
                self.assertEqual(report["checks"]["protected_main"]["status"], "BLOCKED")


    def ready_metadata(self) -> dict:
        return {
            "repository": {"nameWithOwner": "IRCVLab/GPU_monitor", "isPrivate": True},
            "defaultBranchRef": {"name": "main"},
            "branchProtectionRule": {
                "requiredStatusCheckContexts": ["ci/required"],
                "requiredApprovingReviewCount": 1,
                "requiresCodeOwnerReviews": True,
                "isAdminEnforced": True,
                "allowsForcePushes": False,
                "adminBypassAllowed": False,
            },
            "codeowners": {"present": True, "reviewRequired": True},
            "runnerAvailability": {"runners": [{"name": "prod-1", "status": "online", "scope": "repo"}]},
            "serverReachability": {"status": "unknown", "evidence": "host check not requested"},
        }

    def test_branch_protection_missing_explicit_admin_and_force_push_evidence_is_unknown_not_ready(self):
        for omitted in ("isAdminEnforced", "allowsForcePushes", "adminBypassAllowed"):
            with self.subTest(omitted=omitted):
                metadata = self.ready_metadata()
                del metadata["branchProtectionRule"][omitted]

                report = check_deploy_prerequisites.evaluate_metadata(metadata)

                self.assertEqual(report["checks"]["protected_main"]["status"], "UNKNOWN")
                self.assertIn(omitted, report["checks"]["protected_main"]["evidence"])
                self.assertEqual(report["overall"], "UNKNOWN")

    def test_branch_protection_explicit_admin_not_enforced_is_blocked(self):
        metadata = self.ready_metadata()
        metadata["branchProtectionRule"]["isAdminEnforced"] = False

        report = check_deploy_prerequisites.evaluate_metadata(metadata)

        self.assertEqual(report["checks"]["protected_main"]["status"], "BLOCKED")
        self.assertIn("administrator bypass", report["checks"]["protected_main"]["evidence"])

    def test_host_target_rejects_option_injection_and_invalid_targets(self):
        bad_targets = (
            "-oProxyCommand=sh",
            " host.example.com",
            "host.example.com ",
            "host\n.example.com",
            "user name@host.example.com",
            "user@-host.example.com",
            "user@host.example.com:0",
            "user@host.example.com:65536",
            "user@host.example.com:notaport",
            "bad/user@host.example.com",
            "user@bad_host!",
        )
        for target in bad_targets:
            with self.subTest(target=target):
                with self.assertRaises(ValueError):
                    check_deploy_prerequisites.parse_host_target(target)

    def test_host_probe_passes_subprocess_timeout(self):
        calls = []

        def fake_run(argv, **kwargs):
            calls.append((argv, kwargs))
            return subprocess.CompletedProcess(argv, 0, "", "")

        report = check_deploy_prerequisites.inspect_host("deploy@example.com:2200", runner=fake_run)

        self.assertEqual(report["status"], "ready")
        self.assertEqual(calls[0][1].get("timeout"), 10)
        self.assertIn("deploy@example.com", calls[0][0])

    def test_metadata_file_with_check_host_performs_fresh_probe_and_overrides_stale_reachability(self):
        with tempfile.TemporaryDirectory() as tempdir:
            metadata = self.ready_metadata()
            metadata["serverReachability"] = {"status": "ready", "evidence": "stale prior success"}
            metadata_file = Path(tempdir) / "metadata.json"
            metadata_file.write_text(json.dumps(metadata), encoding="utf-8")
            calls = []

            def fake_inspect(host):
                calls.append(host)
                return {"status": "blocked", "evidence": "fresh probe failed"}

            original = check_deploy_prerequisites.inspect_host
            try:
                check_deploy_prerequisites.inspect_host = fake_inspect
                stdout = io.StringIO()
                stderr = io.StringIO()
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    code = check_deploy_prerequisites.main([
                        "--metadata-file",
                        str(metadata_file),
                        "--check-host",
                        "example.com:2200",
                        "--require-host-for-cutover",
                    ])
            finally:
                check_deploy_prerequisites.inspect_host = original

        self.assertEqual(calls, ["example.com:2200"])
        self.assertEqual(code, 1)
        report = json.loads(stdout.getvalue())
        self.assertEqual(report["checks"]["server_reachability"]["status"], "BLOCKED")
        self.assertIn("fresh probe failed", report["checks"]["server_reachability"]["evidence"])
        self.assertEqual(stderr.getvalue(), "")


    def test_live_runner_availability_enumerates_repo_and_org_runners(self):
        calls = []

        def fake_api(path):
            calls.append(path)
            if path == "repos/IRCVLab/GPU_monitor/actions/runners":
                return {"runners": [{"name": "repo-prod", "status": "offline"}]}
            if path == "orgs/IRCVLab/actions/runners":
                return {"runners": [{"name": "org-prod", "status": "online"}]}
            self.fail(f"unexpected API path {path}")

        original = check_deploy_prerequisites.run_gh_api
        try:
            check_deploy_prerequisites.run_gh_api = fake_api
            runner = check_deploy_prerequisites.fetch_runner_availability("IRCVLab/GPU_monitor")
        finally:
            check_deploy_prerequisites.run_gh_api = original

        self.assertEqual(calls, ["repos/IRCVLab/GPU_monitor/actions/runners", "orgs/IRCVLab/actions/runners"])
        self.assertEqual([item["name"] for item in runner["runners"]], ["repo-prod", "org-prod"])
        self.assertEqual(check_deploy_prerequisites.runner_check({"runnerAvailability": runner})["status"], "UNKNOWN")

    def test_enumerated_no_runner_is_blocked_not_ready(self):
        result = check_deploy_prerequisites.runner_check({"runnerAvailability": {"runners": []}})

        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("no repository-scoped runner", result["evidence"])

    def test_live_runner_permission_uncertainty_is_unknown(self):
        def fake_api(path):
            if path == "repos/IRCVLab/GPU_monitor/actions/runners":
                return {"runners": []}
            raise check_deploy_prerequisites.GhApiError(path, 1, "Resource not accessible by integration (HTTP 403)")

        original = check_deploy_prerequisites.run_gh_api
        try:
            check_deploy_prerequisites.run_gh_api = fake_api
            runner = check_deploy_prerequisites.fetch_runner_availability("IRCVLab/GPU_monitor")
        finally:
            check_deploy_prerequisites.run_gh_api = original

        self.assertEqual(runner["status"], "unknown")
        self.assertIn("HTTP 403", runner["evidence"])

    def test_runner_group_readability_without_online_runner_is_unknown_not_ready(self):
        report = check_deploy_prerequisites.evaluate_metadata(
            {
                **self.ready_metadata(),
                "runnerAvailability": {
                    "status": "ready",
                    "evidence": "org runner-group API is readable; runner registration permission can be evaluated",
                },
            }
        )

        self.assertEqual(report["checks"]["runner_availability"]["status"], "UNKNOWN")
        self.assertIn("no eligible online runner", report["checks"]["runner_availability"]["evidence"])


    def test_org_scoped_online_runner_without_repo_eligibility_is_unknown_not_ready(self):
        metadata = self.ready_metadata()
        metadata["runnerAvailability"] = {
            "runners": [
                {"name": "org-prod", "status": "online", "scope": "org"},
            ],
            "evidence": "repo runners enumerated: 0; org runners enumerated: 1",
        }

        report = check_deploy_prerequisites.evaluate_metadata(metadata)

        self.assertEqual(report["checks"]["runner_availability"]["status"], "UNKNOWN")
        self.assertEqual(report["overall"], "UNKNOWN")
        self.assertIn("repository-scoped", report["checks"]["runner_availability"]["evidence"])


    def test_online_runner_with_explicit_repository_eligibility_is_ready(self):
        metadata = self.ready_metadata()
        metadata["runnerAvailability"] = {
            "runners": [
                {"name": "org-prod", "status": "online", "scope": "org", "repositoryEligible": True},
            ],
            "evidence": "runner group membership explicitly includes this repository",
        }

        report = check_deploy_prerequisites.evaluate_metadata(metadata)

        self.assertEqual(report["checks"]["runner_availability"]["status"], "READY")
        self.assertEqual(report["overall"], "READY")

    def test_repo_scoped_online_runner_is_ready_even_when_org_enumeration_is_forbidden(self):
        calls = []

        def fake_api(path):
            calls.append(path)
            if path == "repos/IRCVLab/GPU_monitor/actions/runners":
                return {"runners": [{"name": "repo-prod", "status": "online"}]}
            if path == "orgs/IRCVLab/actions/runners":
                raise check_deploy_prerequisites.GhApiError(path, 1, "Resource not accessible by integration (HTTP 403)")
            self.fail(f"unexpected API path {path}")

        original = check_deploy_prerequisites.run_gh_api
        try:
            check_deploy_prerequisites.run_gh_api = fake_api
            runner = check_deploy_prerequisites.fetch_runner_availability("IRCVLab/GPU_monitor")
        finally:
            check_deploy_prerequisites.run_gh_api = original

        self.assertEqual(calls, ["repos/IRCVLab/GPU_monitor/actions/runners", "orgs/IRCVLab/actions/runners"])
        self.assertIn("org runner enumeration unavailable", runner["evidence"])
        self.assertEqual(check_deploy_prerequisites.runner_check({"runnerAvailability": runner})["status"], "READY")

    def test_live_org_online_runner_does_not_make_repo_runner_readiness_ready(self):
        def fake_api(path):
            if path == "repos/IRCVLab/GPU_monitor/actions/runners":
                return {"runners": []}
            if path == "orgs/IRCVLab/actions/runners":
                return {"runners": [{"name": "org-prod", "status": "online"}]}
            self.fail(f"unexpected API path {path}")

        original = check_deploy_prerequisites.run_gh_api
        try:
            check_deploy_prerequisites.run_gh_api = fake_api
            runner = check_deploy_prerequisites.fetch_runner_availability("IRCVLab/GPU_monitor")
        finally:
            check_deploy_prerequisites.run_gh_api = original

        result = check_deploy_prerequisites.runner_check({"runnerAvailability": runner})
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertIn("repository-scoped", result["evidence"])

    def test_live_zero_or_offline_repo_runners_are_not_ready(self):
        for repo_runners, expected_status, evidence_text in (
            ([], "BLOCKED", "no repository-scoped runner"),
            ([{"name": "repo-prod", "status": "offline"}], "BLOCKED", "offline"),
        ):
            with self.subTest(repo_runners=repo_runners):
                def fake_api(path):
                    if path == "repos/IRCVLab/GPU_monitor/actions/runners":
                        return {"runners": repo_runners}
                    if path == "orgs/IRCVLab/actions/runners":
                        return {"runners": []}
                    self.fail(f"unexpected API path {path}")

                original = check_deploy_prerequisites.run_gh_api
                try:
                    check_deploy_prerequisites.run_gh_api = fake_api
                    runner = check_deploy_prerequisites.fetch_runner_availability("IRCVLab/GPU_monitor")
                finally:
                    check_deploy_prerequisites.run_gh_api = original

                result = check_deploy_prerequisites.runner_check({"runnerAvailability": runner})
                self.assertEqual(result["status"], expected_status)
                self.assertIn(evidence_text, result["evidence"])

    def test_fetch_codeowners_only_404_is_absent_auth_errors_fail_closed(self):
        calls = []

        def fake_api(path):
            calls.append(path)
            if path.endswith(".github/CODEOWNERS?ref=main"):
                raise check_deploy_prerequisites.GhApiError(path, 1, "Not Found (HTTP 404)")
            raise check_deploy_prerequisites.GhApiError(path, 1, "Bad credentials (HTTP 401)")

        original = check_deploy_prerequisites.run_gh_api
        try:
            check_deploy_prerequisites.run_gh_api = fake_api
            result = check_deploy_prerequisites.fetch_codeowners("IRCVLab/GPU_monitor", "main")
        finally:
            check_deploy_prerequisites.run_gh_api = original

        self.assertEqual(result["present"], None)
        self.assertEqual(result["status"], "unknown")
        self.assertIn("HTTP 401", result["evidence"])
        self.assertEqual(len(calls), 2)

    def test_metadata_from_api_payloads_copies_inputs_before_mutation(self):
        codeowners = {"present": True, "reviewRequired": False}
        runner = {"runners": [{"name": "prod-1", "status": "online"}]}
        metadata = check_deploy_prerequisites.metadata_from_api_payloads(
            repo="IRCVLab/GPU_monitor",
            repository={"full_name": "IRCVLab/GPU_monitor", "private": True, "default_branch": "main"},
            protection={
                "required_status_checks": {"contexts": ["ci/required"]},
                "required_pull_request_reviews": {
                    "required_approving_review_count": 1,
                    "require_code_owner_reviews": True,
                },
                "enforce_admins": {"enabled": True},
                "allow_force_pushes": {"enabled": False},
            },
            codeowners=codeowners,
            runner=runner,
        )

        self.assertEqual(codeowners, {"present": True, "reviewRequired": False})
        self.assertIsNot(metadata["codeowners"], codeowners)
        self.assertIsNot(metadata["runnerAvailability"], runner)
        self.assertTrue(metadata["codeowners"]["reviewRequired"])


if __name__ == "__main__":
    unittest.main()
