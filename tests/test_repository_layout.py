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

    def test_tracked_files_exclude_generated_runtime_and_local_environment_data(self):
        disallowed = [path for path in tracked_paths() if is_disallowed_tracked_path(path)]
        self.assertEqual([], disallowed)


if __name__ == "__main__":
    unittest.main()
