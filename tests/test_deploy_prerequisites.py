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
                "runnerAvailability": {"status": "ready", "evidence": "github-hosted runners available"},
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
                },
                "codeowners": {"present": True, "reviewRequired": True},
                "runnerAvailability": {"status": "ready", "evidence": "github-hosted runners available"},
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
                },
                "codeowners": {"present": True, "reviewRequired": True},
                "runnerAvailability": {"status": "ready", "evidence": "github-hosted runners available"},
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
            "runnerAvailability": {"status": "ready", "evidence": "available"},
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
                },
                "codeowners": {"present": True, "reviewRequired": True},
                "runnerAvailability": {"status": "ready", "evidence": "available"},
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
                        "runnerAvailability": {"status": "ready", "evidence": "available"},
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
                        "runnerAvailability": {"status": "ready", "evidence": "available"},
                        "serverReachability": {"status": "unknown", "evidence": "host check not requested"},
                    }
                )
                self.assertEqual(report["checks"]["protected_main"]["status"], "BLOCKED")


if __name__ == "__main__":
    unittest.main()
