#!/usr/bin/env python3.12
"""Fail-closed GPU release authorization from GitHub provenance evidence."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Authorization:
    authorized: bool
    sha: str
    pr_number: int | None
    reason: str
    reviewer: str | None


def denied(reason: str, sha: str = "", pr_number: int | None = None, reviewer: str | None = None) -> Authorization:
    return Authorization(False, sha, pr_number, reason, reviewer)


def nested(mapping: dict[str, object], *keys: str) -> object:
    current: object = mapping
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def string_field(mapping: dict[str, object], *keys: str) -> str | None:
    value = nested(mapping, *keys)
    return value if isinstance(value, str) and value else None


def int_field(mapping: dict[str, object], *keys: str) -> int | None:
    value = nested(mapping, *keys)
    if isinstance(value, bool):
        return None
    return value if isinstance(value, int) else None


def normalize_login(value: object) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None


def is_merged_main_pr(pull_request: dict[str, object]) -> bool:
    return (
        string_field(pull_request, "base", "ref") == "main"
        and (pull_request.get("merged_at") is not None or pull_request.get("merged") is True)
    )


def review_order(review: dict[str, object]) -> tuple[str, int]:
    submitted_at = string_field(review, "submitted_at") or string_field(review, "submittedAt") or ""
    review_id = int_field(review, "id") or 0
    return submitted_at, review_id


def latest_effective_reviews(reviews: list[dict[str, object]]) -> dict[str, dict[str, object]]:
    latest: dict[str, dict[str, object]] = {}
    for review in reviews:
        login = normalize_login(nested(review, "user", "login")) or normalize_login(review.get("author"))
        state = string_field(review, "state")
        if login is None or state is None:
            continue
        previous = latest.get(login)
        if previous is None or review_order(review) >= review_order(previous):
            latest[login] = review
    return latest


def successful_required_check(check_runs: list[dict[str, object]], *, sha: str, required_check: str) -> bool:
    for check_run in check_runs:
        if string_field(check_run, "name") != required_check:
            continue
        if string_field(check_run, "head_sha") != sha:
            continue
        if string_field(check_run, "status") != "completed":
            continue
        if string_field(check_run, "conclusion") == "success":
            return True
    return False


def authorize_release(
    workflow_run: dict[str, object],
    pull_requests: list[dict[str, object]],
    reviews: list[dict[str, object]],
    check_runs: list[dict[str, object]],
    *,
    repository: str,
    required_check: str = "ci/required",
) -> Authorization:
    try:
        if not isinstance(workflow_run, dict):
            return denied("malformed_input")
        if not isinstance(pull_requests, list) or not isinstance(reviews, list) or not isinstance(check_runs, list):
            return denied("malformed_input")
        if not all(isinstance(item, dict) for item in pull_requests + reviews + check_runs):
            return denied("malformed_input")

        sha = string_field(workflow_run, "head_sha") or ""
        if string_field(workflow_run, "event") != "push":
            return denied("workflow_event_not_push", sha)
        if string_field(workflow_run, "head_branch") != "main":
            return denied("workflow_branch_not_main", sha)
        if string_field(workflow_run, "conclusion") != "success":
            return denied("workflow_conclusion_not_success", sha)
        if string_field(workflow_run, "status") != "completed":
            return denied("workflow_status_not_completed", sha)
        if string_field(workflow_run, "head_repository", "full_name") != repository:
            return denied("workflow_repository_mismatch", sha)
        if not sha:
            return denied("malformed_input")

        merged_main_prs = [pull_request for pull_request in pull_requests if is_merged_main_pr(pull_request)]
        if not merged_main_prs:
            return denied("missing_merged_main_pr", sha)
        if len(merged_main_prs) != 1:
            return denied("ambiguous_merged_main_pr", sha)

        pull_request = merged_main_prs[0]
        pr_number = int_field(pull_request, "number")
        if pr_number is None:
            return denied("malformed_input", sha)
        author = normalize_login(nested(pull_request, "user", "login"))
        if author is None:
            return denied("malformed_input", sha, pr_number)

        reviewer: str | None = None
        effective_reviews = latest_effective_reviews(reviews)
        for login in sorted(effective_reviews):
            review = effective_reviews[login]
            state = (string_field(review, "state") or "").upper()
            if state == "APPROVED" and login != author:
                reviewer = login
                break
        if reviewer is None:
            return denied("missing_non_author_approval", sha, pr_number)

        if not successful_required_check(check_runs, sha=sha, required_check=required_check):
            return denied("required_check_not_successful", sha, pr_number, reviewer)

        return Authorization(True, sha, pr_number, "authorized", reviewer)
    except Exception:
        return denied("malformed_input")


def read_json_file(path: str) -> Any:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def list_payload(value: Any, key: str) -> list[dict[str, object]]:
    if isinstance(value, dict) and isinstance(value.get(key), list):
        value = value[key]
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ValueError(f"{key} payload must be a list of objects")
    return value


def workflow_payload(value: Any) -> dict[str, object]:
    if isinstance(value, dict) and isinstance(value.get("workflow_run"), dict):
        value = value["workflow_run"]
    if not isinstance(value, dict):
        raise ValueError("workflow run payload must be an object")
    return value


def gh_api(path: str) -> Any:
    result = subprocess.run(
        ["gh", "api", "-H", "Accept: application/vnd.github+json", path],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        raise RuntimeError(f"gh api {path} failed: {result.stderr.strip()}")
    return json.loads(result.stdout)


def fetch_live_inputs(repository: str, workflow_run: dict[str, object]) -> tuple[list[dict[str, object]], list[dict[str, object]], list[dict[str, object]]]:
    sha = string_field(workflow_run, "head_sha")
    if sha is None:
        raise ValueError("workflow run is missing head_sha")
    pulls = list_payload(gh_api(f"/repos/{repository}/commits/{sha}/pulls"), "pulls")
    checks = list_payload(gh_api(f"/repos/{repository}/commits/{sha}/check-runs"), "check_runs")
    merged_main_prs = [pull_request for pull_request in pulls if is_merged_main_pr(pull_request)]
    reviews: list[dict[str, object]] = []
    if len(merged_main_prs) == 1:
        pr_number = int_field(merged_main_prs[0], "number")
        if pr_number is not None:
            reviews = list_payload(gh_api(f"/repos/{repository}/pulls/{pr_number}/reviews"), "reviews")
    return pulls, reviews, checks


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--workflow-run-file", required=True)
    parser.add_argument("--pulls-file")
    parser.add_argument("--reviews-file")
    parser.add_argument("--checks-file")
    parser.add_argument("--required-check", default="ci/required")
    parser.add_argument("--live", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        workflow_run = workflow_payload(read_json_file(args.workflow_run_file))
        if args.live:
            pull_requests, reviews, check_runs = fetch_live_inputs(args.repository, workflow_run)
        else:
            if args.pulls_file is None or args.reviews_file is None or args.checks_file is None:
                raise ValueError("--pulls-file, --reviews-file, and --checks-file are required without --live")
            pull_requests = list_payload(read_json_file(args.pulls_file), "pulls")
            reviews = list_payload(read_json_file(args.reviews_file), "reviews")
            check_runs = list_payload(read_json_file(args.checks_file), "check_runs")
        authorization = authorize_release(
            workflow_run,
            pull_requests,
            reviews,
            check_runs,
            repository=args.repository,
            required_check=args.required_check,
        )
    except Exception as error:
        authorization = denied(f"input_error:{type(error).__name__}")

    print(json.dumps(asdict(authorization), sort_keys=True))
    return 0 if authorization.authorized else 1


if __name__ == "__main__":
    raise SystemExit(main())
