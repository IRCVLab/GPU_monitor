#!/usr/bin/env python3.12
"""Fail-closed GPU release authorization from GitHub provenance evidence."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

SHA_RE = re.compile(r"^[0-9a-f]{40}$")
REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
RFC3339_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)


@dataclass(frozen=True)
class Authorization:
    authorized: bool
    sha: str
    reason: str


class MalformedInput(ValueError):
    pass


def denied(reason: str, sha: str = "") -> Authorization:
    return Authorization(False, sha, reason)


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


def parse_github_timestamp(value: object, field_name: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise MalformedInput(f"{field_name} must be a non-empty RFC3339 timestamp")
    if RFC3339_RE.fullmatch(value) is None:
        raise MalformedInput(f"{field_name} must be a GitHub RFC3339 timestamp")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as error:
        raise MalformedInput(f"{field_name} must be a valid timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise MalformedInput(f"{field_name} must include a timezone")
    return parsed.astimezone(timezone.utc)


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

    ordered: list[tuple[datetime, int, dict[str, object]]] = []
    seen_keys: set[tuple[datetime, int]] = set()
    for check_run in matches:
        check_id = int_field(check_run, "id")
        if check_id is None:
            raise MalformedInput("matching required check id must be numeric")
        completed_at = parse_github_timestamp(string_field(check_run, "completed_at"), "completed_at")
        key = (completed_at, check_id)
        if key in seen_keys:
            raise MalformedInput("matching required checks have ambiguous ordering")
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
    check_runs: list[dict[str, object]],
    *,
    current_main_sha: str,
    repository: str,
    required_check: str = "ci/required",
) -> Authorization:
    try:
        validate_repository(repository)
        if not isinstance(workflow_run, dict):
            return denied("malformed_input")
        if not isinstance(check_runs, list) or not all(isinstance(item, dict) for item in check_runs):
            return denied("malformed_input")

        sha = string_field(workflow_run, "head_sha") or ""
        validate_sha(sha)
        if string_field(workflow_run, "name") != "ci":
            return denied("workflow_name_mismatch", sha)
        if string_field(workflow_run, "event") != "push":
            return denied("workflow_event_not_push", sha)
        if string_field(workflow_run, "head_branch") != "main":
            return denied("workflow_branch_not_main", sha)
        if string_field(workflow_run, "status") != "completed":
            return denied("workflow_status_not_completed", sha)
        if string_field(workflow_run, "conclusion") != "success":
            return denied("workflow_conclusion_not_success", sha)
        if string_field(workflow_run, "head_repository", "full_name") != repository:
            return denied("workflow_repository_mismatch", sha)
        validate_sha(current_main_sha)
        if sha != current_main_sha:
            return denied("workflow_sha_not_current_main", sha)
        if not required_check_is_successful(check_runs, sha=sha, required_check=required_check):
            return denied("required_check_not_successful", sha)
        return Authorization(True, sha, "authorized")
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


def gh_api_object(path: str) -> dict[str, object]:
    result = subprocess.run(
        ["gh", "api", "-H", "Accept: application/vnd.github+json", path],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        raise RuntimeError(f"gh api {path} failed: {result.stderr.strip()}")
    payload = json.loads(result.stdout)
    if not isinstance(payload, dict):
        raise ValueError(f"gh api {path} payload must be an object")
    return payload


def fetch_live_evidence(
    repository: str,
    workflow_run: dict[str, object],
) -> tuple[list[dict[str, object]], str]:
    validate_repository(repository)
    sha = string_field(workflow_run, "head_sha") if isinstance(workflow_run, dict) else None
    if sha is None:
        raise ValueError("workflow run is missing head_sha")
    validate_sha(sha)
    checks = flatten_object_pages(
        gh_api_paginated(f"/repos/{repository}/commits/{sha}/check-runs"),
        "check_runs",
    )
    main_ref = gh_api_object(f"/repos/{repository}/git/ref/heads/main")
    current_main_sha = string_field(main_ref, "object", "sha")
    if current_main_sha is None:
        raise ValueError("main ref is missing object.sha")
    validate_sha(current_main_sha)
    return checks, current_main_sha


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--workflow-run-file", required=True)
    parser.add_argument("--checks-file")
    parser.add_argument("--current-main-sha")
    parser.add_argument("--required-check", default="ci/required")
    parser.add_argument("--live", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        workflow_run = workflow_payload(read_json_file(args.workflow_run_file))
        if args.live:
            check_runs, current_main_sha = fetch_live_evidence(args.repository, workflow_run)
        else:
            if args.checks_file is None or args.current_main_sha is None:
                raise ValueError("--checks-file and --current-main-sha are required without --live")
            check_runs = list_payload(read_json_file(args.checks_file), "check_runs")
            current_main_sha = args.current_main_sha
        authorization = authorize_release(
            workflow_run,
            check_runs,
            current_main_sha=current_main_sha,
            repository=args.repository,
            required_check=args.required_check,
        )
    except Exception as error:
        authorization = denied(f"input_error:{type(error).__name__}")

    print(json.dumps(asdict(authorization), sort_keys=True))
    return 0 if authorization.authorized else 1


if __name__ == "__main__":
    raise SystemExit(main())
