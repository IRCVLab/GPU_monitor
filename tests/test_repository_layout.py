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
    ".pytest_cache",
    ".ruff_cache",
    ".svelte-kit",
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
            ["layout-test", "history-test"],
            make_target_dependencies("test"),
        )
        self.assertEqual(
            [
                "cd apps/gpu-monitor/frontend && npm run check",
                "cd apps/gpu-monitor && SECRET_KEY=baseline-test-key ADMIN_PASSWORD=baseline-test-password python3.12 -m unittest discover -s backend/tests -v",
            ],
            make_target_recipe("test-gpu"),
        )
        self.assertEqual(
            ["cd apps/gpu-monitor/frontend && npm run build"],
            make_target_recipe("build-gpu"),
        )
        self.assertEqual(
            ["layout-test", "history-test", "test-gpu", "build-gpu", "test-storage", "diff-check"],
            make_target_dependencies("verify"),
        )

        text = makefile_text()
        self.assertNotIn("npm --workspace", text)
        self.assertNotIn("pnpm", text)
        self.assertNotIn("yarn workspace", text)

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
                'if [ "$$(uname -s)" = Linux ]; then \\',
                '  bash scanner/test_hstscan.sh; \\',
                '  bash deploy/verify-linux.sh --local; \\',
                'else \\',
                "  printf '%s\\n' 'SKIP: Linux-only scanner tests use SYS_getdents64; covered by Task 3 remote Linux verification.'; \\",
                'fi',
            ],
            make_target_recipe("test-storage"),
        )

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

    def test_tracked_files_exclude_generated_runtime_and_local_environment_data(self):
        disallowed = [path for path in tracked_paths() if is_disallowed_tracked_path(path)]
        self.assertEqual([], disallowed)


if __name__ == "__main__":
    unittest.main()
