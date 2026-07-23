import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts import ci_impact


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "ci_impact.py"
DECISION_KEYS = (
    "gpu",
    "storage_dashboard",
    "storage_agent",
    "shared",
    "workflow",
    "documentation",
    "apps_required",
)


class CiImpactTest(unittest.TestCase):
    def classify(self, *paths: str) -> dict[str, bool]:
        return ci_impact.classify_paths(paths)

    def assert_decisions(self, paths: tuple[str, ...], **expected: bool) -> None:
        decisions = self.classify(*paths)
        self.assertEqual(set(DECISION_KEYS), set(decisions))
        for key in DECISION_KEYS:
            self.assertEqual(expected.get(key, False), decisions[key], key)

    def run_script(self, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["python3.12", str(SCRIPT), *args],
            cwd=REPO_ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={**os.environ, **(env or {})},
        )

    def test_gpu_only_change_requires_only_gpu_application(self):
        self.assert_decisions(
            ("apps/gpu-monitor/frontend/src/routes/+page.svelte",),
            gpu=True,
            apps_required=True,
        )

    def test_storage_dashboard_only_change_requires_only_storage_dashboard(self):
        self.assert_decisions(
            ("apps/storage-monitor/viewer/app.js",),
            storage_dashboard=True,
            apps_required=True,
        )

    def test_storage_agent_only_change_requires_only_storage_agent(self):
        self.assert_decisions(
            ("apps/storage-monitor/agent/scan_runner.py",),
            storage_agent=True,
            apps_required=True,
        )

    def test_documentation_only_change_sets_no_application_decisions(self):
        self.assert_decisions(
            ("docs/development.md", "README.md"),
            documentation=True,
        )

    def test_shared_root_change_sets_both_application_decisions(self):
        self.assert_decisions(
            ("Makefile",),
            gpu=True,
            storage_dashboard=True,
            storage_agent=True,
            shared=True,
            apps_required=True,
        )

    def test_workflow_change_sets_both_application_decisions(self):
        self.assert_decisions(
            (".github/workflows/ci.yml",),
            gpu=True,
            storage_dashboard=True,
            storage_agent=True,
            workflow=True,
            apps_required=True,
        )

    def test_empty_diff_sets_no_decisions(self):
        self.assert_decisions(())

    def test_rename_path_pairs_classify_old_and_new_paths(self):
        self.assert_decisions(
            (
                "apps/storage-monitor/viewer/old-dashboard.js",
                "apps/storage-monitor/agent/new_agent.py",
            ),
            storage_dashboard=True,
            storage_agent=True,
            apps_required=True,
        )

    def test_paths_file_output_is_deterministic_json(self):
        with tempfile.TemporaryDirectory() as tempdir:
            paths_file = Path(tempdir) / "paths.txt"
            paths_file.write_text(
                "./apps/storage-monitor/viewer/app.js\n"
                "apps/gpu-monitor/backend/main.py\n",
                encoding="utf-8",
            )

            first = self.run_script("--paths-file", str(paths_file))
            second = self.run_script("--paths-file", str(paths_file))

        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(first.stdout, second.stdout)
        self.assertEqual(
            json.loads(first.stdout),
            {
                "apps_required": True,
                "documentation": False,
                "gpu": True,
                "shared": False,
                "storage_agent": False,
                "storage_dashboard": True,
                "workflow": False,
            },
        )

    def test_rejects_absolute_and_parent_traversal_paths(self):
        for bad_path in ("/tmp/file", "../outside", "docs/../Makefile"):
            with self.subTest(bad_path=bad_path):
                with tempfile.TemporaryDirectory() as tempdir:
                    paths_file = Path(tempdir) / "paths.txt"
                    paths_file.write_text(f"{bad_path}\n", encoding="utf-8")
                    result = self.run_script("--paths-file", str(paths_file))
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("invalid repository-relative path", result.stderr)

    def test_writes_lowercase_github_outputs(self):
        with tempfile.TemporaryDirectory() as tempdir:
            paths_file = Path(tempdir) / "paths.txt"
            output_file = Path(tempdir) / "github-output.txt"
            paths_file.write_text("apps/gpu-monitor/backend/main.py\n", encoding="utf-8")

            result = self.run_script(
                "--paths-file",
                str(paths_file),
                env={"GITHUB_OUTPUT": str(output_file)},
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                output_file.read_text(encoding="utf-8").splitlines(),
                [
                    "gpu=true",
                    "storage_dashboard=false",
                    "storage_agent=false",
                    "shared=false",
                    "workflow=false",
                    "documentation=false",
                    "apps_required=true",
                ],
            )

    def test_git_range_reads_changed_and_renamed_paths(self):
        with tempfile.TemporaryDirectory() as tempdir:
            repo = Path(tempdir) / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", "--initial-branch=main"], cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            (repo / "apps/storage-monitor/viewer").mkdir(parents=True)
            old_path = repo / "apps/storage-monitor/viewer/old.js"
            old_path.write_text("old\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            subprocess.run(
                ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", "initial"],
                cwd=repo,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True, text=True, stdout=subprocess.PIPE).stdout.strip()
            new_dir = repo / "apps/storage-monitor/agent"
            new_dir.mkdir(parents=True)
            old_path.rename(new_dir / "renamed.py")
            subprocess.run(["git", "add", "-A"], cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            subprocess.run(
                ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", "rename"],
                cwd=repo,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True, text=True, stdout=subprocess.PIPE).stdout.strip()

            result = subprocess.run(
                ["python3.12", str(SCRIPT), "--base", base, "--head", head],
                cwd=repo,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["storage_dashboard"])
        self.assertTrue(payload["storage_agent"])


if __name__ == "__main__":
    unittest.main()
