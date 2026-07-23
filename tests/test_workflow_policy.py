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


    def test_accepts_gated_gpu_dev_workflow_dispatch_deployment(self):
        workflow = Path(".github/workflows/deploy-gpu-dev.yml").read_text(encoding="utf-8")
        result = self.run_validator({"deploy-gpu-dev.yml": workflow})

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


    def test_rejects_gpu_dev_workflow_dispatch_bypasses(self):
        workflows = {
            "missing-pr-input.yml": """
                on:
                  workflow_dispatch:
                jobs:
                  deploy:
                    runs-on: ubuntu-24.04
                    environment: gpu-dev
                    steps:
                      - run: gh api repos/${{ github.repository }}/pulls/1
                      - run: ci/required
                      - run: ssh "$target" "upload dev $sha $digest" < "$artifact"
            """,
            "missing-ci-required.yml": """
                on:
                  workflow_dispatch:
                    inputs:
                      pr_number:
                        required: true
                jobs:
                  deploy:
                    runs-on: ubuntu-24.04
                    environment: gpu-dev
                    steps:
                      - run: gh api repos/${{ github.repository }}/pulls/$pr_number
                      - run: ssh "$target" "upload dev $sha $digest" < "$artifact"
            """,
            "wrong-environment.yml": """
                on:
                  workflow_dispatch:
                    inputs:
                      pr_number:
                        required: true
                concurrency:
                  group: gpu-live
                  cancel-in-progress: false
                jobs:
                  deploy:
                    runs-on: ubuntu-24.04
                    environment: gpu-live
                    steps:
                      - run: gh api repos/${{ github.repository }}/pulls/$pr_number && ci/required
                      - run: ssh "$target" "upload dev $sha $digest" < "$artifact"
            """,
            "mutable-runner.yml": """
                on:
                  workflow_dispatch:
                    inputs:
                      pr_number:
                        required: true
                concurrency:
                  group: gpu-dev
                  cancel-in-progress: true
                jobs:
                  deploy:
                    runs-on: ubuntu-latest
                    environment: gpu-dev
                    steps:
                      - run: gh api repos/${{ github.repository }}/pulls/$pr_number && ci/required
                      - run: ssh "$target" "upload dev $sha $digest" < "$artifact"
            """,
        }
        for filename, body in workflows.items():
            with self.subTest(filename=filename):
                self.assert_policy_violation(body, "workflow-dispatch-dev-deploy-guard", "deploy", filename)

    def test_rejects_live_deployments_without_separate_non_secret_authorization_job(self):
        workflows = {
            "same-job-auth.yml": self.guarded_workflow_run("run: python3.12 scripts/authorize_gpu_release.py --repo owner/repo --sha 0123456789abcdef0123456789abcdef01234567"),
            "auth-job-has-secret.yml": f"""
                on:
                  workflow_run:
                    workflows: [ci]
                    types: [completed]
                jobs:
                  authorize:
                    if: github.event.workflow_run.event == 'push' && github.event.workflow_run.head_branch == 'main' && github.event.workflow_run.conclusion == 'success' && github.event.workflow_run.head_repository.full_name == github.repository
                    runs-on: ubuntu-24.04
                    env:
                      TOKEN: ${{{{ secrets.GPU_DEPLOY_HOST }}}}
                    steps:
                      - run: python3.12 scripts/authorize_gpu_release.py --repo owner/repo --sha 0123456789abcdef0123456789abcdef01234567
                  deploy:
                    needs: authorize
                    if: github.event.workflow_run.event == 'push' && github.event.workflow_run.head_branch == 'main' && github.event.workflow_run.conclusion == 'success' && github.event.workflow_run.head_repository.full_name == github.repository
                    runs-on: ubuntu-24.04
                    environment: gpu-live
                    steps:
                      - run: ./deploy.sh
            """,
            "deploy-missing-needs.yml": f"""
                on:
                  workflow_run:
                    workflows: [ci]
                    types: [completed]
                jobs:
                  authorize:
                    if: github.event.workflow_run.event == 'push' && github.event.workflow_run.head_branch == 'main' && github.event.workflow_run.conclusion == 'success' && github.event.workflow_run.head_repository.full_name == github.repository
                    runs-on: ubuntu-24.04
                    steps:
                      - run: python3.12 scripts/authorize_gpu_release.py --repo owner/repo --sha 0123456789abcdef0123456789abcdef01234567
                  deploy:
                    if: github.event.workflow_run.event == 'push' && github.event.workflow_run.head_branch == 'main' && github.event.workflow_run.conclusion == 'success' && github.event.workflow_run.head_repository.full_name == github.repository
                    runs-on: ubuntu-24.04
                    environment: gpu-live
                    steps:
                      - run: ./deploy.sh
            """,
        }
        for filename, body in workflows.items():
            with self.subTest(filename=filename):
                self.assert_policy_violation(body, "workflow-run-deploy-guard", filename=filename)

    def test_rejects_gpu_dev_without_base_repo_validation_or_paginated_latest_required_check(self):
        workflows = {
            "missing-base-repo.yml": """
                on:
                  workflow_dispatch:
                    inputs:
                      pr_number:
                        required: true
                concurrency:
                  group: gpu-dev
                  cancel-in-progress: true
                jobs:
                  deploy:
                    runs-on: ubuntu-24.04
                    environment: gpu-dev
                    steps:
                      - name: Resolve PR
                        run: |
                          pr_number="${{ github.event.inputs.pr_number }}"
                          [[ "$pr_number" =~ ^[1-9][0-9]*$ ]]
                          pr_json=$(gh api "repos/${{ github.repository }}/pulls/$pr_number")
                          state open
                          head.repo.full_name
                          head.sha
                          gh api --paginate "repos/${{ github.repository }}/commits/$sha/check-runs"
                          completed_at id latest status conclusion ci/required
                      - uses: actions/checkout@0123456789abcdef0123456789abcdef01234567
                        with:
                          ref: ${{ steps.resolve.outputs.sha }}
                      - run: |
                          [[ "$sha" =~ ^[0-9a-f]{40}$ ]]
                          [[ "$digest" =~ ^[0-9a-f]{64}$ ]]
                          [[ "$GPU_DEPLOY_HOST" =~ ^[A-Za-z0-9._-]+$ ]]
                          [[ "$GPU_DEPLOY_USER" =~ ^[A-Za-z0-9._-]+$ ]]
                          [[ "$GPU_DEPLOY_PORT" =~ ^[0-9]+$ ]]
                          target="$GPU_DEPLOY_USER@$GPU_DEPLOY_HOST"
                          ssh_opts=(-o BatchMode=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile="$known_hosts" -o IdentitiesOnly=yes -i "$key_file" -p "$GPU_DEPLOY_PORT")
                          ssh "${ssh_opts[@]}" "$target" "upload dev $sha $digest" < "$artifact"
                          ssh "${ssh_opts[@]}" "$target" "activate dev $sha $digest"
                          ssh "${ssh_opts[@]}" "$target" "status dev"
            """,
            "non-paginated-check-runs.yml": """
                on:
                  workflow_dispatch:
                    inputs:
                      pr_number:
                        required: true
                concurrency:
                  group: gpu-dev
                  cancel-in-progress: true
                jobs:
                  deploy:
                    runs-on: ubuntu-24.04
                    environment: gpu-dev
                    steps:
                      - name: Resolve PR
                        run: |
                          pr_number="${{ github.event.inputs.pr_number }}"
                          [[ "$pr_number" =~ ^[1-9][0-9]*$ ]]
                          pr_json=$(gh api "repos/${{ github.repository }}/pulls/$pr_number")
                          state open
                          base.repo.full_name
                          head.repo.full_name
                          head.sha
                          gh api "repos/${{ github.repository }}/commits/$sha/check-runs"
                          completed_at id latest status conclusion ci/required
                      - uses: actions/checkout@0123456789abcdef0123456789abcdef01234567
                        with:
                          ref: ${{ steps.resolve.outputs.sha }}
                      - run: |
                          [[ "$sha" =~ ^[0-9a-f]{40}$ ]]
                          [[ "$digest" =~ ^[0-9a-f]{64}$ ]]
                          [[ "$GPU_DEPLOY_HOST" =~ ^[A-Za-z0-9._-]+$ ]]
                          [[ "$GPU_DEPLOY_USER" =~ ^[A-Za-z0-9._-]+$ ]]
                          [[ "$GPU_DEPLOY_PORT" =~ ^[0-9]+$ ]]
                          target="$GPU_DEPLOY_USER@$GPU_DEPLOY_HOST"
                          ssh_opts=(-o BatchMode=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile="$known_hosts" -o IdentitiesOnly=yes -i "$key_file" -p "$GPU_DEPLOY_PORT")
                          ssh "${ssh_opts[@]}" "$target" "upload dev $sha $digest" < "$artifact"
                          ssh "${ssh_opts[@]}" "$target" "activate dev $sha $digest"
                          ssh "${ssh_opts[@]}" "$target" "status dev"
            """,
            "missing-slurp-check-runs.yml": """
                on:
                  workflow_dispatch:
                    inputs:
                      pr_number:
                        required: true
                concurrency:
                  group: gpu-dev
                  cancel-in-progress: true
                jobs:
                  deploy:
                    runs-on: ubuntu-24.04
                    environment: gpu-dev
                    steps:
                      - name: Resolve PR
                        run: |
                          pr_number="${{ github.event.inputs.pr_number }}"
                          [[ "$pr_number" =~ ^[1-9][0-9]*$ ]]
                          pr_json=$(gh api "repos/${{ github.repository }}/pulls/$pr_number")
                          state open
                          base.repo.full_name
                          head.repo.full_name
                          head.sha
                          gh api --paginate "repos/${{ github.repository }}/commits/$sha/check-runs"
                          completed_at id latest status conclusion ci/required
                      - uses: actions/checkout@0123456789abcdef0123456789abcdef01234567
                        with:
                          ref: ${{ steps.resolve.outputs.sha }}
                      - run: |
                          [[ "$sha" =~ ^[0-9a-f]{40}$ ]]
                          [[ "$digest" =~ ^[0-9a-f]{64}$ ]]
                          [[ "$GPU_DEPLOY_HOST" =~ ^[A-Za-z0-9._-]+$ ]]
                          [[ "$GPU_DEPLOY_USER" =~ ^[A-Za-z0-9._-]+$ ]]
                          [[ "$GPU_DEPLOY_PORT" =~ ^[0-9]+$ ]]
                          target="$GPU_DEPLOY_USER@$GPU_DEPLOY_HOST"
                          ssh_opts=(-o BatchMode=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile="$known_hosts" -o IdentitiesOnly=yes -i "$key_file" -p "$GPU_DEPLOY_PORT")
                          ssh "${ssh_opts[@]}" "$target" "upload dev $sha $digest" < "$artifact"
                          ssh "${ssh_opts[@]}" "$target" "activate dev $sha $digest"
                          ssh "${ssh_opts[@]}" "$target" "status dev"
            """,
            "older-success-mask.yml": """
                on:
                  workflow_dispatch:
                    inputs:
                      pr_number:
                        required: true
                concurrency:
                  group: gpu-dev
                  cancel-in-progress: true
                jobs:
                  deploy:
                    runs-on: ubuntu-24.04
                    environment: gpu-dev
                    steps:
                      - name: Resolve PR
                        run: |
                          pr_number="${{ github.event.inputs.pr_number }}"
                          [[ "$pr_number" =~ ^[1-9][0-9]*$ ]]
                          pr_json=$(gh api "repos/${{ github.repository }}/pulls/$pr_number")
                          state open
                          base.repo.full_name
                          head.repo.full_name
                          head.sha
                          gh api --paginate "repos/${{ github.repository }}/commits/$sha/check-runs"
                          ci/required status conclusion success
                      - uses: actions/checkout@0123456789abcdef0123456789abcdef01234567
                        with:
                          ref: ${{ steps.resolve.outputs.sha }}
                      - run: |
                          [[ "$sha" =~ ^[0-9a-f]{40}$ ]]
                          [[ "$digest" =~ ^[0-9a-f]{64}$ ]]
                          [[ "$GPU_DEPLOY_HOST" =~ ^[A-Za-z0-9._-]+$ ]]
                          [[ "$GPU_DEPLOY_USER" =~ ^[A-Za-z0-9._-]+$ ]]
                          [[ "$GPU_DEPLOY_PORT" =~ ^[0-9]+$ ]]
                          target="$GPU_DEPLOY_USER@$GPU_DEPLOY_HOST"
                          ssh_opts=(-o BatchMode=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile="$known_hosts" -o IdentitiesOnly=yes -i "$key_file" -p "$GPU_DEPLOY_PORT")
                          ssh "${ssh_opts[@]}" "$target" "upload dev $sha $digest" < "$artifact"
                          ssh "${ssh_opts[@]}" "$target" "activate dev $sha $digest"
                          ssh "${ssh_opts[@]}" "$target" "status dev"
            """,
        }
        for filename, body in workflows.items():
            with self.subTest(filename=filename):
                self.assert_policy_violation(body, "workflow-dispatch-dev-deploy-guard", "deploy", filename)

    def test_rejects_gpu_dev_synthetic_substring_bypasses(self):
        workflows = {
            "echoed-contract.yml": """
                on:
                  workflow_dispatch:
                    inputs:
                      pr_number:
                        required: true
                concurrency:
                  group: gpu-dev
                  cancel-in-progress: true
                jobs:
                  deploy:
                    runs-on: ubuntu-24.04
                    environment: gpu-dev
                    steps:
                      - name: fake contract
                        run: |
                          echo 'pulls/ github.repository base.repo.full_name head.repo.full_name head.sha state open ci/required check-runs --paginate --slurp completed_at id latest status conclusion steps.resolve.outputs.sha StrictHostKeyChecking=yes UserKnownHostsFile IdentitiesOnly=yes GPU_DEPLOY_HOST GPU_DEPLOY_USER upload dev $sha $digest activate dev $sha $digest status dev ^[1-9][0-9]*$ ^[0-9a-f]{40}$ ^[0-9a-f]{64}$ GPU_DEPLOY_PORT target'
                      - uses: actions/checkout@0123456789abcdef0123456789abcdef01234567
                        with:
                          ref: main
                      - run: ssh example.invalid uptime
            """,
            "commented-contract.yml": """
                on:
                  workflow_dispatch:
                    inputs:
                      pr_number:
                        required: true
                concurrency:
                  group: gpu-dev
                  cancel-in-progress: true
                jobs:
                  deploy:
                    runs-on: ubuntu-24.04
                    environment: gpu-dev
                    steps:
                      - name: commented contract
                        run: |
                          # pulls/ github.repository base.repo.full_name head.repo.full_name head.sha state open ci/required check-runs --paginate --slurp completed_at id latest status conclusion steps.resolve.outputs.sha StrictHostKeyChecking=yes UserKnownHostsFile IdentitiesOnly=yes GPU_DEPLOY_HOST GPU_DEPLOY_USER upload dev $sha $digest activate dev $sha $digest status dev ^[1-9][0-9]*$ ^[0-9a-f]{40}$ ^[0-9a-f]{64}$ GPU_DEPLOY_PORT target
                          true
                      - uses: actions/checkout@0123456789abcdef0123456789abcdef01234567
                        with:
                          ref: ${{ steps.resolve.outputs.sha }}
                      - run: true
            """,
            "wrong-checkout-ref.yml": """
                on:
                  workflow_dispatch:
                    inputs:
                      pr_number:
                        required: true
                concurrency:
                  group: gpu-dev
                  cancel-in-progress: true
                jobs:
                  deploy:
                    runs-on: ubuntu-24.04
                    environment: gpu-dev
                    steps:
                      - id: resolve
                        run: |
                          pr_number="${{ github.event.inputs.pr_number }}"
                          [[ "$pr_number" =~ ^[1-9][0-9]*$ ]]
                          pr_json=$(gh api "repos/${{ github.repository }}/pulls/$pr_number")
                          state=$(python3.12 -c 'print("open")')
                          [[ "$state" == open ]]
                          base.repo.full_name head.repo.full_name head.sha
                          gh api --paginate --slurp "repos/${{ github.repository }}/commits/$sha/check-runs"
                          completed_at id latest status conclusion ci/required
                      - uses: actions/checkout@0123456789abcdef0123456789abcdef01234567
                        with:
                          ref: main
                      - run: |
                          [[ "$sha" =~ ^[0-9a-f]{40}$ ]]
                          [[ "$digest" =~ ^[0-9a-f]{64}$ ]]
                          [[ "$GPU_DEPLOY_HOST" =~ ^[A-Za-z0-9._-]+$ ]]
                          [[ "$GPU_DEPLOY_USER" =~ ^[A-Za-z0-9._-]+$ ]]
                          [[ "$GPU_DEPLOY_PORT" =~ ^[0-9]+$ ]]
                          target="$GPU_DEPLOY_USER@$GPU_DEPLOY_HOST"
                          ssh_opts=(-o BatchMode=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile="$known_hosts" -o IdentitiesOnly=yes -i "$key_file" -p "$GPU_DEPLOY_PORT")
                          ssh "${ssh_opts[@]}" "$target" "upload dev $sha $digest" < "$artifact"
                          ssh "${ssh_opts[@]}" "$target" "activate dev $sha $digest"
                          ssh "${ssh_opts[@]}" "$target" "status dev"
            """,
            "arbitrary-ssh.yml": """
                on:
                  workflow_dispatch:
                    inputs:
                      pr_number:
                        required: true
                concurrency:
                  group: gpu-dev
                  cancel-in-progress: true
                jobs:
                  deploy:
                    runs-on: ubuntu-24.04
                    environment: gpu-dev
                    steps:
                      - id: resolve
                        run: |
                          pr_number="${{ github.event.inputs.pr_number }}"
                          [[ "$pr_number" =~ ^[1-9][0-9]*$ ]]
                          pr_json=$(gh api "repos/${{ github.repository }}/pulls/$pr_number")
                          state open base.repo.full_name head.repo.full_name head.sha
                          gh api --paginate --slurp "repos/${{ github.repository }}/commits/$sha/check-runs"
                          completed_at id latest status conclusion ci/required
                      - uses: actions/checkout@0123456789abcdef0123456789abcdef01234567
                        with:
                          ref: ${{ steps.resolve.outputs.sha }}
                      - run: |
                          [[ "$sha" =~ ^[0-9a-f]{40}$ ]]
                          [[ "$digest" =~ ^[0-9a-f]{64}$ ]]
                          [[ "$GPU_DEPLOY_HOST" =~ ^[A-Za-z0-9._-]+$ ]]
                          [[ "$GPU_DEPLOY_USER" =~ ^[A-Za-z0-9._-]+$ ]]
                          [[ "$GPU_DEPLOY_PORT" =~ ^[0-9]+$ ]]
                          target="$GPU_DEPLOY_USER@$GPU_DEPLOY_HOST"
                          ssh_opts=(-o BatchMode=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile="$known_hosts" -o IdentitiesOnly=yes -i "$key_file" -p "$GPU_DEPLOY_PORT")
                          echo 'upload dev $sha $digest activate dev $sha $digest status dev'
                          ssh "${ssh_opts[@]}" "$target" uptime
            """,
        }
        for filename, body in workflows.items():
            with self.subTest(filename=filename):
                self.assert_policy_violation(body, "workflow-dispatch-dev-deploy-guard", "deploy", filename)

    def test_rejects_gpu_live_synthetic_substring_bypasses(self):
        workflows = {
            "live-echoed-forced-protocol.yml": f"""
                on:
                  workflow_run:
                    workflows: [ci]
                    types: [completed]
                concurrency:
                  group: gpu-live
                  cancel-in-progress: false
                jobs:
                  authorize:
                    if: github.event.workflow_run.event == 'push' && github.event.workflow_run.head_branch == 'main' && github.event.workflow_run.conclusion == 'success' && github.event.workflow_run.head_repository.full_name == github.repository
                    runs-on: ubuntu-24.04
                    steps:
                      - run: python3.12 scripts/authorize_gpu_release.py --repository ${{{{ github.repository }}}} --workflow-run-file ${{{{ github.event_path }}}} --live
                  deploy:
                    needs: authorize
                    if: github.event.workflow_run.event == 'push' && github.event.workflow_run.head_branch == 'main' && github.event.workflow_run.conclusion == 'success' && github.event.workflow_run.head_repository.full_name == github.repository
                    runs-on: ubuntu-24.04
                    environment: gpu-live
                    steps:
                      - uses: actions/checkout@{PINNED_SHA}
                        with:
                          ref: ${{{{ github.event.workflow_run.head_sha }}}}
                      - run: |
                          echo 'github.event.workflow_run.head_sha upload live $sha $digest activate live $sha $digest status live StrictHostKeyChecking=yes UserKnownHostsFile IdentitiesOnly=yes GPU_DEPLOY_HOST GPU_DEPLOY_USER ^[0-9a-f]{{40}}$ ^[0-9a-f]{{64}}$ GPU_DEPLOY_PORT target'
                          ssh example.invalid uptime
            """,
            "live-wrong-checkout-ref.yml": f"""
                on:
                  workflow_run:
                    workflows: [ci]
                    types: [completed]
                concurrency:
                  group: gpu-live
                  cancel-in-progress: false
                jobs:
                  authorize:
                    if: github.event.workflow_run.event == 'push' && github.event.workflow_run.head_branch == 'main' && github.event.workflow_run.conclusion == 'success' && github.event.workflow_run.head_repository.full_name == github.repository
                    runs-on: ubuntu-24.04
                    steps:
                      - run: python3.12 scripts/authorize_gpu_release.py --repository ${{{{ github.repository }}}} --workflow-run-file ${{{{ github.event_path }}}} --live
                  deploy:
                    needs: authorize
                    if: github.event.workflow_run.event == 'push' && github.event.workflow_run.head_branch == 'main' && github.event.workflow_run.conclusion == 'success' && github.event.workflow_run.head_repository.full_name == github.repository
                    runs-on: ubuntu-24.04
                    environment: gpu-live
                    steps:
                      - uses: actions/checkout@{PINNED_SHA}
                        with:
                          ref: main
                      - run: |
                          sha="${{{{ github.event.workflow_run.head_sha }}}}"
                          [[ "$sha" =~ ^[0-9a-f]{{40}}$ ]]
                          [[ "$digest" =~ ^[0-9a-f]{{64}}$ ]]
                          [[ "$GPU_DEPLOY_HOST" =~ ^[A-Za-z0-9._-]+$ ]]
                          [[ "$GPU_DEPLOY_USER" =~ ^[A-Za-z0-9._-]+$ ]]
                          [[ "$GPU_DEPLOY_PORT" =~ ^[0-9]+$ ]]
                          target="$GPU_DEPLOY_USER@$GPU_DEPLOY_HOST"
                          ssh_opts=(-o BatchMode=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile="$known_hosts" -o IdentitiesOnly=yes -i "$key_file" -p "$GPU_DEPLOY_PORT")
                          ssh "${{ssh_opts[@]}}" "$target" "upload live $sha $digest" < "$artifact"
                          ssh "${{ssh_opts[@]}}" "$target" "activate live $sha $digest"
                          ssh "${{ssh_opts[@]}}" "$target" "status live"
            """,
            "live-arbitrary-ssh.yml": f"""
                on:
                  workflow_run:
                    workflows: [ci]
                    types: [completed]
                concurrency:
                  group: gpu-live
                  cancel-in-progress: false
                jobs:
                  authorize:
                    if: github.event.workflow_run.event == 'push' && github.event.workflow_run.head_branch == 'main' && github.event.workflow_run.conclusion == 'success' && github.event.workflow_run.head_repository.full_name == github.repository
                    runs-on: ubuntu-24.04
                    steps:
                      - run: python3.12 scripts/authorize_gpu_release.py --repository ${{{{ github.repository }}}} --workflow-run-file ${{{{ github.event_path }}}} --live
                  deploy:
                    needs: authorize
                    if: github.event.workflow_run.event == 'push' && github.event.workflow_run.head_branch == 'main' && github.event.workflow_run.conclusion == 'success' && github.event.workflow_run.head_repository.full_name == github.repository
                    runs-on: ubuntu-24.04
                    environment: gpu-live
                    steps:
                      - uses: actions/checkout@{PINNED_SHA}
                        with:
                          ref: ${{{{ github.event.workflow_run.head_sha }}}}
                      - run: |
                          sha="${{{{ github.event.workflow_run.head_sha }}}}"
                          [[ "$sha" =~ ^[0-9a-f]{{40}}$ ]]
                          [[ "$digest" =~ ^[0-9a-f]{{64}}$ ]]
                          [[ "$GPU_DEPLOY_HOST" =~ ^[A-Za-z0-9._-]+$ ]]
                          [[ "$GPU_DEPLOY_USER" =~ ^[A-Za-z0-9._-]+$ ]]
                          [[ "$GPU_DEPLOY_PORT" =~ ^[0-9]+$ ]]
                          target="$GPU_DEPLOY_USER@$GPU_DEPLOY_HOST"
                          ssh_opts=(-o BatchMode=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile="$known_hosts" -o IdentitiesOnly=yes -i "$key_file" -p "$GPU_DEPLOY_PORT")
                          echo 'upload live $sha $digest activate live $sha $digest status live'
                          ssh "${{ssh_opts[@]}}" "$target" uptime
            """,
        }
        for filename, body in workflows.items():
            with self.subTest(filename=filename):
                self.assert_policy_violation(body, "workflow-run-deploy-guard", "deploy", filename)

    def test_rejects_gpu_dev_extra_workflow_dispatch_inputs(self):
        for extra_input in ("sha", "ref", "branch"):
            with self.subTest(extra_input=extra_input):
                self.assert_policy_violation(
                    f"""
                    on:
                      workflow_dispatch:
                        inputs:
                          pr_number:
                            required: true
                          {extra_input}:
                            required: false
                    concurrency:
                      group: gpu-dev
                      cancel-in-progress: true
                    jobs:
                      deploy:
                        runs-on: ubuntu-24.04
                        environment: gpu-dev
                        steps:
                          - id: resolve
                            run: |
                              pr_number="${{{{ github.event.inputs.pr_number }}}}"
                              [[ "$pr_number" =~ ^[1-9][0-9]*$ ]]
                              pr_json=$(gh api "repos/${{{{ github.repository }}}}/pulls/$pr_number")
                              state=$(python3.12 -c 'print("open")')
                              [[ "$state" == open ]]
                              base_repo=$(python3.12 -c 'print("base.repo.full_name")')
                              [[ "$base_repo" == "$GITHUB_REPOSITORY" ]]
                              head_repo=$(python3.12 -c 'print("head.repo.full_name")')
                              [[ "$head_repo" == "$GITHUB_REPOSITORY" ]]
                              sha=$(python3.12 -c 'print("head.sha")')
                              [[ "$sha" =~ ^[0-9a-f]{{40}}$ ]]
                              gh api --paginate --slurp "repos/${{{{ github.repository }}}}/commits/$sha/check-runs"
                              completed_at id latest status conclusion ci/required
                          - uses: actions/checkout@0123456789abcdef0123456789abcdef01234567
                            with:
                              ref: ${{{{ steps.resolve.outputs.sha }}}}
                          - id: build
                            env:
                              SHA: ${{{{ steps.resolve.outputs.sha }}}}
                            run: |
                              sha="$SHA"
                              [[ "$sha" =~ ^[0-9a-f]{{40}}$ ]]
                          - env:
                              SHA: ${{{{ steps.resolve.outputs.sha }}}}
                              DIGEST: ${{{{ steps.build.outputs.digest }}}}
                              ARTIFACT: ${{{{ steps.build.outputs.artifact }}}}
                              GPU_DEPLOY_HOST: ${{{{ secrets.GPU_DEPLOY_HOST }}}}
                              GPU_DEPLOY_PORT: ${{{{ secrets.GPU_DEPLOY_PORT }}}}
                              GPU_DEPLOY_USER: ${{{{ secrets.GPU_DEPLOY_USER }}}}
                            run: |
                              sha="$SHA"
                              digest="$DIGEST"
                              artifact="$ARTIFACT"
                              [[ "$sha" =~ ^[0-9a-f]{{40}}$ ]]
                              [[ "$digest" =~ ^[0-9a-f]{{64}}$ ]]
                              [[ "$GPU_DEPLOY_HOST" =~ ^[A-Za-z0-9._-]+$ ]]
                              [[ "$GPU_DEPLOY_USER" =~ ^[A-Za-z0-9._-]+$ ]]
                              [[ "$GPU_DEPLOY_PORT" =~ ^[0-9]+$ ]]
                              target="$GPU_DEPLOY_USER@$GPU_DEPLOY_HOST"
                              ssh_opts=(-o BatchMode=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile="$known_hosts" -o IdentitiesOnly=yes -i "$key_file" -p "$GPU_DEPLOY_PORT")
                              ssh "${{{{ssh_opts[@]}}}}" "$target" "upload dev $sha $digest" < "$artifact"
                              ssh "${{{{ssh_opts[@]}}}}" "$target" "activate dev $sha $digest"
                              ssh "${{{{ssh_opts[@]}}}}" "$target" "status dev"
                    """,
                    "workflow-dispatch-dev-deploy-guard",
                    "deploy",
                    f"extra-{extra_input}.yml",
                )

    def test_rejects_gpu_live_cancel_in_progress_true(self):
        self.assert_policy_violation(
            f"""
            on:
              workflow_run:
                workflows: [ci]
                types: [completed]
            concurrency:
              group: gpu-live
              cancel-in-progress: true
            jobs:
              authorize:
                if: github.event.workflow_run.event == 'push' && github.event.workflow_run.head_branch == 'main' && github.event.workflow_run.conclusion == 'success' && github.event.workflow_run.head_repository.full_name == github.repository
                runs-on: ubuntu-24.04
                steps:
                  - run: python3.12 scripts/authorize_gpu_release.py --repository ${{{{ github.repository }}}} --workflow-run-file ${{{{ github.event_path }}}} --live
              deploy:
                needs: authorize
                if: github.event.workflow_run.event == 'push' && github.event.workflow_run.head_branch == 'main' && github.event.workflow_run.conclusion == 'success' && github.event.workflow_run.head_repository.full_name == github.repository
                runs-on: ubuntu-24.04
                environment: gpu-live
                steps:
                  - uses: actions/checkout@{PINNED_SHA}
                    with:
                      ref: ${{{{ github.event.workflow_run.head_sha }}}}
                  - id: build
                    env:
                      SHA: ${{{{ github.event.workflow_run.head_sha }}}}
                    run: |
                      sha="$SHA"
                      [[ "$sha" =~ ^[0-9a-f]{{40}}$ ]]
                  - env:
                      SHA: ${{{{ github.event.workflow_run.head_sha }}}}
                      DIGEST: ${{{{ steps.build.outputs.digest }}}}
                      ARTIFACT: ${{{{ steps.build.outputs.artifact }}}}
                      GPU_DEPLOY_HOST: ${{{{ secrets.GPU_DEPLOY_HOST }}}}
                      GPU_DEPLOY_PORT: ${{{{ secrets.GPU_DEPLOY_PORT }}}}
                      GPU_DEPLOY_USER: ${{{{ secrets.GPU_DEPLOY_USER }}}}
                    run: |
                      sha="$SHA"
                      digest="$DIGEST"
                      artifact="$ARTIFACT"
                      [[ "$sha" =~ ^[0-9a-f]{{40}}$ ]]
                      [[ "$digest" =~ ^[0-9a-f]{{64}}$ ]]
                      [[ "$GPU_DEPLOY_HOST" =~ ^[A-Za-z0-9._-]+$ ]]
                      [[ "$GPU_DEPLOY_USER" =~ ^[A-Za-z0-9._-]+$ ]]
                      [[ "$GPU_DEPLOY_PORT" =~ ^[0-9]+$ ]]
                      target="$GPU_DEPLOY_USER@$GPU_DEPLOY_HOST"
                      ssh_opts=(-o BatchMode=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile="$known_hosts" -o IdentitiesOnly=yes -i "$key_file" -p "$GPU_DEPLOY_PORT")
                      ssh "${{ssh_opts[@]}}" "$target" "upload live $sha $digest" < "$artifact"
                      ssh "${{ssh_opts[@]}}" "$target" "activate live $sha $digest"
                      ssh "${{ssh_opts[@]}}" "$target" "status live"
            """,
            "workflow-run-deploy-guard",
            "deploy",
            "live-cancel-true.yml",
        )

    def test_rejects_gpu_dev_extra_ssh_commands_even_with_required_forced_commands(self):
        for filename, extra_ssh in {
            "dev-extra-rollback.yml": 'ssh "${ssh_opts[@]}" "$target" "rollback dev"',
            "dev-extra-uptime.yml": 'ssh "${ssh_opts[@]}" "$target" uptime',
            "dev-extra-command-ssh.yml": 'command ssh "${ssh_opts[@]}" "$target" uptime',
            "dev-extra-env-ssh.yml": 'env ssh "${ssh_opts[@]}" "$target" uptime',
            "dev-extra-tab-ssh.yml": 'ssh	"${ssh_opts[@]}" "$target" uptime',
            "dev-extra-env-assignment-ssh.yml": 'LC_ALL=C ssh "${ssh_opts[@]}" "$target" uptime',
        }.items():
            workflow = Path(".github/workflows/deploy-gpu-dev.yml").read_text(encoding="utf-8")
            workflow = workflow.replace('ssh "${ssh_opts[@]}" "$target" "status dev"', 'ssh "${ssh_opts[@]}" "$target" "status dev"\n          ' + extra_ssh)
            with self.subTest(filename=filename):
                self.assert_policy_violation(workflow, "workflow-dispatch-dev-deploy-guard", "deploy", filename)

    def test_rejects_gpu_live_extra_ssh_commands_even_with_required_forced_commands(self):
        for filename, extra_ssh in {
            "live-extra-rollback.yml": 'ssh "${ssh_opts[@]}" "$target" "rollback live"',
            "live-extra-uptime.yml": 'ssh "${ssh_opts[@]}" "$target" uptime',
            "live-extra-command-ssh.yml": 'command ssh "${ssh_opts[@]}" "$target" uptime',
            "live-extra-env-ssh.yml": 'env ssh "${ssh_opts[@]}" "$target" uptime',
            "live-extra-tab-ssh.yml": 'ssh	"${ssh_opts[@]}" "$target" uptime',
            "live-extra-env-assignment-ssh.yml": 'LC_ALL=C ssh "${ssh_opts[@]}" "$target" uptime',
        }.items():
            workflow = Path(".github/workflows/deploy-gpu-live.yml").read_text(encoding="utf-8")
            workflow = workflow.replace('ssh "${ssh_opts[@]}" "$target" "status live"', 'ssh "${ssh_opts[@]}" "$target" "status live"\n          ' + extra_ssh)
            with self.subTest(filename=filename):
                self.assert_policy_violation(workflow, "workflow-run-deploy-guard", "deploy", filename)

    def test_rejects_gpu_deployment_workflows_with_storage_coupling(self):
        for filename, event in (("dev-storage.yml", "workflow_dispatch"), ("live-storage.yml", "workflow_run")):
            with self.subTest(filename=filename):
                if event == "workflow_dispatch":
                    body = """
                    on:
                      workflow_dispatch:
                        inputs:
                          pr_number:
                            required: true
                    concurrency:
                      group: gpu-dev
                      cancel-in-progress: true
                    jobs:
                      deploy:
                        runs-on: ubuntu-24.04
                        environment: gpu-dev
                        steps:
                          - run: gh api repos/${{ github.repository }}/pulls/$pr_number && ci/required
                          - run: echo storage-monitor
                          - run: ssh "$target" "upload dev $sha $digest" < "$artifact"
                    """
                    rule = "workflow-dispatch-dev-deploy-guard"
                    job = "deploy"
                else:
                    body = self.split_authorized_live_workflow(extra_deploy_step="- run: echo storage-monitor")
                    rule = "gpu-deploy-storage-coupling"
                    job = None
                self.assert_policy_violation(body, rule, job, filename)

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
        result = self.run_validator({"gpu-live.yml": self.split_authorized_live_workflow()})

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)




    def split_authorized_live_workflow(self, *, extra_deploy_step: str = "", auth_continue: str | None = None) -> str:
        extra = textwrap.indent(textwrap.dedent(extra_deploy_step).strip("\n"), "                      ") if extra_deploy_step else ""
        if extra:
            extra = "\n" + extra
        continue_line = f"\n                    continue-on-error: {auth_continue}" if auth_continue else ""
        return f"""
            on:
              workflow_run:
                workflows: [ci]
                types: [completed]
            permissions: read-all
            concurrency:
              group: gpu-live
              cancel-in-progress: false
            jobs:
              authorize:
                if: github.event.workflow_run.event == 'push' && github.event.workflow_run.head_branch == 'main' && github.event.workflow_run.conclusion == 'success' && github.event.workflow_run.head_repository.full_name == github.repository
                runs-on: ubuntu-24.04
                steps:
                  - uses: actions/checkout@{PINNED_SHA}
                  - name: Authorize deployment{continue_line}
                    run: python3.12 scripts/authorize_gpu_release.py --repository ${{{{ github.repository }}}} --workflow-run-file ${{{{ github.event_path }}}} --live --required-check ci/required
              deploy:
                needs: authorize
                if: github.event.workflow_run.event == 'push' && github.event.workflow_run.head_branch == 'main' && github.event.workflow_run.conclusion == 'success' && github.event.workflow_run.head_repository.full_name == github.repository
                runs-on: ubuntu-24.04
                environment: gpu-live
                steps:
                  - uses: actions/checkout@{PINNED_SHA}
                    with:
                      ref: ${{{{ github.event.workflow_run.head_sha }}}}
                  - id: build
                    env:
                      SHA: ${{{{ github.event.workflow_run.head_sha }}}}
                    run: |
                      sha="$SHA"
                      [[ "$sha" =~ ^[0-9a-f]{{40}}$ ]]
                  - env:
                      SHA: ${{{{ github.event.workflow_run.head_sha }}}}
                      DIGEST: ${{{{ steps.build.outputs.digest }}}}
                      ARTIFACT: ${{{{ steps.build.outputs.artifact }}}}
                      GPU_DEPLOY_HOST: ${{{{ secrets.GPU_DEPLOY_HOST }}}}
                      GPU_DEPLOY_PORT: ${{{{ secrets.GPU_DEPLOY_PORT }}}}
                      GPU_DEPLOY_USER: ${{{{ secrets.GPU_DEPLOY_USER }}}}
                    run: |
                      sha="$SHA"
                      digest="$DIGEST"
                      artifact="$ARTIFACT"
                      target="$GPU_DEPLOY_USER@$GPU_DEPLOY_HOST"
                      [[ "$sha" =~ ^[0-9a-f]{{40}}$ ]]
                      [[ "$digest" =~ ^[0-9a-f]{{64}}$ ]]
                      [[ "$GPU_DEPLOY_HOST" =~ ^[A-Za-z0-9._-]+$ ]]
                      [[ "$GPU_DEPLOY_USER" =~ ^[A-Za-z0-9._-]+$ ]]
                      [[ "$GPU_DEPLOY_PORT" =~ ^[0-9]+$ ]]
                      [[ "$target" =~ ^[A-Za-z0-9._-]+@[A-Za-z0-9._-]+$ ]]
                      ssh_opts=(-o BatchMode=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile="$known_hosts" -o IdentitiesOnly=yes -i "$key_file" -p "$GPU_DEPLOY_PORT")
                      ssh "${{ssh_opts[@]}}" "$target" "upload live $sha $digest" < "$artifact"
                      ssh "${{ssh_opts[@]}}" "$target" "activate live $sha $digest"
                      ssh "${{ssh_opts[@]}}" "$target" "status live"{extra}
        """



    def guarded_workflow_run(self, run_step: str, *, step_prefix: str = "- name: Authorize deployment", extra_job: str = "") -> str:
        run_step = textwrap.indent(textwrap.dedent(run_step).strip("\n"), "                        ")
        extra_job = textwrap.indent(textwrap.dedent(extra_job).strip("\n"), "                    ") if extra_job else ""
        if extra_job:
            extra_job = "\n" + extra_job
        return f"""
            on:
              workflow_run:
                workflows: [ci]
                types: [completed]
            jobs:
              release-gpu:
                if: github.event.workflow_run.event == 'push' && github.event.workflow_run.head_branch == 'main' && github.event.workflow_run.conclusion == 'success' && github.event.workflow_run.head_repository.full_name == github.repository
                runs-on: ubuntu-24.04
                environment: gpu-live{extra_job}
                steps:
                  {step_prefix}
{run_step}
        """

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
                    runs-on: ubuntu-24.04
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
                    runs-on: ubuntu-24.04
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
                    runs-on: ubuntu-24.04
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


    def test_rejects_authorization_command_that_is_not_strict_gate(self):
        workflows = {
            "or-mask.yml": self.guarded_workflow_run("run: python3.12 scripts/authorize_gpu_release.py --repo owner/repo || true"),
            "and-chain.yml": self.guarded_workflow_run("run: python3.12 scripts/authorize_gpu_release.py --repo owner/repo && ./deploy.sh"),
            "semicolon.yml": self.guarded_workflow_run("run: python3.12 scripts/authorize_gpu_release.py --repo owner/repo; ./deploy.sh"),
            "pipe.yml": self.guarded_workflow_run("run: python3.12 scripts/authorize_gpu_release.py --repo owner/repo | cat"),
            "redirect.yml": self.guarded_workflow_run("run: python3.12 scripts/authorize_gpu_release.py --repo owner/repo > /tmp/auth.log"),
            "subshell.yml": self.guarded_workflow_run("run: $(python3.12 scripts/authorize_gpu_release.py --repo owner/repo)"),
            "background.yml": self.guarded_workflow_run("run: python3.12 scripts/authorize_gpu_release.py --repo owner/repo &"),
            "attached-background.yml": self.guarded_workflow_run("run: python3.12 scripts/authorize_gpu_release.py --repo owner/repo& ./deploy.sh"),
            "attached-double-background.yml": self.guarded_workflow_run("run: python3.12 scripts/authorize_gpu_release.py --repo owner/repo&& ./deploy.sh"),
            "leading-background.yml": self.guarded_workflow_run("run: python3.12 scripts/authorize_gpu_release.py --repo owner/repo&"),
            "step-if.yml": self.guarded_workflow_run(
                """
                if: always()
                run: python3.12 scripts/authorize_gpu_release.py --repo owner/repo
                """
            ),
            "continue-on-error.yml": self.guarded_workflow_run(
                """
                continue-on-error: true
                run: python3.12 scripts/authorize_gpu_release.py --repo owner/repo
                """
            ),
            "working-directory.yml": self.guarded_workflow_run(
                """
                working-directory: /tmp
                run: python3.12 scripts/authorize_gpu_release.py --repo owner/repo
                """
            ),
        }
        for filename, body in workflows.items():
            with self.subTest(filename=filename):
                self.assert_policy_violation(body, "workflow-run-deploy-guard", "release-gpu", filename)

    def test_accepts_safe_multiline_authorization_run_block(self):
        result = self.run_validator({"gpu-live.yml": self.split_authorized_live_workflow()})

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)



    def test_continue_on_error_must_be_explicitly_absent_or_false_for_authorization(self):
        rejected_values = {
            "yes.yml": "yes",
            "on.yml": "on",
            "one.yml": "1",
            "spaced-true-expression.yml": "${{ true }}",
            "dynamic-expression.yml": "${{ always() }}",
            "unknown-expression.yml": "${{ github.ref == 'refs/heads/main' }}",
            "arbitrary-word.yml": "maybe",
        }
        for filename, value in rejected_values.items():
            with self.subTest(filename=filename):
                self.assert_policy_violation(
                    self.guarded_workflow_run(
                        f"""
                        continue-on-error: {value}
                        run: python3.12 scripts/authorize_gpu_release.py --repo owner/repo
                        """
                    ),
                    "workflow-run-deploy-guard",
                    "release-gpu",
                    filename,
                )

        accepted_values = {
            "false.yml": "false",
            "false-expression.yml": "${{ false }}",
        }
        for filename, value in accepted_values.items():
            with self.subTest(filename=filename):
                result = self.run_validator(
                    {filename: self.split_authorized_live_workflow(auth_continue=value)}
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_deployment_name_exceptions_are_field_local_only(self):
        workflows = {
            "job-id-deployer-display-release-notes.yml": ("gpu-deployer", "release-notes"),
            "job-id-release-notes-display-deploy.yml": ("release-notes", "Deploy production"),
            "job-id-activate-venv-display-deployment.yml": ("activate-venv", "deployment smoke"),
        }
        for filename, (job_id, display_name) in workflows.items():
            with self.subTest(filename=filename):
                self.assert_policy_violation(
                    f"""
                    on: push
                    jobs:
                      {job_id}:
                        name: {display_name}
                        runs-on: ubuntu-24.04
                        steps:
                          - run: true
                    """,
                    "deploy-main-guard",
                    job_id,
                    filename,
                )

    def test_rejects_deployment_runners_except_exact_known_github_hosted_labels(self):
        workflows = {
            "ubuntu-latest.yml": "ubuntu-latest",
            "ubuntu-gpu.yml": "ubuntu-gpu",
            "custom-prefix.yml": "ubuntu-24.04-gpu",
            "array-extra.yml": "[ubuntu-24.04, gpu-live]",
            "self-hosted.yml": "[ubuntu-24.04, self-hosted]",
        }
        for filename, runs_on in workflows.items():
            with self.subTest(filename=filename):
                self.assert_policy_violation(
                    f"""
                    on: push
                    jobs:
                      deploy:
                        if: github.ref == 'refs/heads/main'
                        runs-on: {runs_on}
                        steps:
                          - run: ./deploy.sh
                    """,
                    "deploy-runner",
                    "deploy",
                    filename,
                )

    def test_detects_deployment_inflections_and_strong_signals_override_exceptions(self):
        workflows = {
            "deployment.yml": ("deployment-check", "deploy-main-guard"),
            "deployer.yml": ("gpu-deployer", "deploy-main-guard"),
            "deployed.yml": ("gpu-deployed", "deploy-main-guard"),
            "releasing.yml": ("gpu-releasing", "deploy-main-guard"),
            "activation.yml": ("gpu-activation", "deploy-main-guard"),
            "exact-release-overrides-name.yml": ("release", "deploy-main-guard"),
            "exact-activate-overrides-name.yml": ("activate", "deploy-main-guard"),
        }
        for filename, (job_id, rule) in workflows.items():
            with self.subTest(filename=filename):
                name_line = "name: release-notes" if "overrides" in filename else "name: smoke"
                self.assert_policy_violation(
                    f"""
                    on: push
                    jobs:
                      {job_id}:
                        {name_line}
                        runs-on: ubuntu-24.04
                        steps:
                          - run: true
                    """,
                    rule,
                    job_id,
                    filename,
                )

    def test_secret_expression_scanner_ignores_quoted_strings_but_handles_embedded_closing_braces(self):
        result = self.run_validator(
            {
                "quoted.yml": """
                on: pull_request
                jobs:
                  unit:
                    runs-on: ubuntu-latest
                    steps:
                      - run: echo "${{ format('https://example.invalid/secrets }} docs', github.ref_name) }}"
                """
            }
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        self.assert_policy_violation(
            """
            on: pull_request
            jobs:
              unit:
                runs-on: ubuntu-latest
                steps:
                  - run: echo "${{ format('}}', secrets.DEPLOY_TOKEN) }}"
            """,
            "pr-secrets",
            "unit",
        )

    def test_rejects_pull_request_secret_context_after_jobs_section(self):
        self.assert_policy_violation(
            """
            on: pull_request
            jobs:
              unit:
                runs-on: ubuntu-latest
                steps:
                  - run: true
            env:
              TOKEN: ${{ secrets.DEPLOY_TOKEN }}
            """,
            "pr-secrets",
        )

    def test_allows_nested_with_and_matrix_environment_keys(self):
        result = self.run_validator(
            {
                "nested-env.yml": f"""
                on: push
                jobs:
                  test:
                    runs-on: ubuntu-latest
                    strategy:
                      matrix:
                        environment: [dev, test]
                    steps:
                      - uses: actions/checkout@{PINNED_SHA}
                        with:
                          environment: dev
                      - run: true
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
                    runs-on: ubuntu-24.04
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
