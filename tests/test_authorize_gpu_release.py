import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.authorize_gpu_release import authorize_release, fetch_live_evidence, main


REPOSITORY = "IRCVLab/GPU_monitor"
FINAL_SHA = "f" * 40


class AuthorizeGpuReleaseTest(unittest.TestCase):
    def workflow_run(self, **overrides):
        data = {
            "name": "ci",
            "event": "push",
            "head_branch": "main",
            "conclusion": "success",
            "status": "completed",
            "head_sha": FINAL_SHA,
            "head_repository": {"full_name": REPOSITORY},
            "path": ".github/workflows/ci.yml",
        }
        data.update(overrides)
        return data

    def required_check(self, **overrides):
        data = {
            "id": 1001,
            "name": "ci/required",
            "head_sha": FINAL_SHA,
            "status": "completed",
            "conclusion": "success",
            "completed_at": "2026-07-23T00:04:00Z",
        }
        data.update(overrides)
        return data

    def authorize(self, workflow_run=None, check_runs=None):
        return authorize_release(
            workflow_run or self.workflow_run(),
            check_runs if check_runs is not None else [self.required_check()],
            current_main_sha=FINAL_SHA,
            repository=REPOSITORY,
        )

    def test_authorizes_successful_same_repository_main_push(self):
        authorization = self.authorize()

        self.assertIs(authorization.authorized, True)
        self.assertEqual(authorization.sha, FINAL_SHA)
        self.assertEqual(authorization.reason, "authorized")

    def test_rejects_untrusted_workflow_provenance(self):
        cases = (
            ({"name": "other"}, "workflow_name_mismatch"),
            ({"event": "pull_request"}, "workflow_event_not_push"),
            ({"head_branch": "feature"}, "workflow_branch_not_main"),
            ({"status": "in_progress"}, "workflow_status_not_completed"),
            ({"conclusion": "failure"}, "workflow_conclusion_not_success"),
            ({"head_repository": {"full_name": "IRCVLab/fork"}}, "workflow_repository_mismatch"),
            ({"path": ".github/workflows/other-ci.yml"}, "workflow_path_mismatch"),
            ({"path": "ci.yml"}, "workflow_path_mismatch"),
        )
        for overrides, reason in cases:
            with self.subTest(overrides=overrides):
                result = self.authorize(self.workflow_run(**overrides))
                self.assertIs(result.authorized, False)
                self.assertEqual(result.reason, reason)

    def test_rejects_workflow_sha_that_is_no_longer_current_main(self):
        result = authorize_release(
            self.workflow_run(),
            [self.required_check()],
            current_main_sha="e" * 40,
            repository=REPOSITORY,
        )

        self.assertIs(result.authorized, False)
        self.assertEqual(result.reason, "workflow_sha_not_current_main")

    def test_rejects_pending_or_failed_required_check(self):
        for check_run in (
            self.required_check(status="in_progress", conclusion=None),
            self.required_check(conclusion="failure"),
            self.required_check(name="ci/optional"),
            self.required_check(head_sha="a" * 40),
        ):
            with self.subTest(check_run=check_run):
                authorization = self.authorize(check_runs=[check_run])

                self.assertIs(authorization.authorized, False)
                self.assertEqual(authorization.reason, "required_check_not_successful")

    def test_duplicate_required_checks_use_latest_completed_run(self):
        old_success = self.required_check(id=1, completed_at="2026-07-23T00:01:00Z", conclusion="success")
        new_failure = self.required_check(id=2, completed_at="2026-07-23T00:02:00Z", conclusion="failure")

        authorization = self.authorize(check_runs=[old_success, new_failure])

        self.assertIs(authorization.authorized, False)
        self.assertEqual(authorization.reason, "required_check_not_successful")

    def test_duplicate_required_checks_with_malformed_order_fail_closed(self):
        missing_completed_at = self.required_check(id=1, completed_at=None)
        valid_success = self.required_check(id=2, completed_at="2026-07-23T00:02:00Z")

        authorization = self.authorize(check_runs=[missing_completed_at, valid_success])

        self.assertIs(authorization.authorized, False)
        self.assertEqual(authorization.reason, "malformed_input")

    def test_single_matching_required_check_requires_id_and_completed_at(self):
        for bad_check in (
            self.required_check(id=None),
            self.required_check(completed_at=None),
            self.required_check(completed_at="2026-07-23T00:04:00"),
            self.required_check(completed_at="not-a-timestamp"),
        ):
            with self.subTest(bad_check=bad_check):
                authorization = self.authorize(check_runs=[bad_check])

                self.assertIs(authorization.authorized, False)
                self.assertEqual(authorization.reason, "malformed_input")

    def test_duplicate_required_checks_compare_parsed_timestamps_then_id(self):
        earlier_text_later_instant = self.required_check(
            id=1,
            completed_at="2026-07-23T00:30:00+09:00",
            conclusion="failure",
        )
        later_instant_lower_text = self.required_check(
            id=2,
            completed_at="2026-07-22T16:00:00Z",
            conclusion="success",
        )

        authorization = self.authorize(check_runs=[earlier_text_later_instant, later_instant_lower_text])

        self.assertIs(authorization.authorized, True)
        self.assertEqual(authorization.reason, "authorized")

    def test_duplicate_required_checks_reject_invalid_completed_at(self):
        authorization = self.authorize(
            check_runs=[
                self.required_check(id=1, completed_at="2026-07-23T00:01:00Z"),
                self.required_check(id=2, completed_at="not-a-timestamp"),
            ]
        )

        self.assertIs(authorization.authorized, False)
        self.assertEqual(authorization.reason, "malformed_input")

    def test_rejects_invalid_sha_and_repository_before_live_paths(self):
        for workflow_run, repository in (
            (self.workflow_run(head_sha="F" * 40), REPOSITORY),
            (self.workflow_run(head_sha="f" * 39), REPOSITORY),
            (self.workflow_run(), "IRCVLab"),
            (self.workflow_run(), "IRCVLab/GPU_monitor/extra"),
        ):
            with self.subTest(workflow_run=workflow_run, repository=repository):
                authorization = authorize_release(
                    workflow_run,
                    [self.required_check()],
                    current_main_sha=FINAL_SHA,
                    repository=repository,
                )
                self.assertIs(authorization.authorized, False)
                self.assertEqual(authorization.reason, "malformed_input")

                with patch("scripts.authorize_gpu_release.subprocess.run") as run:
                    with self.assertRaises(ValueError):
                        fetch_live_evidence(repository, workflow_run)
                    run.assert_not_called()

    def test_live_fetch_reads_paginated_checks_and_current_main(self):
        calls = []

        def fake_run(argv, **_kwargs):
            calls.append(argv)
            path = argv[-1]
            if path.endswith(f"/commits/{FINAL_SHA}/check-runs"):
                payload = [{"check_runs": [self.required_check()]}]
            elif path.endswith("/git/ref/heads/main"):
                payload = {"object": {"sha": FINAL_SHA}}
            else:
                self.fail(f"unexpected gh path {path}")
            return subprocess.CompletedProcess(argv, 0, json.dumps(payload), "")

        with patch("scripts.authorize_gpu_release.subprocess.run", fake_run):
            checks, current_main_sha = fetch_live_evidence(REPOSITORY, self.workflow_run())

        self.assertEqual(checks, [self.required_check()])
        self.assertEqual(current_main_sha, FINAL_SHA)
        self.assertEqual(len(calls), 2)
        self.assertIn("--paginate", calls[0])
        self.assertIn("--slurp", calls[0])
        self.assertNotIn("--paginate", calls[1])
        self.assertNotIn("--slurp", calls[1])

    def test_non_live_cli_requires_checks_file_and_current_main_sha(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workflow_path = Path(tmpdir) / "workflow.json"
            workflow_path.write_text(json.dumps({"workflow_run": self.workflow_run()}), encoding="utf-8")
            stdout = io.StringIO()

            with contextlib.redirect_stdout(stdout):
                exit_code = main(["--repository", REPOSITORY, "--workflow-run-file", str(workflow_path)])

        self.assertEqual(exit_code, 1)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["authorized"], False)
        self.assertTrue(payload["reason"].startswith("input_error:"))

    def test_non_live_cli_authorizes_from_checks_file_and_current_main_sha(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workflow_path = Path(tmpdir) / "workflow.json"
            checks_path = Path(tmpdir) / "checks.json"
            workflow_path.write_text(json.dumps({"workflow_run": self.workflow_run()}), encoding="utf-8")
            checks_path.write_text(json.dumps({"check_runs": [self.required_check()]}), encoding="utf-8")
            stdout = io.StringIO()

            with contextlib.redirect_stdout(stdout):
                exit_code = main(
                    [
                        "--repository",
                        REPOSITORY,
                        "--workflow-run-file",
                        str(workflow_path),
                        "--checks-file",
                        str(checks_path),
                        "--current-main-sha",
                        FINAL_SHA,
                    ]
                )

        self.assertEqual(exit_code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload, {"authorized": True, "reason": "authorized", "sha": FINAL_SHA})


if __name__ == "__main__":
    unittest.main()
