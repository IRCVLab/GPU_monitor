import json
import subprocess
import unittest
from unittest.mock import patch

from scripts.authorize_gpu_release import authorize_release, fetch_live_inputs


REPOSITORY = "IRCVLab/GPU_monitor"
FINAL_SHA = "f" * 40
PR_HEAD_SHA = "a" * 40


class AuthorizeGpuReleaseTest(unittest.TestCase):
    def workflow_run(self, **overrides):
        data = {
            "event": "push",
            "head_branch": "main",
            "conclusion": "success",
            "status": "completed",
            "head_sha": FINAL_SHA,
            "head_repository": {"full_name": REPOSITORY},
        }
        data.update(overrides)
        return data

    def pull_request(self, **overrides):
        data = {
            "number": 42,
            "state": "closed",
            "merged_at": "2026-07-23T00:00:00Z",
            "base": {"ref": "main"},
            "head": {"sha": PR_HEAD_SHA},
            "user": {"login": "author"},
        }
        data.update(overrides)
        return data

    def approval(self, **overrides):
        data = {
            "id": 1001,
            "submitted_at": "2026-07-23T00:01:00Z",
            "state": "APPROVED",
            "user": {"login": "reviewer"},
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

    def authorize(self, workflow_run=None, pull_requests=None, reviews=None, check_runs=None):
        return authorize_release(
            workflow_run or self.workflow_run(),
            pull_requests if pull_requests is not None else [self.pull_request()],
            reviews if reviews is not None else [self.approval()],
            check_runs if check_runs is not None else [self.required_check()],
            repository=REPOSITORY,
        )

    def test_authorizes_successful_main_ci_for_reviewed_merged_pr(self):
        authorization = self.authorize()

        self.assertIs(authorization.authorized, True)
        self.assertEqual(authorization.reason, "authorized")
        self.assertEqual(authorization.sha, FINAL_SHA)
        self.assertEqual(authorization.pr_number, 42)
        self.assertEqual(authorization.reviewer, "reviewer")

    def test_rejects_direct_push_without_associated_pr(self):
        authorization = self.authorize(pull_requests=[])

        self.assertIs(authorization.authorized, False)
        self.assertEqual(authorization.reason, "missing_merged_main_pr")

    def test_rejects_pr_targeting_non_main_branch(self):
        authorization = self.authorize(pull_requests=[self.pull_request(base={"ref": "release"})])

        self.assertIs(authorization.authorized, False)
        self.assertEqual(authorization.reason, "missing_merged_main_pr")

    def test_rejects_pending_or_failed_required_check(self):
        for check_run in (
            self.required_check(status="in_progress", conclusion=None),
            self.required_check(conclusion="failure"),
            self.required_check(name="ci/optional"),
            self.required_check(head_sha=PR_HEAD_SHA),
        ):
            with self.subTest(check_run=check_run):
                authorization = self.authorize(check_runs=[check_run])

                self.assertIs(authorization.authorized, False)
                self.assertEqual(authorization.reason, "required_check_not_successful")

    def test_rejects_author_only_approval(self):
        authorization = self.authorize(reviews=[self.approval(user={"login": "author"})])

        self.assertIs(authorization.authorized, False)
        self.assertEqual(authorization.reason, "missing_non_author_approval")

    def test_uses_latest_effective_review_per_reviewer(self):
        stale_approval = self.approval(
            id=1,
            submitted_at="2026-07-23T00:01:00Z",
            user={"login": "reviewer"},
            state="APPROVED",
        )
        later_change_request = self.approval(
            id=2,
            submitted_at="2026-07-23T00:02:00Z",
            user={"login": "reviewer"},
            state="CHANGES_REQUESTED",
        )
        author_approval = self.approval(
            id=3,
            submitted_at="2026-07-23T00:03:00Z",
            user={"login": "author"},
            state="APPROVED",
        )

        authorization = self.authorize(reviews=[stale_approval, later_change_request, author_approval])

        self.assertIs(authorization.authorized, False)
        self.assertEqual(authorization.reason, "missing_non_author_approval")

    def test_fails_closed_on_multiple_ambiguous_merged_prs(self):
        authorization = self.authorize(
            pull_requests=[self.pull_request(number=42), self.pull_request(number=43, user={"login": "other-author"})]
        )

        self.assertIs(authorization.authorized, False)
        self.assertEqual(authorization.reason, "ambiguous_merged_main_pr")


    def test_rejects_malformed_merge_evidence(self):
        malformed_values = ("", False, {}, 0)
        for merged_at in malformed_values:
            with self.subTest(merged_at=merged_at):
                authorization = self.authorize(pull_requests=[self.pull_request(merged_at=merged_at)])

                self.assertIs(authorization.authorized, False)
                self.assertEqual(authorization.reason, "malformed_input")

    def test_accepts_explicit_true_merge_evidence_without_merged_timestamp(self):
        authorization = self.authorize(pull_requests=[self.pull_request(merged_at=None, merged=True)])

        self.assertIs(authorization.authorized, True)
        self.assertEqual(authorization.reason, "authorized")

    def test_live_reviews_include_later_page_changes_requested(self):
        calls = []

        def fake_run(argv, **_kwargs):
            calls.append(argv)
            path = argv[-1]
            if path.endswith(f"/commits/{FINAL_SHA}/pulls"):
                payload = [[self.pull_request()]]
            elif path.endswith(f"/commits/{FINAL_SHA}/check-runs"):
                payload = [{"check_runs": [self.required_check()]}]
            elif path.endswith("/pulls/42/reviews"):
                payload = [
                    [self.approval(id=1, submitted_at="2026-07-23T00:01:00Z", state="APPROVED")],
                    [self.approval(id=2, submitted_at="2026-07-23T00:02:00Z", state="CHANGES_REQUESTED")],
                    [self.approval(id=3, submitted_at="2026-07-23T00:03:00Z", state="DISMISSED")],
                ]
            else:
                self.fail(f"unexpected gh path {path}")
            return subprocess.CompletedProcess(argv, 0, json.dumps(payload), "")

        with patch("scripts.authorize_gpu_release.subprocess.run", fake_run):
            pulls, reviews, checks = fetch_live_inputs(REPOSITORY, self.workflow_run())

        authorization = self.authorize(pull_requests=pulls, reviews=reviews, check_runs=checks)

        self.assertEqual(len(reviews), 3)
        self.assertIs(authorization.authorized, False)
        self.assertEqual(authorization.reason, "missing_non_author_approval")
        self.assertTrue(all("--paginate" in argv and "--slurp" in argv for argv in calls))
        self.assertTrue(all(argv[0:2] == ["gh", "api"] for argv in calls))

    def test_live_pulls_include_second_merged_pr_on_later_page(self):
        def fake_run(argv, **_kwargs):
            path = argv[-1]
            if path.endswith(f"/commits/{FINAL_SHA}/pulls"):
                payload = [[self.pull_request(number=42)], [self.pull_request(number=43, user={"login": "other-author"})]]
            elif path.endswith(f"/commits/{FINAL_SHA}/check-runs"):
                payload = [{"check_runs": [self.required_check()]}]
            else:
                self.fail(f"unexpected gh path {path}")
            return subprocess.CompletedProcess(argv, 0, json.dumps(payload), "")

        with patch("scripts.authorize_gpu_release.subprocess.run", fake_run):
            pulls, reviews, checks = fetch_live_inputs(REPOSITORY, self.workflow_run())

        authorization = self.authorize(pull_requests=pulls, reviews=reviews, check_runs=checks)

        self.assertEqual(len(pulls), 2)
        self.assertEqual(reviews, [])
        self.assertIs(authorization.authorized, False)
        self.assertEqual(authorization.reason, "ambiguous_merged_main_pr")

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

    def test_author_reviewer_identity_is_case_insensitive(self):
        authorization = self.authorize(
            pull_requests=[self.pull_request(user={"login": "Author"})],
            reviews=[self.approval(user={"login": "author"})],
        )

        self.assertIs(authorization.authorized, False)
        self.assertEqual(authorization.reason, "missing_non_author_approval")

    def test_per_reviewer_latest_review_key_is_case_insensitive(self):
        authorization = self.authorize(
            reviews=[
                self.approval(id=1, submitted_at="2026-07-23T00:01:00Z", user={"login": "Reviewer"}),
                self.approval(id=2, submitted_at="2026-07-23T00:02:00Z", user={"login": "reviewer"}, state="DISMISSED"),
            ]
        )

        self.assertIs(authorization.authorized, False)
        self.assertEqual(authorization.reason, "missing_non_author_approval")

    def test_rejects_invalid_final_sha_and_repository_before_live_paths(self):
        for workflow_run, repository in (
            (self.workflow_run(head_sha="F" * 40), REPOSITORY),
            (self.workflow_run(head_sha="f" * 39), REPOSITORY),
            (self.workflow_run(), "IRCVLab"),
            (self.workflow_run(), "IRCVLab/GPU_monitor/extra"),
        ):
            with self.subTest(workflow_run=workflow_run, repository=repository):
                authorization = authorize_release(
                    workflow_run,
                    [self.pull_request()],
                    [self.approval()],
                    [self.required_check()],
                    repository=repository,
                )
                self.assertIs(authorization.authorized, False)
                self.assertEqual(authorization.reason, "malformed_input")

                with patch("scripts.authorize_gpu_release.subprocess.run") as run:
                    with self.assertRaises(ValueError):
                        fetch_live_inputs(repository, workflow_run)
                    run.assert_not_called()


    def test_rejects_arbitrary_or_timezone_naive_merge_timestamps(self):
        for merged_at in ("not-a-timestamp", "2026-07-23T00:00:00", "2026-13-99T99:99:99Z"):
            with self.subTest(merged_at=merged_at):
                authorization = self.authorize(pull_requests=[self.pull_request(merged_at=merged_at)])

                self.assertIs(authorization.authorized, False)
                self.assertEqual(authorization.reason, "malformed_input")

    def test_rejects_single_approval_missing_review_ordering_fields(self):
        for bad_review in (
            self.approval(id=None),
            self.approval(submitted_at=None),
            self.approval(submitted_at="2026-07-23T00:01:00"),
            self.approval(submitted_at="not-a-timestamp"),
        ):
            with self.subTest(bad_review=bad_review):
                authorization = self.authorize(reviews=[bad_review])

                self.assertIs(authorization.authorized, False)
                self.assertEqual(authorization.reason, "malformed_input")

    def test_rejects_review_missing_login_or_state(self):
        for bad_review in (
            self.approval(user={"login": ""}),
            self.approval(user={}),
            self.approval(state=""),
            self.approval(state=None),
        ):
            with self.subTest(bad_review=bad_review):
                authorization = self.authorize(reviews=[bad_review])

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

    def test_rejects_workflow_run_from_different_repository(self):
        authorization = self.authorize(workflow_run=self.workflow_run(head_repository={"full_name": "IRCVLab/fork"}))

        self.assertIs(authorization.authorized, False)
        self.assertEqual(authorization.reason, "workflow_repository_mismatch")


if __name__ == "__main__":
    unittest.main()
