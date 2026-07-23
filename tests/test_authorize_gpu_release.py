import unittest

from scripts.authorize_gpu_release import authorize_release


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
            "name": "ci/required",
            "head_sha": FINAL_SHA,
            "status": "completed",
            "conclusion": "success",
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

    def test_rejects_workflow_run_from_different_repository(self):
        authorization = self.authorize(workflow_run=self.workflow_run(head_repository={"full_name": "IRCVLab/fork"}))

        self.assertIs(authorization.authorized, False)
        self.assertEqual(authorization.reason, "workflow_repository_mismatch")


if __name__ == "__main__":
    unittest.main()
