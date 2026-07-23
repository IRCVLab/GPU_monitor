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

    def test_rejects_yaml_tags_in_policy_sensitive_workflow_syntax(self):
        workflows = {
            "tagged-on.yml": """
                on: !!seq [pull_request_target]
                jobs:
                  test:
                    runs-on: ubuntu-latest
                    steps:
                      - run: true
            """,
            "tagged-runs-on-seq.yml": """
                on: pull_request
                jobs:
                  integration:
                    runs-on: !!seq [self-hosted, linux]
                    steps:
                      - run: true
            """,
            "tagged-runs-on-map.yml": """
                on: pull_request
                jobs:
                  integration:
                    runs-on: !!map {labels: [self-hosted, linux]}
                    steps:
                      - run: true
            """,
        }
        for filename, body in workflows.items():
            with self.subTest(filename=filename):
                job = "integration" if "runs-on" in filename else None
                self.assert_policy_violation(body, "unsupported-yaml-tag", job, filename)

    def test_rejects_broader_yaml_anchor_names_and_adjacent_flow_delimiters(self):
        workflows = {
            "anchored-on-dot.yml": """
                on: &ev.ents [pull_request_target]
                jobs:
                  test:
                    runs-on: ubuntu-latest
                    steps:
                      - run: true
            """,
            "anchored-on-flow.yml": """
                on: &ev[pull_request_target]
                jobs:
                  test:
                    runs-on: ubuntu-latest
                    steps:
                      - run: true
            """,
            "aliased-permissions-dot.yml": """
                x-perms: &perms.prod write-all
                on: push
                permissions: *perms.prod
                jobs:
                  test:
                    runs-on: ubuntu-latest
                    steps:
                      - run: true
            """,
            "aliased-permissions-flow.yml": """
                x-perms: &perms[write-all]
                on: push
                permissions: *perms
                jobs:
                  test:
                    runs-on: ubuntu-latest
                    steps:
                      - run: true
            """,
            "aliased-runs-on-dot.yml": """
                x-runner: &runner.prod [self-hosted, linux]
                on: pull_request
                jobs:
                  integration:
                    runs-on: *runner.prod
                    steps:
                      - run: true
            """,
            "aliased-runs-on-flow.yml": """
                x-runner: &runner[self-hosted, linux]
                on: pull_request
                jobs:
                  integration:
                    runs-on: *runner
                    steps:
                      - run: true
            """,
        }
        for filename, body in workflows.items():
            with self.subTest(filename=filename):
                job = "integration" if "runs-on" in filename else None
                self.assert_policy_violation(body, "unsupported-yaml-anchor-alias", job, filename)


    def test_rejects_fail_closed_yaml_tokens_across_semantic_structure(self):
        workflows = {
            "cross-field-alias-event-key.yml": """
                x-event: &prt pull_request_target
                on:
                  *prt:
                jobs:
                  test:
                    runs-on: ubuntu-latest
                    steps:
                      - run: true
            """,
            "block-permission-anchored-value.yml": """
                on: push
                permissions:
                  contents: &perm write
                jobs:
                  test:
                    runs-on: ubuntu-latest
                    steps:
                      - run: true
            """,
            "block-permission-tagged-value.yml": """
                on: push
                permissions:
                  contents: !!str write
                jobs:
                  test:
                    runs-on: ubuntu-latest
                    steps:
                      - run: true
            """,
            "alias-key.yml": """
                on: push
                *aliased-key: value
                jobs:
                  test:
                    runs-on: ubuntu-latest
                    steps:
                      - run: true
            """,
            "tagged-key.yml": """
                on: push
                !tagged-key: value
                jobs:
                  test:
                    runs-on: ubuntu-latest
                    steps:
                      - run: true
            """,
        }
        expected_rules = {
            "block-permission-tagged-value.yml": "unsupported-yaml-tag",
            "tagged-key.yml": "unsupported-yaml-tag",
        }
        for filename, body in workflows.items():
            with self.subTest(filename=filename):
                self.assert_policy_violation(
                    body,
                    expected_rules.get(filename, "unsupported-yaml-anchor-alias"),
                    filename=filename,
                )

    def test_accepts_github_expressions_shell_tokens_and_quoted_yaml_tokens(self):
        result = self.run_validator(
            {
                "expressions-and-shell.yml": f"""
                on: push
                permissions: read-all
                jobs:
                  test:
                    if: ${{{{ !cancelled() && github.ref != 'refs/heads/release/*' }}}}
                    runs-on: ubuntu-latest
                    env:
                      QUOTED_ANCHOR: "&anchor *alias !tag !!str"
                      SINGLE_QUOTED: '*literal !literal &literal'
                    steps:
                      - name: "quoted *alias !tag &anchor"
                        run: echo !important && printf '%s\\n' src/* "&quoted" '*quoted' !!not-yaml
                      - run: |
                          echo & shell background is allowed inside run blocks
                          printf '%s\\n' !negated src/* *glob
                      - uses: actions/checkout@{PINNED_SHA}
                """
            }
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_rejects_inline_runs_on_mapping_with_trailing_comment(self):
        self.assert_policy_violation(
            """
            on: pull_request
            jobs:
              integration:
                runs-on: {labels: self-hosted} # comment
                steps:
                  - run: true
            """,
            "unsupported-runs-on-mapping",
            "integration",
        )

    def test_yaml_token_detection_ignores_quoted_strings_and_run_globs(self):
        result = self.run_validator(
            {
                "quoted-and-run.yml": f"""
                on: push
                permissions: read-all
                jobs:
                  test:
                    runs-on: ubuntu-latest
                    steps:
                      - run: echo 'literal &ev.ents *runner !!seq [not-yaml]' && ls src/* || true
                      - run: echo "{{labels: self-hosted}} # not a yaml comment"
                      - uses: actions/checkout@{PINNED_SHA}
                """
            }
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

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


    def test_rejects_environment_bearing_release_named_job_without_deploy_token(self):
        self.assert_policy_violation(
            """
            on: push
            jobs:
              release-gpu:
                runs-on: ubuntu-latest
                environment: gpu-live
                steps:
                  - run: ./release.sh
            """,
            "deploy-main-guard",
            "release-gpu",
        )

    def test_rejects_live_deployment_workflows_triggered_by_pull_request(self):
        self.assert_policy_violation(
            """
            on: pull_request
            jobs:
              deploy:
                if: github.ref == 'refs/heads/main'
                runs-on: ubuntu-latest
                environment: gpu-live
                steps:
                  - run: ./deploy.sh
            """,
            "deploy-pull-request-event",
            "deploy",
        )

    def test_rejects_workflow_run_deployments_without_completed_ci_push_main_same_repo_success_guard(self):
        workflows = {
            "missing-types.yml": """
                on:
                  workflow_run:
                    workflows: [ci]
                jobs:
                  deploy:
                    if: github.event.workflow_run.event == 'push' && github.event.workflow_run.head_branch == 'main' && github.event.workflow_run.conclusion == 'success' && github.event.workflow_run.head_repository.full_name == github.repository
                    runs-on: ubuntu-latest
                    environment: gpu-live
                    steps:
                      - name: Authorize deployment
                        run: python3.12 scripts/authorize_gpu_release.py --repo owner/repo --sha 0123456789abcdef0123456789abcdef01234567
                      - run: ./deploy.sh
            """,
            "missing-ci-workflow.yml": """
                on:
                  workflow_run:
                    workflows: [lint]
                    types: [completed]
                jobs:
                  deploy:
                    if: github.event.workflow_run.event == 'push' && github.event.workflow_run.head_branch == 'main' && github.event.workflow_run.conclusion == 'success' && github.event.workflow_run.head_repository.full_name == github.repository
                    runs-on: ubuntu-latest
                    environment: gpu-live
                    steps:
                      - name: Authorize deployment
                        run: python3.12 scripts/authorize_gpu_release.py --repo owner/repo --sha 0123456789abcdef0123456789abcdef01234567
                      - run: ./deploy.sh
            """,
            "missing-event-guard.yml": """
                on:
                  workflow_run:
                    workflows: [ci]
                    types: [completed]
                jobs:
                  deploy:
                    if: github.event.workflow_run.head_branch == 'main' && github.event.workflow_run.conclusion == 'success' && github.event.workflow_run.head_repository.full_name == github.repository
                    runs-on: ubuntu-latest
                    environment: gpu-live
                    steps:
                      - name: Authorize deployment
                        run: python3.12 scripts/authorize_gpu_release.py --repo owner/repo --sha 0123456789abcdef0123456789abcdef01234567
                      - run: ./deploy.sh
            """,
            "missing-branch-guard.yml": """
                on:
                  workflow_run:
                    workflows: [ci]
                    types: [completed]
                jobs:
                  deploy:
                    if: github.event.workflow_run.event == 'push' && github.event.workflow_run.conclusion == 'success' && github.event.workflow_run.head_repository.full_name == github.repository
                    runs-on: ubuntu-latest
                    environment: gpu-live
                    steps:
                      - name: Authorize deployment
                        run: python3.12 scripts/authorize_gpu_release.py --repo owner/repo --sha 0123456789abcdef0123456789abcdef01234567
                      - run: ./deploy.sh
            """,
            "missing-success-guard.yml": """
                on:
                  workflow_run:
                    workflows: [ci]
                    types: [completed]
                jobs:
                  deploy:
                    if: github.event.workflow_run.event == 'push' && github.event.workflow_run.head_branch == 'main' && github.event.workflow_run.head_repository.full_name == github.repository
                    runs-on: ubuntu-latest
                    environment: gpu-live
                    steps:
                      - name: Authorize deployment
                        run: python3.12 scripts/authorize_gpu_release.py --repo owner/repo --sha 0123456789abcdef0123456789abcdef01234567
                      - run: ./deploy.sh
            """,
            "missing-same-repo-guard.yml": """
                on:
                  workflow_run:
                    workflows: [ci]
                    types: [completed]
                jobs:
                  deploy:
                    if: github.event.workflow_run.event == 'push' && github.event.workflow_run.head_branch == 'main' && github.event.workflow_run.conclusion == 'success'
                    runs-on: ubuntu-latest
                    environment: gpu-live
                    steps:
                      - name: Authorize deployment
                        run: python3.12 scripts/authorize_gpu_release.py --repo owner/repo --sha 0123456789abcdef0123456789abcdef01234567
                      - run: ./deploy.sh
            """,
            "missing-authorization-step.yml": """
                on:
                  workflow_run:
                    workflows: [ci]
                    types: [completed]
                jobs:
                  deploy:
                    if: github.event.workflow_run.event == 'push' && github.event.workflow_run.head_branch == 'main' && github.event.workflow_run.conclusion == 'success' && github.event.workflow_run.head_repository.full_name == github.repository
                    runs-on: ubuntu-latest
                    environment: gpu-live
                    steps:
                      - run: ./deploy.sh
            """,
        }
        for filename, body in workflows.items():
            with self.subTest(filename=filename):
                self.assert_policy_violation(body, "workflow-run-deploy-guard", "deploy", filename)

    def test_rejects_deployment_jobs_using_self_hosted_runners(self):
        self.assert_policy_violation(
            """
            on: push
            jobs:
              activate:
                if: github.ref == 'refs/heads/main'
                runs-on: [self-hosted, gpu-live]
                environment: gpu-live
                steps:
                  - run: ./activate.sh
            """,
            "deploy-self-hosted-runner",
            "activate",
        )

    def test_rejects_pull_request_jobs_that_reference_secrets(self):
        self.assert_policy_violation(
            """
            on: pull_request
            jobs:
              unit:
                runs-on: ubuntu-latest
                env:
                  API_TOKEN: ${{ secrets.DEPLOY_TOKEN }}
                steps:
                  - run: echo '${{ secrets.DEPLOY_TOKEN }}'
            """,
            "pr-secrets",
            "unit",
        )

    def test_accepts_sha_pinned_authorized_workflow_run_live_deployment(self):
        result = self.run_validator(
            {
                "gpu-live.yml": f"""
                on:
                  workflow_run:
                    workflows: [ci]
                    types: [completed]
                permissions: read-all
                jobs:
                  release-gpu:
                    if: github.event.workflow_run.event == 'push' && github.event.workflow_run.head_branch == 'main' && github.event.workflow_run.conclusion == 'success' && github.event.workflow_run.head_repository.full_name == github.repository
                    runs-on: ubuntu-latest
                    environment: gpu-live
                    steps:
                      - uses: actions/checkout@{PINNED_SHA}
                      - name: Authorize deployment
                        run: python3.12 scripts/authorize_gpu_release.py --repo owner/repo --sha 0123456789abcdef0123456789abcdef01234567
                      - run: ./deploy.sh
                """
            }
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


    def test_rejects_workflow_run_provenance_guard_or_quoted_dead_branch_and_extra_clause_bypasses(self):
        workflows = {
            "or-bypass.yml": """
                on:
                  workflow_run:
                    workflows: [ci]
                    types: [completed]
                jobs:
                  release-gpu:
                    if: github.event.workflow_run.event == 'push' && github.event.workflow_run.head_branch == 'main' && github.event.workflow_run.conclusion == 'success' && github.event.workflow_run.head_repository.full_name == github.repository || always()
                    runs-on: ubuntu-latest
                    environment: gpu-live
                    steps:
                      - name: Authorize deployment
                        run: python3.12 scripts/authorize_gpu_release.py --repo owner/repo --sha 0123456789abcdef0123456789abcdef01234567
            """,
            "quoted-injection.yml": """
                on:
                  workflow_run:
                    workflows: [ci]
                    types: [completed]
                jobs:
                  release-gpu:
                    if: contains('github.event.workflow_run.event == ''push'' && github.event.workflow_run.head_branch == ''main'' && github.event.workflow_run.conclusion == ''success'' && github.event.workflow_run.head_repository.full_name == github.repository', 'push')
                    runs-on: ubuntu-latest
                    environment: gpu-live
                    steps:
                      - name: Authorize deployment
                        run: python3.12 scripts/authorize_gpu_release.py --repo owner/repo --sha 0123456789abcdef0123456789abcdef01234567
            """,
            "dead-branch.yml": """
                on:
                  workflow_run:
                    workflows: [ci]
                    types: [completed]
                jobs:
                  release-gpu:
                    if: false && github.event.workflow_run.event == 'push' && github.event.workflow_run.head_branch == 'main' && github.event.workflow_run.conclusion == 'success' && github.event.workflow_run.head_repository.full_name == github.repository
                    runs-on: ubuntu-latest
                    environment: gpu-live
                    steps:
                      - name: Authorize deployment
                        run: python3.12 scripts/authorize_gpu_release.py --repo owner/repo --sha 0123456789abcdef0123456789abcdef01234567
            """,
            "negated-clause.yml": """
                on:
                  workflow_run:
                    workflows: [ci]
                    types: [completed]
                jobs:
                  release-gpu:
                    if: ${{ github.event.workflow_run.event == 'push' && !github.event.workflow_run.head_branch == 'main' && github.event.workflow_run.conclusion == 'success' && github.event.workflow_run.head_repository.full_name == github.repository }}
                    runs-on: ubuntu-latest
                    environment: gpu-live
                    steps:
                      - name: Authorize deployment
                        run: python3.12 scripts/authorize_gpu_release.py --repo owner/repo --sha 0123456789abcdef0123456789abcdef01234567
            """,
            "extra-true-clause.yml": """
                on:
                  workflow_run:
                    workflows: [ci]
                    types: [completed]
                jobs:
                  release-gpu:
                    if: github.event.workflow_run.event == 'push' && github.event.workflow_run.head_branch == 'main' && github.event.workflow_run.conclusion == 'success' && github.event.workflow_run.head_repository.full_name == github.repository && true
                    runs-on: ubuntu-latest
                    environment: gpu-live
                    steps:
                      - name: Authorize deployment
                        run: python3.12 scripts/authorize_gpu_release.py --repo owner/repo --sha 0123456789abcdef0123456789abcdef01234567
            """,
        }
        for filename, body in workflows.items():
            with self.subTest(filename=filename):
                self.assert_policy_violation(body, "workflow-run-deploy-guard", "release-gpu", filename)

    def test_rejects_four_space_job_properties_that_would_hide_environment(self):
        self.assert_policy_violation(
            """
            on: push
            jobs:
              smoke-gpu:
                  runs-on: ubuntu-latest
                  environment: gpu-live
                  steps:
                    - run: ./smoke.sh
            """,
            "deploy-main-guard",
            "smoke-gpu",
        )

    def test_rejects_workflow_run_with_four_space_trigger_children_missing_types(self):
        self.assert_policy_violation(
            """
            on:
              workflow_run:
                  workflows: [ci]
            jobs:
              release-gpu:
                if: github.event.workflow_run.event == 'push' && github.event.workflow_run.head_branch == 'main' && github.event.workflow_run.conclusion == 'success' && github.event.workflow_run.head_repository.full_name == github.repository
                runs-on: ubuntu-latest
                environment: gpu-live
                steps:
                  - run: python3.12 scripts/authorize_gpu_release.py --repo owner/repo --sha 0123456789abcdef0123456789abcdef01234567
            """,
            "workflow-run-deploy-guard",
            "release-gpu",
        )

    def test_rejects_cosmetic_or_substring_authorization_steps(self):
        workflows = {
            "job-name-only.yml": """
                on:
                  workflow_run:
                    workflows: [ci]
                    types: [completed]
                jobs:
                  release-gpu:
                    name: Authorize deployment
                    if: github.event.workflow_run.event == 'push' && github.event.workflow_run.head_branch == 'main' && github.event.workflow_run.conclusion == 'success' && github.event.workflow_run.head_repository.full_name == github.repository
                    runs-on: ubuntu-latest
                    environment: gpu-live
                    steps:
                      - run: ./deploy.sh
            """,
            "unauthorized-name.yml": """
                on:
                  workflow_run:
                    workflows: [ci]
                    types: [completed]
                jobs:
                  release-gpu:
                    if: github.event.workflow_run.event == 'push' && github.event.workflow_run.head_branch == 'main' && github.event.workflow_run.conclusion == 'success' && github.event.workflow_run.head_repository.full_name == github.repository
                    runs-on: ubuntu-latest
                    environment: gpu-live
                    steps:
                      - name: unauthorized deploy
                        run: ./deploy.sh
            """,
            "echo-only.yml": """
                on:
                  workflow_run:
                    workflows: [ci]
                    types: [completed]
                jobs:
                  release-gpu:
                    if: github.event.workflow_run.event == 'push' && github.event.workflow_run.head_branch == 'main' && github.event.workflow_run.conclusion == 'success' && github.event.workflow_run.head_repository.full_name == github.repository
                    runs-on: ubuntu-latest
                    environment: gpu-live
                    steps:
                      - name: Authorize deployment
                        run: echo python3.12 scripts/authorize_gpu_release.py
            """,
            "wrong-script.yml": """
                on:
                  workflow_run:
                    workflows: [ci]
                    types: [completed]
                jobs:
                  release-gpu:
                    if: github.event.workflow_run.event == 'push' && github.event.workflow_run.head_branch == 'main' && github.event.workflow_run.conclusion == 'success' && github.event.workflow_run.head_repository.full_name == github.repository
                    runs-on: ubuntu-latest
                    environment: gpu-live
                    steps:
                      - name: Authorize deployment
                        run: python3.12 scripts/check_deploy_prerequisites.py --repo owner/repo
            """,
        }
        for filename, body in workflows.items():
            with self.subTest(filename=filename):
                self.assert_policy_violation(body, "workflow-run-deploy-guard", "release-gpu", filename)

    def test_rejects_pull_request_workflow_env_and_expression_secret_contexts(self):
        workflows = {
            "workflow-env.yml": """
                on: pull_request
                env:
                  TOKEN: ${{ secrets.DEPLOY_TOKEN }}
                jobs:
                  unit:
                    runs-on: ubuntu-latest
                    steps:
                      - run: true
            """,
            "whole-context.yml": """
                on: pull_request
                jobs:
                  unit:
                    runs-on: ubuntu-latest
                    steps:
                      - run: echo '${{ secrets }}'
            """,
            "to-json.yml": """
                on: pull_request
                jobs:
                  unit:
                    runs-on: ubuntu-latest
                    steps:
                      - run: echo '${{ toJson(secrets) }}'
            """,
            "spaced-bracket.yml": """
                on: pull_request
                jobs:
                  unit:
                    runs-on: ubuntu-latest
                    steps:
                      - run: echo "${{ secrets ['DEPLOY_TOKEN'] }}"
            """,
            "secrets-mapping.yml": """
                on: pull_request
                jobs:
                  unit:
                    runs-on: ubuntu-latest
                    secrets: inherit
            """,
        }
        for filename, body in workflows.items():
            with self.subTest(filename=filename):
                self.assert_policy_violation(body, "pr-secrets", filename=filename)

    def test_allows_pull_request_literal_secret_word_in_url_without_expression(self):
        result = self.run_validator(
            {
                "docs.yml": """
                on: pull_request
                jobs:
                  unit:
                    runs-on: ubuntu-latest
                    steps:
                      - run: curl https://example.invalid/docs/secrets.html
                """
            }
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_rejects_dynamic_matrix_and_non_github_hosted_deployment_runners(self):
        workflows = {
            "dynamic.yml": """
                on: push
                jobs:
                  deploy:
                    if: github.ref == 'refs/heads/main'
                    runs-on: ${{ matrix.runner }}
                    steps:
                      - run: ./deploy.sh
            """,
            "expression.yml": """
                on: push
                jobs:
                  deploy:
                    if: github.ref == 'refs/heads/main'
                    runs-on: ${{ github.ref_name }}
                    steps:
                      - run: ./deploy.sh
            """,
            "custom-label.yml": """
                on: push
                jobs:
                  deploy:
                    if: github.ref == 'refs/heads/main'
                    runs-on: [ubuntu-latest, gpu-live]
                    steps:
                      - run: ./deploy.sh
            """,
        }
        for filename, body in workflows.items():
            with self.subTest(filename=filename):
                self.assert_policy_violation(body, "deploy-runner", "deploy", filename)

    def test_allows_release_notes_and_activate_venv_non_deploy_names(self):
        result = self.run_validator(
            {
                "docs.yml": """
                on: push
                jobs:
                  release-notes:
                    runs-on: ubuntu-latest
                    steps:
                      - run: ./scripts/generate-release-notes.sh
                  activate-venv:
                    runs-on: ubuntu-latest
                    steps:
                      - run: python -m venv .venv && . .venv/bin/activate
                """
            }
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

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
                    runs-on: ubuntu-latest
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
