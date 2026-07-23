#!/usr/bin/env python3.12
"""Fail-closed GPU release authorization from GitHub provenance evidence."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

SHA_RE = re.compile(r"^[0-9a-f]{40}$")
REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


@dataclass(frozen=True)
class Authorization:
    authorized: bool
    sha: str
    pr_number: int | None
    reason: str
    reviewer: str | None


class MalformedInput(ValueError):
    pass


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


def validate_repository(repository: str) -> None:
    if not isinstance(repository, str) or REPOSITORY_RE.fullmatch(repository) is None:
        raise MalformedInput("repository must be OWNER/REPO")


def validate_sha(sha: str) -> None:
    if SHA_RE.fullmatch(sha) is None:
        raise MalformedInput("sha must be exactly 40 lowercase hex characters")


def normalize_login(value: object) -> str | None:
    if isinstance(value, str) and value:
        return value.casefold()
    return None


def display_login(value: object) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None


def has_valid_merge_evidence(pull_request: dict[str, object]) -> bool:
    if "merged_at" in pull_request:
        merged_at = pull_request.get("merged_at")
        if isinstance(merged_at, str) and merged_at:
            return True
        if merged_at is not None:
            raise MalformedInput("merged_at must be a non-empty string timestamp")
    merged = pull_request.get("merged")
    if merged is True:
        return True
    if "merged" in pull_request and merged not in (None, False):
        raise MalformedInput("merged must be boolean true when used as merge evidence")
    return False


def is_merged_main_pr(pull_request: dict[str, object]) -> bool:
    return string_field(pull_request, "base", "ref") == "main" and has_valid_merge_evidence(pull_request)


def review_order(review: dict[str, object]) -> tuple[str, int]:
    submitted_at = string_field(review, "submitted_at") or string_field(review, "submittedAt")
    review_id = int_field(review, "id")
    if submitted_at is None or review_id is None:
        raise MalformedInput("review ordering fields must be present")
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


def matching_required_checks(check_runs: list[dict[str, object]], *, sha: str, required_check: str) -> list[dict[str, object]]:
    return [
        check_run
        for check_run in check_runs
        if string_field(check_run, "name") == required_check and string_field(check_run, "head_sha") == sha
    ]


def latest_required_check(check_runs: list[dict[str, object]], *, sha: str, required_check: str) -> dict[str, object] | None:
    matches = matching_required_checks(check_runs, sha=sha, required_check=required_check)
    if not matches:
        return None
    if len(matches) == 1:
        return matches[0]

    ordered: list[tuple[str, int, dict[str, object]]] = []
    seen_keys: set[tuple[str, int]] = set()
    for check_run in matches:
        completed_at = string_field(check_run, "completed_at")
        check_id = int_field(check_run, "id")
        if completed_at is None or check_id is None:
            raise MalformedInput("duplicate required checks must have completed_at and numeric id")
        key = (completed_at, check_id)
        if key in seen_keys:
            raise MalformedInput("duplicate required checks have ambiguous ordering")
        seen_keys.add(key)
        ordered.append((completed_at, check_id, check_run))
    return max(ordered, key=lambda item: (item[0], item[1]))[2]


def required_check_is_successful(check_runs: list[dict[str, object]], *, sha: str, required_check: str) -> bool:
    check_run = latest_required_check(check_runs, sha=sha, required_check=required_check)
    if check_run is None:
        return False
    return string_field(check_run, "status") == "completed" and string_field(check_run, "conclusion") == "success"


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
        validate_repository(repository)
        if not isinstance(workflow_run, dict):
            return denied("malformed_input")
        if not isinstance(pull_requests, list) or not isinstance(reviews, list) or not isinstance(check_runs, list):
            return denied("malformed_input")
        if not all(isinstance(item, dict) for item in pull_requests + reviews + check_runs):
            return denied("malformed_input")

        sha = string_field(workflow_run, "head_sha") or ""
        validate_sha(sha)
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
                reviewer = display_login(nested(review, "user", "login")) or display_login(review.get("author"))
                break
        if reviewer is None:
            return denied("missing_non_author_approval", sha, pr_number)

        if not required_check_is_successful(check_runs, sha=sha, required_check=required_check):
            return denied("required_check_not_successful", sha, pr_number, reviewer)

        return Authorization(True, sha, pr_number, "authorized", reviewer)
    except MalformedInput:
        return denied("malformed_input")
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


def flatten_list_pages(value: Any, key: str) -> list[dict[str, object]]:
    if not isinstance(value, list):
        raise ValueError(f"paginated {key} payload must be a list of pages")
    flattened: list[dict[str, object]] = []
    for page in value:
        flattened.extend(list_payload(page, key))
    return flattened


def flatten_object_pages(value: Any, key: str) -> list[dict[str, object]]:
    if not isinstance(value, list):
        raise ValueError(f"paginated {key} payload must be a list of pages")
    flattened: list[dict[str, object]] = []
    for page in value:
        if not isinstance(page, dict):
            raise ValueError(f"paginated {key} page must be an object")
        flattened.extend(list_payload(page, key))
    return flattened


def workflow_payload(value: Any) -> dict[str, object]:
    if isinstance(value, dict) and isinstance(value.get("workflow_run"), dict):
        value = value["workflow_run"]
    if not isinstance(value, dict):
        raise ValueError("workflow run payload must be an object")
    return value


def gh_api_paginated(path: str) -> Any:
    result = subprocess.run(
        ["gh", "api", "--paginate", "--slurp", "-H", "Accept: application/vnd.github+json", path],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        raise RuntimeError(f"gh api {path} failed: {result.stderr.strip()}")
    return json.loads(result.stdout)


def fetch_live_inputs(repository: str, workflow_run: dict[str, object]) -> tuple[list[dict[str, object]], list[dict[str, object]], list[dict[str, object]]]:
    validate_repository(repository)
    sha = string_field(workflow_run, "head_sha") if isinstance(workflow_run, dict) else None
    if sha is None:
        raise ValueError("workflow run is missing head_sha")
    validate_sha(sha)
    pulls = flatten_list_pages(gh_api_paginated(f"/repos/{repository}/commits/{sha}/pulls"), "pulls")
    checks = flatten_object_pages(gh_api_paginated(f"/repos/{repository}/commits/{sha}/check-runs"), "check_runs")
    merged_main_prs = [pull_request for pull_request in pulls if is_merged_main_pr(pull_request)]
    reviews: list[dict[str, object]] = []
    if len(merged_main_prs) == 1:
        pr_number = int_field(merged_main_prs[0], "number")
        if pr_number is not None:
            reviews = flatten_list_pages(gh_api_paginated(f"/repos/{repository}/pulls/{pr_number}/reviews"), "reviews")
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
