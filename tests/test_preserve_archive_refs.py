import importlib.util
import io
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "preserve_archive_refs.py"
EXPECTED = {
    "archive/gpu-dev/codex/task5-failure-veil": "7aa30626cf0ceda3b1d5aada4c19d834ecd4b834",
    "archive/gpu-dev/develop": "cf70ad07bda5b9b2efb7fb3b06869cc080f95c9a",
    "archive/gpu-dev/feature/apple-dashboard-refinement": "ca9ec6614458a6049041dca3c3b874ae4f34bf6f",
    "archive/gpu-dev/feature/compact-gpu-dashboard": "64c4b838d6e1293daf52ab0039084a2b9f84bc59",
    "archive/gpu-dev/main": "c50f9d2aa9465d742c870ba47793589807832efa",
    "archive/gpu-live/main": "f2ea62f5ba4dc6a791bf0faf3fee4153e83462ce",
    "archive/gpu-live/old": "b18c78fd7adda3c6065df32d183524f281fa94fe",
    "archive/storage/checkpoint/ai-advisor-workspace-20260717": "0685b5f2161041ccce7025a8e5d2b4dd140d6590",
    "archive/storage/feature/multiserver-storage-dashboard": "0d7e1dcf2cfd9cfe819851e37384e8bb80930365",
    "archive/storage/master": "ea59cb591fbf408c583bdfad570726d8787cc25a",
}


def load_module():
    spec = importlib.util.spec_from_file_location("preserve_archive_refs", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PreserveArchiveRefsTests(unittest.TestCase):
    def test_cli_script_exists(self):
        self.assertTrue(SCRIPT.is_file())

    def test_maps_all_known_branches_to_collision_free_archive_tags(self):
        module = load_module()
        actual = {item.branch: item.oid for item in module.ARCHIVE_REFS}
        self.assertEqual(actual, EXPECTED)
        tags = [item.tag for item in module.ARCHIVE_REFS]
        self.assertEqual(len(tags), len(set(tags)))
        self.assertEqual(
            tags,
            [f"archive/branch/{branch.removeprefix('archive/')}" for branch in EXPECTED],
        )
        module.validate_archive_refs(module.ARCHIVE_REFS)

    def test_ref_validation_rejects_directory_file_conflicts(self):
        module = load_module()
        refs = (
            module.ArchiveRef("archive/example", "1" * 40, "archive/branch/example"),
            module.ArchiveRef(
                "archive/example/child",
                "2" * 40,
                "archive/branch/example/child",
            ),
        )
        with self.assertRaisesRegex(module.SafetyError, "directory/file conflict"):
            module.validate_archive_refs(refs)

    def complete_remote_snapshot(self, module):
        lines = []
        for index, item in enumerate(module.ARCHIVE_REFS, start=1):
            lines.append(f"{item.oid}\trefs/heads/{item.branch}")
            lines.append(f"{index:040x}\trefs/tags/{item.tag}")
            lines.append(f"{item.oid}\trefs/tags/{item.tag}^{{}}")
        return module.parse_ls_remote("\n".join(lines) + "\n")

    def test_verification_requires_unmoved_branches_and_annotated_peeled_tags(self):
        module = load_module()
        snapshot = self.complete_remote_snapshot(module)
        module.verify_snapshot(snapshot)

        first = module.ARCHIVE_REFS[0]
        cases = {
            "moved branch": {
                **snapshot,
                f"refs/heads/{first.branch}": "0" * 40,
            },
            "missing tag": {
                key: value
                for key, value in snapshot.items()
                if key != f"refs/tags/{first.tag}"
            },
            "lightweight tag": {
                key: value
                for key, value in snapshot.items()
                if key != f"refs/tags/{first.tag}^{{}}"
            },
            "mismatched peeled tag": {
                **snapshot,
                f"refs/tags/{first.tag}^{{}}": "f" * 40,
            },
        }
        for label, invalid in cases.items():
            with self.subTest(label=label):
                with self.assertRaises(module.SafetyError):
                    module.verify_snapshot(invalid)

    def test_local_annotated_tag_is_reproducible_without_global_identity(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            subprocess.run(["git", "init", "-q", repository], check=True)
            commit_environment = {
                **os.environ,
                "GIT_AUTHOR_NAME": "Fixture Author",
                "GIT_AUTHOR_EMAIL": "fixture-author@example.invalid",
                "GIT_AUTHOR_DATE": "2026-07-23T00:00:00+00:00",
                "GIT_COMMITTER_NAME": "Fixture Committer",
                "GIT_COMMITTER_EMAIL": "fixture-committer@example.invalid",
                "GIT_COMMITTER_DATE": "2026-07-23T00:00:00+00:00",
            }
            subprocess.run(
                ["git", "-C", repository, "commit", "--allow-empty", "-m", "fixture"],
                check=True,
                env=commit_environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            oid = subprocess.run(
                ["git", "-C", repository, "rev-parse", "HEAD"],
                check=True,
                stdout=subprocess.PIPE,
                text=True,
            ).stdout.strip()
            item = module.ArchiveRef(
                "archive/example/main",
                oid,
                "archive/branch/example/main",
            )
            previous_directory = Path.cwd()
            try:
                os.chdir(repository)
                first_tag_oid = module.create_local_annotated_tag(item)
                object_type = module.run_git(
                    ["cat-file", "-t", f"refs/tags/{item.tag}"]
                ).stdout.strip()
                peeled = module.run_git(
                    ["rev-parse", f"refs/tags/{item.tag}^{{}}"]
                ).stdout.strip()
                module.run_git(["update-ref", "-d", f"refs/tags/{item.tag}"])
                second_tag_oid = module.create_local_annotated_tag(item)
            finally:
                os.chdir(previous_directory)

            self.assertEqual(object_type, "tag")
            self.assertEqual(peeled, oid)
            self.assertEqual(first_tag_oid, second_tag_oid)

    def test_rejects_preexisting_local_tag_with_different_metadata(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            subprocess.run(["git", "init", "-q", repository], check=True)
            environment = {
                **os.environ,
                "GIT_AUTHOR_NAME": "Fixture Author",
                "GIT_AUTHOR_EMAIL": "fixture-author@example.invalid",
                "GIT_AUTHOR_DATE": "2026-07-23T00:00:00+00:00",
                "GIT_COMMITTER_NAME": "Fixture Committer",
                "GIT_COMMITTER_EMAIL": "fixture-committer@example.invalid",
                "GIT_COMMITTER_DATE": "2026-07-23T00:00:00+00:00",
            }
            subprocess.run(
                ["git", "-C", repository, "commit", "--allow-empty", "-m", "fixture"],
                check=True,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            oid = subprocess.run(
                ["git", "-C", repository, "rev-parse", "HEAD"],
                check=True,
                stdout=subprocess.PIPE,
                text=True,
            ).stdout.strip()
            item = module.ArchiveRef(
                "archive/example/main",
                oid,
                "archive/branch/example/main",
            )
            subprocess.run(
                [
                    "git",
                    "-C",
                    repository,
                    "tag",
                    "-a",
                    item.tag,
                    oid,
                    "-m",
                    "different metadata",
                    "--no-sign",
                ],
                check=True,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            previous_directory = Path.cwd()
            try:
                os.chdir(repository)
                with self.assertRaisesRegex(
                    module.SafetyError, "non-deterministic local tag"
                ):
                    module.create_local_annotated_tag(item)
            finally:
                os.chdir(previous_directory)

    def test_tag_push_is_atomic_object_pinned_and_expect_absent(self):
        module = load_module()
        tag_objects = {
            item.tag: f"{index:040x}"
            for index, item in enumerate(module.ARCHIVE_REFS, start=1)
        }
        command = module.build_tag_push_command("origin", tag_objects)
        self.assertEqual(command[:2], ["push", "--atomic"])
        self.assertEqual(command[-11], "origin")
        for item in module.ARCHIVE_REFS:
            tag_ref = f"refs/tags/{item.tag}"
            self.assertIn(f"--force-with-lease={tag_ref}:", command)
            self.assertIn(f"{tag_objects[item.tag]}:{tag_ref}", command)
            self.assertNotIn(tag_ref, command)

    def test_object_pinned_expect_absent_tag_push_works_against_bare_remote(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            repository = root / "repository"
            remote = root / "origin.git"
            subprocess.run(["git", "init", "-q", repository], check=True)
            subprocess.run(["git", "init", "--bare", "-q", remote], check=True)
            subprocess.run(
                ["git", "-C", repository, "remote", "add", "origin", str(remote)],
                check=True,
            )
            environment = {
                **os.environ,
                "GIT_AUTHOR_NAME": "Fixture Author",
                "GIT_AUTHOR_EMAIL": "fixture-author@example.invalid",
                "GIT_AUTHOR_DATE": "2026-07-23T00:00:00+00:00",
                "GIT_COMMITTER_NAME": "Fixture Committer",
                "GIT_COMMITTER_EMAIL": "fixture-committer@example.invalid",
                "GIT_COMMITTER_DATE": "2026-07-23T00:00:00+00:00",
            }
            subprocess.run(
                ["git", "-C", repository, "commit", "--allow-empty", "-m", "fixture"],
                check=True,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            subprocess.run(
                [
                    "git",
                    "-C",
                    repository,
                    "tag",
                    "-a",
                    "scratch",
                    "-m",
                    "scratch",
                    "--no-sign",
                ],
                check=True,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            tag_object_oid = subprocess.run(
                ["git", "-C", repository, "rev-parse", "refs/tags/scratch"],
                check=True,
                stdout=subprocess.PIPE,
                text=True,
            ).stdout.strip()
            item = module.ARCHIVE_REFS[0]
            command = module.build_tag_push_command(
                "origin", {item.tag: tag_object_oid}
            )
            subprocess.run(
                ["git", "-C", repository, *command],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            remote_refs = subprocess.run(
                ["git", "ls-remote", "--tags", str(remote)],
                check=True,
                stdout=subprocess.PIPE,
                text=True,
            ).stdout

            self.assertIn(f"refs/tags/{item.tag}\n", remote_refs)
            self.assertIn(f"refs/tags/{item.tag}^{{}}\n", remote_refs)

    def test_full_create_verify_and_guarded_delete_flow_against_bare_remote(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            repository = root / "repository"
            remote = root / "origin.git"
            subprocess.run(["git", "init", "-q", repository], check=True)
            subprocess.run(["git", "init", "--bare", "-q", remote], check=True)
            subprocess.run(
                ["git", "-C", repository, "remote", "add", "origin", str(remote)],
                check=True,
            )
            environment = {
                **os.environ,
                "GIT_AUTHOR_NAME": "Fixture Author",
                "GIT_AUTHOR_EMAIL": "fixture-author@example.invalid",
                "GIT_AUTHOR_DATE": "2026-07-23T00:00:00+00:00",
                "GIT_COMMITTER_NAME": "Fixture Committer",
                "GIT_COMMITTER_EMAIL": "fixture-committer@example.invalid",
                "GIT_COMMITTER_DATE": "2026-07-23T00:00:00+00:00",
            }
            subprocess.run(
                ["git", "-C", repository, "commit", "--allow-empty", "-m", "fixture"],
                check=True,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            oid = subprocess.run(
                ["git", "-C", repository, "rev-parse", "HEAD"],
                check=True,
                stdout=subprocess.PIPE,
                text=True,
            ).stdout.strip()
            item = module.ArchiveRef(
                "archive/example/main",
                oid,
                "archive/branch/example/main",
            )
            subprocess.run(
                [
                    "git",
                    "-C",
                    repository,
                    "push",
                    "origin",
                    f"{oid}:refs/heads/{item.branch}",
                ],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

            previous_directory = Path.cwd()
            try:
                os.chdir(repository)
                with (
                    mock.patch.object(module, "ARCHIVE_REFS", (item,)),
                    redirect_stdout(io.StringIO()),
                ):
                    self.assertEqual(
                        module.main(["--remote", "origin", "--create-tags"]),
                        0,
                    )
                    self.assertEqual(
                        module.main(["--remote", "origin", "--verify"]),
                        0,
                    )
                    self.assertEqual(
                        module.main(
                            [
                                "--remote",
                                "origin",
                                "--delete-verified-branches",
                            ]
                        ),
                        0,
                    )
            finally:
                os.chdir(previous_directory)

            remote_refs = subprocess.run(
                ["git", "ls-remote", "--heads", "--tags", str(remote)],
                check=True,
                stdout=subprocess.PIPE,
                text=True,
            ).stdout
            self.assertNotIn(f"refs/heads/{item.branch}\n", remote_refs)
            self.assertIn(f"refs/tags/{item.tag}\n", remote_refs)
            self.assertIn(f"{oid}\trefs/tags/{item.tag}^{{}}\n", remote_refs)

    def test_deletion_command_uses_explicit_per_ref_force_with_lease(self):
        module = load_module()
        command = module.build_delete_command("origin")
        self.assertEqual(command[:2], ["push", "--atomic"])
        self.assertEqual(command[-11], "origin")
        for item in module.ARCHIVE_REFS:
            branch_ref = f"refs/heads/{item.branch}"
            self.assertIn(f"--force-with-lease={branch_ref}:{item.oid}", command)
            self.assertIn(f":{branch_ref}", command)
        self.assertNotIn("--force", command)

    def test_only_explicit_delete_mode_is_destructive(self):
        module = load_module()
        snapshot = self.complete_remote_snapshot(module)
        for mode in ("--dry-run", "--create-tags", "--verify"):
            with self.subTest(mode=mode):
                with (
                    mock.patch.object(module, "validate_archive_refs"),
                    mock.patch.object(
                        module, "read_remote_snapshot", return_value=snapshot
                    ),
                    mock.patch.object(
                        module, "create_and_push_tags", return_value=snapshot
                    ) as create_tags,
                    mock.patch.object(
                        module, "delete_verified_branches", return_value=snapshot
                    ) as delete_branches,
                    mock.patch.object(
                        module, "report", return_value={"schema": 1}
                    ),
                    redirect_stdout(io.StringIO()),
                ):
                    self.assertEqual(module.main(["--remote", "origin", mode]), 0)
                delete_branches.assert_not_called()
                if mode == "--create-tags":
                    create_tags.assert_called_once_with("origin", snapshot)
                else:
                    create_tags.assert_not_called()

        with (
            mock.patch.object(module, "validate_archive_refs"),
            mock.patch.object(module, "read_remote_snapshot", return_value=snapshot),
            mock.patch.object(
                module, "delete_verified_branches", return_value=snapshot
            ) as delete_branches,
            mock.patch.object(module, "report", return_value={"schema": 1}),
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(
                module.main(
                    ["--remote", "origin", "--delete-verified-branches"]
                ),
                0,
            )
        delete_branches.assert_called_once_with("origin", snapshot)

    def test_moved_branch_refuses_tag_creation_before_any_git_write(self):
        module = load_module()
        snapshot = self.complete_remote_snapshot(module)
        first = module.ARCHIVE_REFS[0]
        snapshot[f"refs/heads/{first.branch}"] = "0" * 40
        with mock.patch.object(module, "run_git") as run_git:
            with self.assertRaises(module.SafetyError):
                module.create_and_push_tags("origin", snapshot)
        run_git.assert_not_called()


if __name__ == "__main__":
    unittest.main()
