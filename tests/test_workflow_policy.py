import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "validate_workflows.py"
PINNED_SHA = "0123456789abcdef0123456789abcdef01234567"
UPPER_SHA = "0123456789ABCDEF0123456789ABCDEF01234567"


class WorkflowPolicyTest(unittest.TestCase):
    def run_validator(self, workflows: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            workflow_dir = root / ".github" / "workflows"
            if workflows is not None:
                workflow_dir.mkdir(parents=True)
                for name, body in workflows.items():
                    (workflow_dir / name).write_text(textwrap.dedent(body).lstrip(), encoding="utf-8")
            return subprocess.run(
                ["python3.12", str(SCRIPT), str(workflow_dir)],
                cwd=REPO_ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

    def assert_policy_violation(
        self,
        workflow: str,
        rule: str,
        job: str | None = None,
        filename: str = "policy.yml",
    ) -> None:
        result = self.run_validator({filename: workflow})
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        output = result.stdout + result.stderr
        self.assertIn(filename, output)
        self.assertIn(rule, output)
        self.assertNotIn("Traceback", output)
        if job is not None:
            self.assertIn(f"job {job}", output)

    def test_accepts_sha_pinned_actions_and_github_hosted_pull_request_jobs(self):
        result = self.run_validator(
            {
                "ci.yml": f"""
                name: ci
                on:
                  pull_request:
                permissions: read-all
                jobs:
                  unit:
                    runs-on: ubuntu-latest
                    steps:
                      - uses: actions/checkout@{PINNED_SHA}
                      - run: python -m unittest
                """
            }
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("OK: workflow policy validated", result.stdout)

    def test_accepts_sha_pinned_reusable_workflow_jobs(self):
        result = self.run_validator(
            {
                "reuse.yaml": f"""
                on: push
                jobs:
                  shared:
                    uses: octo-org/repo/.github/workflows/reuse.yml@{PINNED_SHA}
                """
            }
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_rejects_step_level_mutable_uses_tags(self):
        self.assert_policy_violation(
            """
            on: push
            jobs:
              test:
                runs-on: ubuntu-latest
                steps:
                  - uses: actions/checkout@v4
            """,
            "pinned-action-sha",
            "test",
        )

    def test_rejects_job_level_mutable_reusable_workflow_uses_tags(self):
        self.assert_policy_violation(
            """
            on: push
            jobs:
              reuse:
                uses: octo-org/repo/.github/workflows/reuse.yml@main
            """,
            "pinned-action-sha",
            "reuse",
        )

    def test_rejects_non_lowercase_or_wrong_length_uses_pins(self):
        for uses_value in (
            f"actions/checkout@{UPPER_SHA}",
            f"actions/checkout@{PINNED_SHA}0",
            f"actions/checkout@{PINNED_SHA[:-1]}",
        ):
            with self.subTest(uses_value=uses_value):
                self.assert_policy_violation(
                    f"""
                    on: push
                    jobs:
                      test:
                        runs-on: ubuntu-latest
                        steps:
                          - uses: {uses_value}
                    """,
                    "pinned-action-sha",
                    "test",
                )

    def test_rejects_write_all_permissions(self):
        self.assert_policy_violation(
            """
            on: push
            permissions: write-all
            jobs:
              test:
                runs-on: ubuntu-latest
                steps:
                  - run: true
            """,
            "permissions-write-all",
        )

    def test_rejects_anchor_and_alias_in_policy_sensitive_top_level_fields(self):
        workflows = {
            "anchored-on.yml": """
                on: &events [pull_request_target]
                jobs:
                  test:
                    runs-on: ubuntu-latest
                    steps:
                      - run: true
            """,
            "aliased-on.yml": """
                x-events: &events [pull_request_target]
                on: *events
                jobs:
                  test:
                    runs-on: ubuntu-latest
                    steps:
                      - run: true
            """,
            "anchored-permissions.yml": """
                on: push
                permissions: &perms write-all
                jobs:
                  test:
                    runs-on: ubuntu-latest
                    steps:
                      - run: true
            """,
            "aliased-permissions.yml": """
                x-perms: &perms write-all
                on: push
                permissions: *perms
                jobs:
                  test:
                    runs-on: ubuntu-latest
                    steps:
                      - run: true
            """,
        }
        for filename, body in workflows.items():
            with self.subTest(filename=filename):
                self.assert_policy_violation(body, "unsupported-yaml-anchor-alias", filename=filename)

    def test_rejects_per_scope_write_permissions_at_top_and_job_level(self):
        result = self.run_validator(
            {
                "policy.yml": """
                on: push
                permissions:
                  contents: write
                jobs:
                  test:
                    runs-on: ubuntu-latest
                    permissions:
                      checks: write
                    steps:
                      - run: true
                """
            }
        )

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        output = result.stdout + result.stderr
        self.assertIn("permissions-write-scope", output)
        self.assertIn("contents", output)
        self.assertIn("job test", output)
        self.assertIn("checks", output)
        self.assertNotIn("Traceback", output)

    def test_rejects_pull_request_target_scalar_list_mapping_and_quoted_forms(self):
        workflows = {
            "scalar.yml": """
                on: pull_request_target
                jobs:
                  test:
                    runs-on: ubuntu-latest
                    steps:
                      - run: true
            """,
            "list.yml": """
                on: [push, pull_request_target]
                jobs:
                  test:
                    runs-on: ubuntu-latest
                    steps:
                      - run: true
            """,
            "mapping.yml": """
                on: { pull_request_target: {}, push: {} }
                jobs:
                  test:
                    runs-on: ubuntu-latest
                    steps:
                      - run: true
            """,
            "quoted.yaml": """
                "on":
                  "pull_request_target":
                jobs:
                  test:
                    runs-on: ubuntu-latest
                    steps:
                      - run: true
            """,
        }
        for filename, body in workflows.items():
            with self.subTest(filename=filename):
                self.assert_policy_violation(body, "pull-request-target", filename=filename)

    def test_rejects_pull_request_jobs_using_self_hosted_runners(self):
        for runs_on in ("self-hosted", "[self-hosted, linux]"):
            with self.subTest(runs_on=runs_on):
                self.assert_policy_violation(
                    f"""
                    on: [pull_request]
                    jobs:
                      integration:
                        runs-on: {runs_on}
                        steps:
                          - run: true
                    """,
                    "pr-self-hosted-runner",
                    "integration",
                )

    def test_rejects_anchor_and_alias_in_job_level_policy_sensitive_fields(self):
        workflows = {
            "anchored-runs-on.yml": """
                on: pull_request
                jobs:
                  integration:
                    runs-on: &runner [self-hosted, linux]
                    steps:
                      - run: true
            """,
            "aliased-runs-on.yml": """
                x-runner: &runner [self-hosted, linux]
                on: pull_request
                jobs:
                  integration:
                    runs-on: *runner
                    steps:
                      - run: true
            """,
            "anchored-job-permissions.yml": """
                on: push
                jobs:
                  test:
                    runs-on: ubuntu-latest
                    permissions: &perms write-all
                    steps:
                      - run: true
            """,
            "aliased-job-permissions.yml": """
                x-perms: &perms write-all
                on: push
                jobs:
                  test:
                    runs-on: ubuntu-latest
                    permissions: *perms
                    steps:
                      - run: true
            """,
        }
        for filename, body in workflows.items():
            with self.subTest(filename=filename):
                job = "integration" if "runs-on" in filename else "test"
                self.assert_policy_violation(body, "unsupported-yaml-anchor-alias", job, filename)

    def test_rejects_pull_request_jobs_using_matrix_runners_as_ambiguous(self):
        self.assert_policy_violation(
            """
            on: pull_request
            jobs:
              integration:
                strategy:
                  matrix:
                    runner: [ubuntu-latest, self-hosted]
                runs-on: ${{ matrix.runner }}
                steps:
                  - run: true
            """,
            "pr-runner-ambiguous",
            "integration",
        )

    def test_rejects_runs_on_mapping_forms_as_unsupported(self):
        workflows = {
            "inline-key.yml": """
                on: pull_request
                jobs:
                  integration:
                    runs-on: {labels/self-hosted}
                    steps:
                      - run: true
            """,
            "inline-value.yml": """
                on: pull_request
                jobs:
                  integration:
                    runs-on: {labels: [self-hosted, linux]}
                    steps:
                      - run: true
            """,
            "block.yml": """
                on: pull_request
                jobs:
                  integration:
                    runs-on:
                      labels: [self-hosted, linux]
                    steps:
                      - run: true
            """,
        }
        for filename, body in workflows.items():
            with self.subTest(filename=filename):
                self.assert_policy_violation(body, "unsupported-runs-on-mapping", "integration", filename)

    def test_rejects_deploy_jobs_without_exact_job_level_main_branch_guard(self):
        for condition in (
            "startsWith(github.ref, 'refs/heads/main')",
            "github.head_ref == 'main'",
            "",
        ):
            with self.subTest(condition=condition):
                if_line = f"if: {condition}" if condition else "steps:"
                steps_line = "steps:" if condition else "  - run: ./deploy.sh"
                self.assert_policy_violation(
                    f"""
                    on: push
                    jobs:
                      deploy:
                        runs-on: ubuntu-latest
                        {if_line}
                        {steps_line}
                          - run: ./deploy.sh
                    """,
                    "deploy-main-guard",
                    "deploy",
                )

    def test_rejects_deploy_jobs_when_main_guard_exists_only_on_a_step(self):
        self.assert_policy_violation(
            """
            on: push
            jobs:
              deploy:
                runs-on: ubuntu-latest
                steps:
                  - if: github.ref == 'refs/heads/main'
                    run: ./deploy.sh
            """,
            "deploy-main-guard",
            "deploy",
        )

    def test_accepts_deploy_jobs_with_exact_job_level_main_branch_guard(self):
        result = self.run_validator(
            {
                "deploy.yml": """
                on: push
                jobs:
                  deploy-production:
                    if: github.ref == 'refs/heads/main'
                    runs-on: [ubuntu-latest, production]
                    steps:
                      - run: ./deploy.sh
                """
            }
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_rejects_non_deploy_jobs_using_production_runner_labels(self):
        for production_label in ("production", "prod", "prd", "prod-runner"):
            with self.subTest(production_label=production_label):
                self.assert_policy_violation(
                    f"""
                    on: push
                    jobs:
                      smoke:
                        runs-on:
                          - ubuntu-latest
                          - {production_label}
                        steps:
                          - run: true
                    """,
                    "production-label-non-deploy",
                    "smoke",
                )

    def test_scans_yml_and_yaml_workflow_files(self):
        result = self.run_validator(
            {
                "first.yml": """
                on: push
                jobs:
                  first:
                    runs-on: ubuntu-latest
                    steps:
                      - uses: actions/checkout@v4
                """,
                "second.yaml": """
                on: push
                jobs:
                  second:
                    runs-on: ubuntu-latest
                    steps:
                      - uses: actions/setup-python@v5
                """,
            }
        )

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        output = result.stdout + result.stderr
        self.assertIn("first.yml", output)
        self.assertIn("second.yaml", output)
        self.assertNotIn("Traceback", output)

    def test_missing_and_empty_workflow_directories_are_explicit_success(self):
        missing = self.run_validator(None)
        self.assertEqual(missing.returncode, 0, missing.stdout + missing.stderr)
        self.assertIn("OK: workflow policy validated 0 workflow file(s); directory missing", missing.stdout)

        empty = self.run_validator({})
        self.assertEqual(empty.returncode, 0, empty.stdout + empty.stderr)
        self.assertIn("OK: workflow policy validated 0 workflow file(s); directory empty", empty.stdout)

    def test_malformed_top_level_list_and_no_jobs_workflows_fail_closed(self):
        workflows = {
            "malformed.yml": "not a valid workflow line",
            "list.yml": "- on: push\n- jobs: []\n",
            "no-jobs.yaml": "on: push\nname: docs only\n",
        }
        for filename, body in workflows.items():
            with self.subTest(filename=filename):
                self.assert_policy_violation(body, "unsupported-yaml", filename=filename)


if __name__ == "__main__":
    unittest.main()
