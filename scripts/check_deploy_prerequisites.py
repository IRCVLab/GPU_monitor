#!/usr/bin/env python3.12
"""Read-only deployment prerequisite checker for the monitoring monorepo."""

from __future__ import annotations

import argparse
import base64
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

REQUIRED_STATUS_CHECK = "ci/required"
READY = "READY"
BLOCKED = "BLOCKED"
UNKNOWN = "UNKNOWN"
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class GhApiError(RuntimeError):
    def __init__(self, path: str, returncode: int, stderr: str):
        super().__init__(f"gh api {path} failed with exit {returncode}: {stderr.strip()}")
        self.path = path
        self.returncode = returncode
        self.stderr = stderr.strip()


def normalize_status(value: Any) -> str:
    status = str(value or UNKNOWN).upper()
    if status in {READY, BLOCKED, UNKNOWN}:
        return status
    if status in {"PASS", "PASSED", "OK", "TRUE", "AVAILABLE"}:
        return READY
    if status in {"FAIL", "FAILED", "ERROR", "FALSE", "MISSING"}:
        return BLOCKED
    return UNKNOWN


def check(status: str, evidence: str) -> dict[str, str]:
    return {"status": normalize_status(status), "evidence": evidence}


def merge_status(checks: list[dict[str, str]], *, unknown_is_ready: bool = False) -> str:
    statuses = [item["status"] for item in checks]
    if BLOCKED in statuses:
        return BLOCKED
    if UNKNOWN in statuses and not unknown_is_ready:
        return UNKNOWN
    return READY


def required_contexts(rule: dict[str, Any]) -> set[str]:
    contexts: set[str] = set()
    for value in rule.get("requiredStatusCheckContexts") or []:
        if isinstance(value, str):
            contexts.add(value)

    status_checks = rule.get("requiredStatusChecks")
    if isinstance(status_checks, dict):
        for value in status_checks.get("contexts") or []:
            if isinstance(value, str):
                contexts.add(value)
        for item in status_checks.get("checks") or []:
            if isinstance(item, dict) and isinstance(item.get("context"), str):
                contexts.add(item["context"])
            elif isinstance(item, str):
                contexts.add(item)
    return contexts


def approving_review_count(rule: dict[str, Any]) -> int:
    value = rule.get("requiredApprovingReviewCount")
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if rule.get("requiresApprovingReviews") is True:
        return 1
    return 0


def branch_protection_check(metadata: dict[str, Any]) -> dict[str, str]:
    default_branch = metadata.get("defaultBranchRef", {}).get("name") or metadata.get("repository", {}).get("default_branch") or "main"
    if default_branch != "main":
        return check(BLOCKED, f"default branch is {default_branch!r}, expected 'main'")

    rule = metadata.get("branchProtectionRule")
    repo = metadata.get("repository", {})
    protection_error = metadata.get("branchProtectionError") or ""
    if not isinstance(rule, dict) or not rule:
        if "upgrade" in protection_error.lower() and "403" in protection_error:
            return check(BLOCKED, f"private-plan branch protection unavailable: {protection_error}")
        if repo.get("isPrivate") is True:
            return check(
                BLOCKED,
                "private-plan branch protection unavailable or not configured for main; required before deployment bootstrap",
            )
        return check(BLOCKED, "main has no branch protection rule")

    contexts = required_contexts(rule)
    if REQUIRED_STATUS_CHECK not in contexts:
        return check(BLOCKED, f"main protection does not require {REQUIRED_STATUS_CHECK}")
    if rule.get("allowsForcePushes") is True:
        return check(BLOCKED, "main protection allows force pushes")
    if rule.get("adminBypassAllowed") is True or rule.get("isAdminEnforced") is False:
        return check(BLOCKED, "main protection allows administrator bypass")
    return check(READY, f"main is protected and requires {REQUIRED_STATUS_CHECK}")

def codeowner_check(metadata: dict[str, Any]) -> dict[str, str]:
    codeowners = metadata.get("codeowners") or {}
    rule = metadata.get("branchProtectionRule") or {}
    present = codeowners.get("present") is True
    review_required = codeowners.get("reviewRequired") is True or rule.get("requiresCodeOwnerReviews") is True
    review_count = approving_review_count(rule) if isinstance(rule, dict) else 0

    if not present:
        return check(BLOCKED, "CODEOWNERS file is missing")
    if review_count < 1:
        return check(BLOCKED, "main protection requires zero approving pull request reviews")
    if not review_required:
        return check(BLOCKED, "main protection does not require CODEOWNER review")
    return check(READY, "CODEOWNERS exists and main requires code-owner review")

def runner_check(metadata: dict[str, Any]) -> dict[str, str]:
    runner = metadata.get("runnerAvailability") or {}
    runners = runner.get("runners")
    if isinstance(runners, list):
        online = [item for item in runners if isinstance(item, dict) and str(item.get("status", "")).lower() == "online"]
        offline = [item for item in runners if isinstance(item, dict) and str(item.get("status", "")).lower() == "offline"]
        if online:
            names = ", ".join(str(item.get("name", "unnamed")) for item in online)
            return check(READY, f"online runner(s) available: {names}")
        if offline:
            names = ", ".join(str(item.get("name", "unnamed")) for item in offline)
            return check(BLOCKED, f"runner(s) are offline: {names}")
    status = normalize_status(runner.get("status"))
    evidence = runner.get("evidence") or "runner availability not inspected"
    return check(status, evidence)

def server_check(metadata: dict[str, Any]) -> dict[str, str]:
    server = metadata.get("serverReachability") or {}
    status = normalize_status(server.get("status"))
    evidence = server.get("evidence") or "host check not requested"
    return check(status, evidence)


def evaluate_metadata(metadata: dict[str, Any], *, require_host_for_cutover: bool = False) -> dict[str, Any]:
    checks = {
        "protected_main": branch_protection_check(metadata),
        "codeowner_enforcement": codeowner_check(metadata),
        "runner_availability": runner_check(metadata),
        "server_reachability": server_check(metadata),
    }

    ci_publication = merge_status([checks["protected_main"], checks["codeowner_enforcement"]])
    runner_registration = merge_status(
        [checks["protected_main"], checks["codeowner_enforcement"], checks["runner_availability"]]
    )
    cutover_checks = [
        checks["protected_main"],
        checks["codeowner_enforcement"],
        checks["runner_availability"],
        checks["server_reachability"],
    ]
    cutover = merge_status(cutover_checks)

    overall_checks = [checks["protected_main"], checks["codeowner_enforcement"], checks["runner_availability"]]
    if require_host_for_cutover:
        overall_checks.append(checks["server_reachability"])
    overall = merge_status(overall_checks)

    return {
        "overall": overall,
        "ci_publication": ci_publication,
        "runner_registration": runner_registration,
        "cutover": cutover,
        "checks": checks,
    }


def classify_gh_api_failure(path: str, returncode: int, stderr: str) -> dict[str, str]:
    evidence = f"gh api {path} returned exit {returncode}: {stderr or 'no error output'}"
    lowered = stderr.lower()
    if "/branches/" in path and "/protection" in path and "403" in stderr and "upgrade" in lowered:
        return {"status": "blocked", "evidence": evidence}
    if returncode == 127 or "command not found" in lowered or "not found" in lowered and "gh" in lowered:
        return {"status": "unknown", "evidence": evidence}
    if any(token in stderr for token in ("HTTP 401", "HTTP 403", "HTTP 500", "HTTP 502", "HTTP 503", "HTTP 504")):
        return {"status": "unknown", "evidence": evidence}
    return {"status": "unknown", "evidence": evidence}


def run_gh_api(path: str) -> dict[str, Any]:
    try:
        result = subprocess.run(
            ["gh", "api", path],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except FileNotFoundError as exc:
        raise GhApiError(path, 127, str(exc)) from exc
    if result.returncode != 0:
        raise GhApiError(path, result.returncode, result.stderr)
    return json.loads(result.stdout or "{}")

def fetch_codeowners(repo: str, branch: str) -> dict[str, Any]:
    for path in (".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS"):
        try:
            payload = run_gh_api(f"repos/{repo}/contents/{path}?ref={branch}")
        except GhApiError:
            continue
        content = payload.get("content") or ""
        if payload.get("encoding") == "base64":
            content = base64.b64decode(content).decode("utf-8", errors="replace")
        return {"present": True, "reviewRequired": False, "path": path, "entries": len(content.splitlines())}
    return {"present": False, "reviewRequired": False}


def metadata_from_api_payloads(
    *,
    repo: str,
    repository: dict[str, Any],
    protection: dict[str, Any] | None = None,
    protection_error: str = "",
    codeowners: dict[str, Any] | None = None,
    runner: dict[str, Any] | None = None,
    check_host: str | None = None,
) -> dict[str, Any]:
    branch = repository.get("default_branch") or "main"
    branch_rule: dict[str, Any] | None = None
    if protection is not None:
        status_checks = protection.get("required_status_checks")
        contexts: list[str] = []
        if isinstance(status_checks, dict):
            contexts.extend(value for value in status_checks.get("contexts") or [] if isinstance(value, str))
            for item in status_checks.get("checks") or []:
                if isinstance(item, dict) and isinstance(item.get("context"), str):
                    contexts.append(item["context"])
        reviews = protection.get("required_pull_request_reviews") or {}
        enforce_admins = protection.get("enforce_admins") or {}
        force_pushes = protection.get("allow_force_pushes") or {}
        branch_rule = {
            "requiredStatusCheckContexts": contexts,
            "requiredApprovingReviewCount": reviews.get("required_approving_review_count", 0),
            "requiresCodeOwnerReviews": reviews.get("require_code_owner_reviews") is True,
            "isAdminEnforced": enforce_admins.get("enabled") is True,
            "allowsForcePushes": force_pushes.get("enabled") is True,
        }

    server = inspect_host(check_host) if check_host else {"status": "unknown", "evidence": "host check not requested; no server contact attempted"}
    metadata = {
        "repository": {
            "nameWithOwner": repository.get("full_name", repo),
            "isPrivate": repository.get("private"),
            "default_branch": branch,
        },
        "defaultBranchRef": {"name": branch},
        "branchProtectionRule": branch_rule,
        "codeowners": codeowners or {"present": False, "reviewRequired": False},
        "runnerAvailability": runner or {"status": "unknown", "evidence": "runner availability not inspected"},
        "serverReachability": server,
    }
    if protection_error:
        metadata["branchProtectionError"] = protection_error
    if branch_rule and branch_rule.get("requiresCodeOwnerReviews"):
        metadata["codeowners"]["reviewRequired"] = True
    return metadata


def validate_repo_name(repo: str) -> None:
    if not REPO_RE.match(repo):
        raise ValueError("--repo must be in OWNER/REPO form using repository owner and name")


def fetch_live_metadata(repo: str, *, check_host: str | None) -> dict[str, Any]:
    validate_repo_name(repo)
    repository = run_gh_api(f"repos/{repo}")
    branch = repository.get("default_branch") or "main"

    try:
        protection = run_gh_api(f"repos/{repo}/branches/{branch}/protection")
        protection_error = ""
    except GhApiError as exc:
        protection = None
        protection_error = classify_gh_api_failure(exc.path, exc.returncode, exc.stderr)["evidence"]

    codeowners = fetch_codeowners(repo, branch)
    owner = repo.split("/", 1)[0]
    try:
        run_gh_api(f"orgs/{owner}/actions/runner-groups")
        runner = {"status": "ready", "evidence": "org runner-group API is readable; runner registration permission can be evaluated"}
    except GhApiError as exc:
        runner = classify_gh_api_failure(exc.path, exc.returncode, exc.stderr)

    return metadata_from_api_payloads(
        repo=repo,
        repository=repository,
        protection=protection,
        protection_error=protection_error,
        codeowners=codeowners,
        runner=runner,
        check_host=check_host,
    )

def parse_host_target(target: str) -> tuple[str, int]:
    if target.count(":") == 1:
        host, port_text = target.rsplit(":", 1)
        if port_text.isdigit():
            port = int(port_text)
            if not (1 <= port <= 65535):
                raise ValueError(f"invalid port in --check-host {target!r}")
            return host, port
    return target, 22


def inspect_host(host: str, *, runner=subprocess.run) -> dict[str, str]:
    try:
        hostname, port = parse_host_target(host)
    except ValueError as exc:
        return {"status": "blocked", "evidence": str(exc)}

    argv = [
        "ssh",
        "-p",
        str(port),
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=5",
        "-o",
        "ConnectionAttempts=1",
        "-o",
        "UserKnownHostsFile=/dev/null",
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "LogLevel=ERROR",
        hostname,
        "true",
    ]
    result = runner(argv, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if result.returncode == 0:
        return {"status": "ready", "evidence": f"bounded read-only ssh probe succeeded for {hostname}:{port}"}
    stderr = (result.stderr or result.stdout or f"ssh exited {result.returncode}").strip()
    return {"status": "blocked", "evidence": f"bounded read-only ssh probe failed for {hostname}:{port}: {stderr}"}


def load_metadata(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--metadata-file", type=Path, help="read deterministic repository metadata JSON")
    source.add_argument("--repo", help="GitHub repository name, for read-only gh api inspection (OWNER/REPO)")
    parser.add_argument("--check-host", help="explicitly run a bounded read-only SSH probe for host or host:port")
    parser.add_argument(
        "--require-host-for-cutover",
        action="store_true",
        help="include server reachability in the process exit status",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        if args.repo:
            validate_repo_name(args.repo)
        if args.metadata_file:
            metadata = load_metadata(args.metadata_file)
            if not isinstance(metadata, dict):
                raise ValueError("metadata root must be a JSON object")
        else:
            metadata = fetch_live_metadata(args.repo, check_host=args.check_host)
    except (json.JSONDecodeError, OSError, ValueError) as exc:
        print(f"invalid metadata or arguments: {exc}", file=sys.stderr)
        return 2
    except GhApiError as exc:
        failure = classify_gh_api_failure(exc.path, exc.returncode, exc.stderr)
        report = {
            "overall": UNKNOWN,
            "ci_publication": UNKNOWN,
            "runner_registration": UNKNOWN,
            "cutover": UNKNOWN,
            "checks": {
                "protected_main": check(UNKNOWN, failure["evidence"]),
                "codeowner_enforcement": check(UNKNOWN, "not inspected because GitHub metadata was unavailable"),
                "runner_availability": check(UNKNOWN, "not inspected because GitHub metadata was unavailable"),
                "server_reachability": check(UNKNOWN, "host check not requested"),
            },
        }
        print(json.dumps(report, indent=2, sort_keys=True))
        return 1

    report = evaluate_metadata(metadata, require_host_for_cutover=args.require_host_for_cutover)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["overall"] == READY else 1

if __name__ == "__main__":
    raise SystemExit(main())
