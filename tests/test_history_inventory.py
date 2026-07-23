import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "history_inventory.py"


class HistoryInventoryTest(unittest.TestCase):
    def git(self, repo: Path, *args: str, **kwargs) -> str:
        result = subprocess.run(
            ["git", "-C", str(repo), *args],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            **kwargs,
        )
        return result.stdout.strip()

    def run_inventory(self, repo: Path, output: Path, *extra_args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["python3.12", str(SCRIPT), str(repo), str(output), *extra_args],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def read_inventory(self, output: Path) -> dict:
        return json.loads(output.read_text(encoding="utf-8"))

    def commit_file(self, repo: Path, name: str, body: str, author: str) -> None:
        (repo / name).write_text(body, encoding="utf-8")
        self.git(repo, "add", name)
        env = {
            **os.environ,
            "GIT_AUTHOR_NAME": author.split(" <", 1)[0],
            "GIT_AUTHOR_EMAIL": author.split("<", 1)[1].rstrip(">"),
            "GIT_COMMITTER_NAME": author.split(" <", 1)[0],
            "GIT_COMMITTER_EMAIL": author.split("<", 1)[1].rstrip(">"),
        }
        subprocess.run(
            ["git", "-C", str(repo), "commit", "-m", f"add {name}"],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )

    def annotated_tag(self, repo: Path, name: str, message: str) -> None:
        env = {
            **os.environ,
            "GIT_AUTHOR_NAME": "Inventory Test",
            "GIT_AUTHOR_EMAIL": "inventory-test@example.com",
            "GIT_COMMITTER_NAME": "Inventory Test",
            "GIT_COMMITTER_EMAIL": "inventory-test@example.com",
        }
        subprocess.run(
            ["git", "-C", str(repo), "tag", "-a", name, "-m", message],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )

    def test_fixture_creates_annotated_tag_without_global_git_identity(self):
        with tempfile.TemporaryDirectory() as tempdir:
            repo = Path(tempdir) / "source"
            empty_home = Path(tempdir) / "empty-home"
            repo.mkdir()
            empty_home.mkdir()
            self.git(repo, "init", "--initial-branch=main")
            self.commit_file(repo, "tracked.txt", "tracked\n", "Alice <alice@example.com>")

            isolated_git = {
                "HOME": str(empty_home),
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_KEY_0": "user.useConfigOnly",
                "GIT_CONFIG_VALUE_0": "true",
            }
            with mock.patch.dict(os.environ, isolated_git):
                without_fixture_identity = subprocess.run(
                    ["git", "-C", str(repo), "tag", "-a", "without-identity", "-m", "must fail"],
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                self.assertNotEqual(without_fixture_identity.returncode, 0)
                self.annotated_tag(repo, "v1.0.0", "version 1")

            self.assertEqual(self.git(repo, "cat-file", "-t", "refs/tags/v1.0.0"), "tag")

    def test_inventory_includes_refs_commit_counts_tags_and_authors(self):
        with tempfile.TemporaryDirectory() as tempdir:
            repo = Path(tempdir) / "source"
            output = Path(tempdir) / "inventory.json"
            repo.mkdir()
            self.git(repo, "init", "--initial-branch=main")

            self.commit_file(repo, "one.txt", "one\n", "Alice <alice@example.com>")
            self.git(repo, "checkout", "-b", "feature/test")
            self.commit_file(repo, "feature.txt", "feature\n", "Bob <bob@example.com>")
            self.git(repo, "checkout", "main")
            self.commit_file(repo, "two.txt", "two\n", "Bob <bob@example.com>")
            self.annotated_tag(repo, "v0.1.0", "version 0.1.0")

            result = self.run_inventory(repo, output)
            self.assertEqual(result.returncode, 0, result.stderr)

            inventory = self.read_inventory(output)

        self.assertEqual(inventory["refs"]["refs/heads/main"]["commit_count"], 2)
        self.assertIn("refs/heads/feature/test", inventory["refs"])
        self.assertIn("refs/tags/v0.1.0", inventory["refs"])
        self.assertEqual(
            sorted(inventory["authors"]),
            [
                "Alice <alice@example.com>",
                "Bob <bob@example.com>",
            ],
        )

    def test_dirty_repository_is_rejected_by_default(self):
        with tempfile.TemporaryDirectory() as tempdir:
            repo = Path(tempdir) / "source"
            output = Path(tempdir) / "inventory.json"
            repo.mkdir()
            self.git(repo, "init", "--initial-branch=main")
            self.commit_file(repo, "tracked.txt", "tracked\n", "Alice <alice@example.com>")
            (repo / "dirty.txt").write_text("dirty\n", encoding="utf-8")

            result = self.run_inventory(repo, output)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("source repository is dirty", result.stderr)
        self.assertFalse(output.exists())

    def test_allow_dirty_records_status_metadata_without_contents(self):
        with tempfile.TemporaryDirectory() as tempdir:
            repo = Path(tempdir) / "source"
            output = Path(tempdir) / "inventory.json"
            repo.mkdir()
            self.git(repo, "init", "--initial-branch=main")
            self.commit_file(repo, "tracked.txt", "tracked\n", "Alice <alice@example.com>")
            (repo / "dirty.txt").write_text("secret file body must not appear\n", encoding="utf-8")

            result = self.run_inventory(repo, output, "--allow-dirty")
            self.assertEqual(result.returncode, 0, result.stderr)
            raw_inventory = output.read_text(encoding="utf-8")
            inventory = json.loads(raw_inventory)

        self.assertTrue(inventory["status"]["dirty"])
        self.assertIn({"code": "??", "path": "dirty.txt"}, inventory["status"]["entries"])
        self.assertNotIn("secret file body must not appear", raw_inventory)

    def test_annotated_tag_records_target_metadata(self):
        with tempfile.TemporaryDirectory() as tempdir:
            repo = Path(tempdir) / "source"
            output = Path(tempdir) / "inventory.json"
            repo.mkdir()
            self.git(repo, "init", "--initial-branch=main")
            self.commit_file(repo, "tracked.txt", "tracked\n", "Alice <alice@example.com>")
            target = self.git(repo, "rev-parse", "HEAD")
            self.annotated_tag(repo, "v1.0.0", "version 1")

            result = self.run_inventory(repo, output)
            self.assertEqual(result.returncode, 0, result.stderr)
            inventory = self.read_inventory(output)

        tag_inventory = inventory["refs"]["refs/tags/v1.0.0"]
        self.assertEqual(tag_inventory["object_type"], "tag")
        self.assertEqual(
            tag_inventory["annotated_tag_target"],
            {"object_id": target, "object_type": "commit"},
        )

    def test_output_is_byte_identical_across_runs(self):
        with tempfile.TemporaryDirectory() as tempdir:
            repo = Path(tempdir) / "source"
            first = Path(tempdir) / "first.json"
            second = Path(tempdir) / "second.json"
            repo.mkdir()
            self.git(repo, "init", "--initial-branch=main")
            self.commit_file(repo, "b.txt", "b\n", "Bob <bob@example.com>")
            self.git(repo, "checkout", "-b", "feature/a")
            self.commit_file(repo, "a.txt", "a\n", "Alice <alice@example.com>")
            self.git(repo, "checkout", "main")
            self.annotated_tag(repo, "v1.0.0", "version 1")

            first_result = self.run_inventory(repo, first)
            second_result = self.run_inventory(repo, second)
            self.assertEqual(first_result.returncode, 0, first_result.stderr)
            self.assertEqual(second_result.returncode, 0, second_result.stderr)
            first_bytes = first.read_bytes()
            second_bytes = second.read_bytes()

        self.assertEqual(first_bytes, second_bytes)

    def test_bare_mirror_inventory_reports_clean_status_and_preserves_metadata(self):
        with tempfile.TemporaryDirectory() as tempdir:
            source = Path(tempdir) / "source"
            mirror = Path(tempdir) / "source.git"
            output = Path(tempdir) / "inventory.json"
            source.mkdir()
            self.git(source, "init", "--initial-branch=main")
            self.commit_file(source, "one.txt", "one\n", "Alice <alice@example.com>")
            main_oid = self.git(source, "rev-parse", "HEAD")
            self.git(source, "checkout", "-b", "feature/test")
            self.commit_file(source, "feature.txt", "feature\n", "Bob <bob@example.com>")
            feature_oid = self.git(source, "rev-parse", "HEAD")
            self.annotated_tag(source, "v1.0.0", "version 1")
            tag_oid = self.git(source, "rev-parse", "refs/tags/v1.0.0")
            tag_target = self.git(source, "rev-parse", "refs/tags/v1.0.0^{}")
            subprocess.run(
                ["git", "clone", "--mirror", str(source), str(mirror)],
                check=True,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            result = self.run_inventory(mirror, output)
            self.assertEqual(result.returncode, 0, result.stderr)
            inventory = self.read_inventory(output)

        self.assertFalse(inventory["status"]["dirty"])
        self.assertEqual(inventory["status"]["entries"], [])
        self.assertTrue(inventory["status"]["bare"])
        self.assertEqual(inventory["head"], feature_oid)
        self.assertEqual(inventory["refs"]["refs/heads/main"]["object_id"], main_oid)
        self.assertEqual(inventory["refs"]["refs/heads/main"]["commit_count"], 1)
        self.assertEqual(inventory["refs"]["refs/heads/feature/test"]["object_id"], feature_oid)
        self.assertEqual(inventory["refs"]["refs/heads/feature/test"]["commit_count"], 2)
        self.assertEqual(inventory["refs"]["refs/tags/v1.0.0"]["object_id"], tag_oid)
        self.assertEqual(
            inventory["refs"]["refs/tags/v1.0.0"]["annotated_tag_target"],
            {"object_id": tag_target, "object_type": "commit"},
        )
        self.assertEqual(
            sorted(inventory["authors"]),
            [
                "Alice <alice@example.com>",
                "Bob <bob@example.com>",
            ],
        )

    def test_bare_mirror_output_is_byte_identical_across_runs(self):
        with tempfile.TemporaryDirectory() as tempdir:
            source = Path(tempdir) / "source"
            mirror = Path(tempdir) / "source.git"
            first = Path(tempdir) / "first.json"
            second = Path(tempdir) / "second.json"
            source.mkdir()
            self.git(source, "init", "--initial-branch=main")
            self.commit_file(source, "b.txt", "b\n", "Bob <bob@example.com>")
            self.git(source, "checkout", "-b", "feature/a")
            self.commit_file(source, "a.txt", "a\n", "Alice <alice@example.com>")
            self.git(source, "checkout", "main")
            self.annotated_tag(source, "v1.0.0", "version 1")
            subprocess.run(
                ["git", "clone", "--mirror", str(source), str(mirror)],
                check=True,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            first_result = self.run_inventory(mirror, first)
            second_result = self.run_inventory(mirror, second)
            self.assertEqual(first_result.returncode, 0, first_result.stderr)
            self.assertEqual(second_result.returncode, 0, second_result.stderr)
            first_bytes = first.read_bytes()
            second_bytes = second.read_bytes()

        self.assertEqual(first_bytes, second_bytes)

    def test_non_commit_branch_ref_fails_instead_of_empty_metadata(self):
        with tempfile.TemporaryDirectory() as tempdir:
            repo = Path(tempdir) / "source"
            output = Path(tempdir) / "inventory.json"
            repo.mkdir()
            self.git(repo, "init", "--initial-branch=main")
            self.commit_file(repo, "tracked.txt", "tracked\n", "Alice <alice@example.com>")
            blob = subprocess.run(
                ["git", "-C", str(repo), "hash-object", "-w", "--stdin"],
                input="blob-only\n",
                check=True,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            ).stdout.strip()
            (repo / ".git" / "refs" / "heads" / "blobref").write_text(blob + "\n", encoding="utf-8")

            result = self.run_inventory(repo, output)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("non-commit ref cannot provide history metadata", result.stderr)
        self.assertFalse(output.exists())
