import subprocess
import unittest
from pathlib import Path


ALLOWED_STORAGE_SAMPLE_FIXTURES = {
    "apps/storage-monitor/data/atlas.sample.json",
    "apps/storage-monitor/data/hinton.sample.json",
    "apps/storage-monitor/data/hosts.json",
    "apps/storage-monitor/data/orion.sample.json",
    "apps/storage-monitor/data/zeus.sample.json",
}

DISALLOWED_DIR_NAMES = {
    ".cache",
    ".omx",
    ".pytest_cache",
    ".ruff_cache",
    ".svelte-kit",
    ".superpowers",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "playwright-report",
    "test-results",
    "venv",
}

DISALLOWED_FILE_NAMES = {
    ".env",
    ".coverage",
    "coverage.json",
}

DISALLOWED_SUFFIXES = {
    ".db",
    ".db-shm",
    ".db-wal",
    ".pyc",
    ".pyo",
    ".sqlite",
    ".sqlite3",
}

RUNTIME_JSON_DIR_PARTS = {
    "cache",
    "caches",
    "coverage",
    "output",
    "outputs",
    "playwright",
    "reports",
    "results",
    "runtime",
    "snapshots",
}

BROWSER_OUTPUT_SUFFIXES = {
    ".png",
    ".webm",
    ".trace",
    ".zip",
}


def workflow_text(path: str = ".github/workflows/ci.yml") -> str:
    return Path(path).read_text(encoding="utf-8")

def makefile_text() -> str:
    return Path("Makefile").read_text()


def make_target_names() -> set[str]:
    targets: set[str] = set()
    for line in makefile_text().splitlines():
        if not line or line.startswith(("	", "#", ".")) or ":" not in line:
            continue
        target_part = line.split(":", 1)[0]
        if "=" in target_part:
            continue
        targets.update(name for name in target_part.split() if name)
    return targets


def make_target_dependencies(target: str) -> list[str]:
    prefix = f"{target}:"
    for line in makefile_text().splitlines():
        if line.startswith(prefix):
            return line.split(":", 1)[1].split()
    return []

def make_target_recipe(target: str) -> list[str]:
    lines = makefile_text().splitlines()
    recipe: list[str] = []
    in_target = False
    for line in lines:
        if line == f"{target}:":
            in_target = True
            continue
        if in_target:
            if line.startswith("	") or not line:
                if line.startswith("	"):
                    recipe.append(line[1:])
                continue
            break
    return recipe


def normalized_make_recipe(target: str) -> str:
    return "\n".join(make_target_recipe(target))


def tracked_paths() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return [line for line in result.stdout.splitlines() if line]


def is_disallowed_tracked_path(path: str) -> bool:
    if path in ALLOWED_STORAGE_SAMPLE_FIXTURES:
        return False

    parts = Path(path).parts
    name = parts[-1]
    suffix = Path(path).suffix

    if any(part in DISALLOWED_DIR_NAMES for part in parts):
        return True
    if name in DISALLOWED_FILE_NAMES or (name.startswith(".env.") and not name.endswith(".example")):
        return True
    if suffix in DISALLOWED_SUFFIXES:
        return True
    if suffix == ".json" and any(part in RUNTIME_JSON_DIR_PARTS for part in parts):
        return True
    if suffix in BROWSER_OUTPUT_SUFFIXES and any(part in RUNTIME_JSON_DIR_PARTS for part in parts):
        return True
    return False


class RepositoryLayoutTest(unittest.TestCase):
    def test_applications_live_under_independent_app_directories(self):
        assert Path("apps/gpu-monitor/frontend/package.json").is_file()
        assert Path("apps/gpu-monitor/backend/main.py").is_file()
        assert Path("apps/storage-monitor/viewer/serve.py").is_file()
        assert Path("apps/storage-monitor/agent/scan_runner.py").is_file()
        assert not Path("frontend").exists()
        assert not Path("backend").exists()


    def test_root_makefile_exposes_exact_application_command_contracts(self):
        self.assertEqual("SHELL := /bin/bash", makefile_text().splitlines()[0])
        self.assertEqual(
            ["layout-test", "history-test", "release-puller-test"],
            make_target_dependencies("test"),
        )
        self.assertEqual(
            [
                "cd apps/gpu-monitor/frontend && npm run check",
                "cd apps/gpu-monitor/frontend && npm run build",
                "cd apps/gpu-monitor/frontend && npm run test:runtime",
                "cd apps/gpu-monitor && SECRET_KEY=baseline-test-key ADMIN_PASSWORD=baseline-test-password python3.12 -m unittest discover -s backend/tests -v",
            ],
            make_target_recipe("test-gpu"),
        )
        self.assertEqual(
            ["cd apps/gpu-monitor/frontend && npm run build"],
            make_target_recipe("build-gpu"),
        )
        self.assertEqual(
            ["test", "test-gpu", "test-storage", "diff-check"],
            make_target_dependencies("verify"),
        )

        text = makefile_text()
        self.assertNotIn("npm --workspace", text)
        self.assertNotIn("pnpm", text)
        self.assertNotIn("yarn workspace", text)

    def test_root_python_verification_recipes_suppress_bytecode_writes(self):
        self.assertEqual(
            ["PYTHONDONTWRITEBYTECODE=1 python3.12 -m unittest tests.test_repository_layout -v"],
            make_target_recipe("layout-test"),
        )
        self.assertEqual(
            ["PYTHONDONTWRITEBYTECODE=1 python3.12 -m unittest tests.test_history_inventory -v"],
            make_target_recipe("history-test"),
        )
        self.assertEqual(
            ["PYTHONDONTWRITEBYTECODE=1 python3.12 -m unittest tests.test_gpu_release_puller -v"],
            make_target_recipe("release-puller-test"),
        )
        root_python_recipes = {
            target: recipe
            for target in ("layout-test", "history-test", "release-puller-test")
            for recipe in make_target_recipe(target)
        }
        self.assertTrue(root_python_recipes)
        for target, recipe in root_python_recipes.items():
            self.assertIn("PYTHONDONTWRITEBYTECODE=1", recipe, target)
            self.assertNotIn("pytest", recipe, target)
            self.assertNotIn("cacheprovider", recipe, target)

    def test_storage_make_targets_run_artifact_checks_in_disposable_clone(self):
        self.assertEqual(
            [
                '@set -euo pipefail; \\',
                'assembled=$$(git rev-parse --show-toplevel); \\',
                'storage_verify=$$(mktemp -d /tmp/storage-monorepo-command-check.XXXXXX); \\',
                'trap \'rm -rf "$$storage_verify"\' EXIT; \\',
                'git clone --no-hardlinks "$$assembled" "$$storage_verify/repo"; \\',
                'rsync -a --delete \\',
                "  --exclude '.git/' \\",
                "  --exclude '.pytest_cache/' \\",
                "  --exclude '__pycache__/' \\",
                "  --exclude 'output/verification/' \\",
                '  "$$assembled/apps/storage-monitor/" \\',
                '  "$$storage_verify/repo/apps/storage-monitor/"; \\',
                'cd "$$storage_verify/repo/apps/storage-monitor"; \\',
                'PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q -p no:cacheprovider; \\',
                "find viewer -maxdepth 1 -name '*.js' -print0 | xargs -0 -n1 node --check; \\",
                'bash deploy/test_deploy_scripts.sh; \\',
                "test ! -e scanner/hstscan || { printf '%s\\n' 'FAIL: deploy tests left scanner/hstscan behind'; exit 1; }; \\",
                "test ! -e output/verification/linux-verification.txt || { printf '%s\\n' 'FAIL: deploy tests left a verification artifact behind'; exit 1; }; \\",
                'if [ "$$(uname -s)" = Linux ]; then \\',
                '  $(MAKE) -C scanner clean all test; \\',
                '  bash deploy/verify-linux.sh --local; \\',
                'else \\',
                "  printf '%s\\n' 'SKIP: Linux-only scanner tests use SYS_getdents64; covered by Task 3 remote Linux verification.'; \\",
                'fi',
            ],
            make_target_recipe("test-storage"),
        )

    def test_storage_deploy_tests_use_one_aggregate_exit_cleanup(self):
        script = Path("apps/storage-monitor/deploy/test_deploy_scripts.sh").read_text()
        exit_traps = [
            line.strip()
            for line in script.splitlines()
            if line.startswith("trap ") and line.endswith(" EXIT")
        ]

        self.assertEqual(exit_traps, ["trap cleanup EXIT"])
        for owned_path in (
            '${VERIFY_TMP:-}',
            '${TMP:-}',
            '${VIEWER_SECRET:-}',
            '${STALE_SCANNER:-}',
            '$ROOT/output/verification/linux-verification.txt',
        ):
            self.assertIn(owned_path, script)

    def test_gpu_shell_scripts_are_app_local_and_resolve_root_from_script_location(self):
        old_script_paths = [
            Path("apps/gpu-monitor/run_monitoring.sh"),
            Path("apps/gpu-monitor/run_development.sh"),
        ]
        new_script_paths = [
            Path("apps/gpu-monitor/scripts/run_monitoring.sh"),
            Path("apps/gpu-monitor/scripts/run_development.sh"),
        ]

        for path in old_script_paths:
            self.assertFalse(path.exists(), f"legacy root-level script remains: {path}")
        for path in new_script_paths:
            self.assertTrue(path.is_file(), f"missing app-local script: {path}")
            content = path.read_text()
            self.assertIn('ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"', content)
            self.assertNotIn("/home/ircv/workspace", content)
            self.assertNotIn('ROOT_DIR="$(pwd)"', content)

    def test_storage_readme_commands_match_their_stated_working_directory(self):
        content = Path("apps/storage-monitor/README.md").read_text()
        self.assertIn("Run the local sample dashboard from `apps/storage-monitor`:", content)
        self.assertIn('STORAGE_VIZ_DEV_SAMPLE_DIR="$(pwd)/data"', content)
        self.assertIn("python3 viewer/serve.py", content)
        self.assertNotIn("Run the local sample dashboard from the repository root:", content)

    def test_ci_workflow_contract_defines_required_path_aware_validation(self):
        path = Path(".github/workflows/ci.yml")
        self.assertTrue(path.is_file(), "missing GitHub Actions CI workflow")
        content = workflow_text()

        self.assertNotIn("pull_request_target", content)
        self.assertIn("pull_request:", content)
        self.assertIn("push:", content)
        self.assertIn("workflow_dispatch:", content)
        self.assertIn("contents: read", content)
        self.assertIn("fetch-depth: 0", content)
        self.assertIn("scripts/ci_impact.py", content)
        self.assertIn("scripts/validate_workflows.py .github/workflows", content)

        for action in (
            "actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683",
            "actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065",
            "actions/setup-node@49933ea5288caeca8642d1e84afbd3f7d6820020",
        ):
            self.assertIn(action, content)

        for required_text in (
            "name: ci/required",
            "name: ci/gpu",
            "name: ci/storage",
            "if: always()",
            "needs: [impact, repository, gpu, storage]",
            "needs.impact.outputs.gpu",
            "needs.impact.outputs.storage_dashboard",
            "needs.impact.outputs.storage_agent",
            "needs.gpu.result",
            "needs.storage.result",
            "expected=${{ needs.impact.outputs.gpu }}",
            "expected_storage=$([[ '${{ needs.impact.outputs.storage_dashboard }}' == 'true' || '${{ needs.impact.outputs.storage_agent }}' == 'true' ]]",
            "if [ \"${expected}\" = true ] && [ \"${actual}\" != success ]; then",
            "if [ \"${expected}\" = false ] && [ \"${actual}\" != skipped ]; then",
            "--merge-base",
            "--fallback-to-all-tracked",
            "git ls-files > /tmp/ci-all-paths.txt",
            "git hash-object -t tree /dev/null",
            "node-version: '22.14.0'",
            "npm run test:runtime",
            "python-version: '3.12.10'",
            "pytest==8.4.1",
        ):
            self.assertIn(required_text, content)

        self.assertNotIn("self-hosted", content)
        self.assertNotIn("python -m pip install --upgrade pip", content)
        self.assertNotIn("python-version: '3.12'", content)
        self.assertNotIn("node-version: '20'", content)
        self.assertNotIn("run: python3.12 -m pip install pytest\n", content)

        check_index = content.index("- name: Check frontend")
        build_index = content.index("- name: Build frontend")
        runtime_index = content.index("- name: Test frontend runtime proxy")
        self.assertLess(check_index, build_index)
        self.assertLess(build_index, runtime_index)

    def test_tracked_files_exclude_generated_runtime_and_local_environment_data(self):
        disallowed = [path for path in tracked_paths() if is_disallowed_tracked_path(path)]
        self.assertEqual([], disallowed)

    def test_diff_check_accepts_an_explicit_committed_range(self):
        recipe = normalized_make_recipe("diff-check")
        self.assertIn("DIFF_CHECK_BASE", recipe)
        self.assertIn("DIFF_CHECK_HEAD", recipe)
        self.assertIn('git diff --check "$$base" "$$head"', recipe)

    def test_gpu_release_policy_documents_trusted_team_sha_contract(self):
        workflow_design = Path(
            "docs/superpowers/specs/2026-07-23-development-release-workflow-design.md"
        ).read_text(encoding="utf-8")
        github_cicd = Path("docs/operations/github-cicd.md").read_text(encoding="utf-8")
        old_monorepo_design = Path(
            "docs/superpowers/specs/2026-07-22-monitoring-platform-monorepo-design.md"
        ).read_text(encoding="utf-8")
        deploy_checker = Path("scripts/check_deploy_prerequisites.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("PR head SHA", workflow_design)
        self.assertIn("direct push", workflow_design.lower())
        self.assertIn("This design is superseded for current release policy by", workflow_design)
        self.assertIn("docs/superpowers/specs/2026-07-25-local-development-live-release-design.md", workflow_design)
        self.assertIn("GitHub-hosted inbound SSH", github_cicd)
        self.assertIn("forced-command", github_cicd)
        self.assertIn("Storage agents and dashboards remain manual/tagged", github_cicd)
        self.assertIn(
            '"runner_availability": runner_check(metadata)',
            deploy_checker,
        )
        self.assertIn(
            "legacy branch-protected/self-hosted readiness model",
            github_cicd,
        )
        self.assertIn(
            "not the current GPU Live authorization gate",
            github_cicd,
        )
        self.assertIn("Historical design artifact (superseded).", workflow_design)
        self.assertIn("Historical rationale", old_monorepo_design)
        self.assertIn("Legacy", github_cicd)


    def test_gpu_deployment_workflow_is_live_only(self):
        self.assertFalse(Path(".github/workflows/deploy-gpu-dev.yml").exists())
        self.assertFalse(Path(".github/workflows/deploy-gpu-live.yml").exists())
        self.assertTrue(
            Path("apps/gpu-monitor/deploy/server/gpu-monitor-release-puller.py").is_file()
        )
        self.assertTrue(
            Path(
                "apps/gpu-monitor/deploy/server/systemd/gpu-monitor-release-puller.service"
            ).is_file()
        )
        self.assertTrue(
            Path(
                "apps/gpu-monitor/deploy/server/systemd/gpu-monitor-release-puller.timer"
            ).is_file()
        )
        workflows = "\n".join(
            path.read_text(encoding="utf-8")
            for path in Path(".github/workflows").glob("*.y*ml")
        )
        self.assertNotIn("GPU_DEPLOY_SSH_KEY", workflows)
        self.assertNotIn("upload live", workflows)
        self.assertNotIn("activate live", workflows)


    def test_gpu_deployment_documentation_covers_operator_contracts(self):
        cicd = Path("docs/operations/github-cicd.md").read_text(encoding="utf-8")
        development = Path("docs/development.md").read_text(encoding="utf-8")
        combined = cicd + "\n" + development

        for retired_secret in (
            "GPU_DEPLOY_HOST",
            "GPU_DEPLOY_PORT",
            "GPU_DEPLOY_USER",
            "GPU_DEPLOY_SSH_KEY",
            "GPU_DEPLOY_KNOWN_HOSTS",
        ):
            self.assertNotIn(retired_secret, combined)
        self.assertIn("Pull requests are optional", combined)
        self.assertIn("trusted-team direct pushes to `main`", combined)
        self.assertIn("changed `main`", combined)
        self.assertIn("public GitHub API", combined)
        self.assertIn("scripts/authorize_gpu_release.py", combined)
        self.assertIn("gpu-monitor-builder", combined)
        self.assertIn("gpu-deploy-live", combined)
        self.assertIn("persistent five-minute calendar cadence", combined)
        self.assertIn("exponentially backs off retries", combined)
        self.assertIn("not a defense against malicious or compromised trusted writers", combined)
        self.assertIn("branch protection with required review is the stronger future control", combined)
        self.assertIn("local development", combined.lower())
        self.assertIn("does not enable or start the puller timer/service", cicd.lower())
        self.assertIn("GitHub-hosted SSH deployment workflow has been removed", combined)
        operation_section = development.split(
            "## GPU Live outbound deployment operation", 1
        )[1].split("##", 1)[0]
        self.assertIn("changed `main`", operation_section)
        self.assertIn("status live", operation_section)

        for phrase in (
            "ci/required",
            "gpu-live",
            "status",
            "rollback",
        ):
            self.assertIn(phrase, combined)

    def test_readme_and_contributing_contracts_use_exact_live_release_flow(self):
        expected_flow = (
            "local development -> optional PR or trusted direct main push -> main CI -> "
            "outbound server puller -> exact successful SHA live activation"
        )
        readme = Path("README.md").read_text(encoding="utf-8")
        contributing = Path("CONTRIBUTING.md").read_text(encoding="utf-8")

        self.assertIn(expected_flow, readme)
        self.assertIn(expected_flow, contributing)
        self.assertNotIn("Merging this foundation", readme)
        self.assertNotIn("Merging this foundation", contributing)


if __name__ == "__main__":
    unittest.main()
