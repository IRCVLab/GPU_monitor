#!/usr/bin/env python3.12
"""Classify changed repository paths into CI impact decisions."""

from __future__ import annotations

import argparse
import json
import os
import posixpath
import subprocess
from pathlib import PureWindowsPath
import sys
from typing import NamedTuple, Sequence


DECISION_KEYS = (
    "gpu",
    "storage_dashboard",
    "storage_agent",
    "shared",
    "workflow",
    "documentation",
    "apps_required",
)

GITHUB_OUTPUT_KEYS = DECISION_KEYS


class PathRule(NamedTuple):
    decision: str
    prefixes: tuple[str, ...]
    exact: tuple[str, ...] = ()
    suffixes: tuple[str, ...] = ()


def _with_slash(prefix: str) -> str:
    return prefix if prefix.endswith("/") else f"{prefix}/"


GPU_RULE = PathRule(
    "gpu",
    prefixes=(
        "apps/gpu-monitor/",
        "deploy/gpu-monitor/",
    ),
)
STORAGE_DASHBOARD_RULE = PathRule(
    "storage_dashboard",
    prefixes=(
        "apps/storage-monitor/collector/",
        "apps/storage-monitor/config/",
        "apps/storage-monitor/data/",
        "apps/storage-monitor/deploy/server/",
        "apps/storage-monitor/viewer/",
        "deploy/storage-dashboard/",
    ),
    exact=(
        "apps/storage-monitor/.gitignore",
        "apps/storage-monitor/Makefile",
        "apps/storage-monitor/deploy/build-dashboard-release.py",
        "apps/storage-monitor/deploy/direct_proxy.py",
        "apps/storage-monitor/deploy/test_dashboard_release.py",
        "apps/storage-monitor/deploy/test_direct_proxy.py",
        "apps/storage-monitor/pyproject.toml",
        "apps/storage-monitor/pytest.ini",
        "apps/storage-monitor/requirements.txt",
        "apps/storage-monitor/requirements-dev.txt",
        "tests/test_storage_release_puller.py",
    ),
)
STORAGE_AGENT_RULE = PathRule(
    "storage_agent",
    prefixes=(
        "apps/storage-monitor/agent/",
        "apps/storage-monitor/config/",
        "apps/storage-monitor/scanner/",
        "deploy/storage-agent/",
    ),
    exact=(
        "apps/storage-monitor/.gitignore",
        "apps/storage-monitor/Makefile",
        "apps/storage-monitor/deploy/deploy-agent.sh",
        "apps/storage-monitor/deploy/install-agent.sh",
        "apps/storage-monitor/deploy/systemd/storage-viz-scan.service.in",
        "apps/storage-monitor/deploy/systemd/storage-viz-scan.timer",
        "apps/storage-monitor/install.sh",
        "apps/storage-monitor/pyproject.toml",
        "apps/storage-monitor/pytest.ini",
        "apps/storage-monitor/requirements.txt",
        "apps/storage-monitor/requirements-dev.txt",
    ),
)
WORKFLOW_RULE = PathRule("workflow", prefixes=(".github/",))
SHARED_RULE = PathRule(
    "shared",
    prefixes=("scripts/",),
    exact=(
        ".editorconfig",
        ".gitattributes",
        ".gitignore",
        "Makefile",
        "package-lock.json",
        "package.json",
        "pyproject.toml",
        "pytest.ini",
        "requirements.txt",
        "requirements-dev.txt",
    ),
)
DESIGN_TOKENS_RULE = PathRule("shared", prefixes=("packages/design-tokens/",))
DOCUMENTATION_RULE = PathRule(
    "documentation",
    prefixes=(
        "docs/",
        "apps/gpu-monitor/docs/",
        "apps/gpu-monitor/feature/",
        "apps/storage-monitor/docs/",
        "apps/storage-monitor/feature/",
    ),
    exact=("README.md", "CONTRIBUTING.md", "SECURITY.md"),
    suffixes=(".md",),
)

PATH_RULES = (
    GPU_RULE,
    STORAGE_DASHBOARD_RULE,
    STORAGE_AGENT_RULE,
    WORKFLOW_RULE,
    SHARED_RULE,
    DOCUMENTATION_RULE,
)


def normalize_path(path: str) -> str:
    raw = path.strip()
    if not raw:
        raise ValueError("empty path")
    if PureWindowsPath(raw).drive:
        raise ValueError(f"invalid repository-relative path: {path}")
    slash_normalized = raw.replace("\\", "/")
    normalized = posixpath.normpath(slash_normalized)
    if normalized == ".":
        raise ValueError("empty path")
    if (
        slash_normalized.startswith("/")
        or any(part == ".." for part in slash_normalized.split("/"))
        or normalized.startswith("/")
        or normalized.startswith("../")
        or normalized == ".."
    ):
        raise ValueError(f"invalid repository-relative path: {path}")
    return normalized[2:] if normalized.startswith("./") else normalized


def normalize_paths(paths: Sequence[str]) -> list[str]:
    normalized_paths: set[str] = set()
    for path in paths:
        if not path.strip():
            continue
        normalized_paths.add(normalize_path(path))
    return sorted(normalized_paths)


def matches_prefix(path: str, prefix: str) -> bool:
    normalized_prefix = prefix.rstrip("/")
    return path == normalized_prefix or path.startswith(_with_slash(normalized_prefix))


def matches_rule(path: str, rule: PathRule) -> bool:
    return (
        path in rule.exact
        or any(matches_prefix(path, prefix) for prefix in rule.prefixes)
        or any(path.endswith(suffix) for suffix in rule.suffixes)
    )


def classify_paths(paths: Sequence[str]) -> dict[str, bool]:
    normalized_paths = normalize_paths(paths)
    decisions = {key: False for key in DECISION_KEYS}
    full_shared_change = False

    for path in normalized_paths:
        documentation_only = matches_rule(path, DOCUMENTATION_RULE)
        path_matched = documentation_only
        if not documentation_only and matches_rule(path, DESIGN_TOKENS_RULE):
            decisions["shared"] = True
            decisions["gpu"] = True
            decisions["storage_dashboard"] = True
            path_matched = True
        for rule in PATH_RULES:
            if documentation_only and rule.decision in {"gpu", "storage_dashboard", "storage_agent"}:
                continue
            if matches_rule(path, rule):
                path_matched = True
                decisions[rule.decision] = True
                if rule is SHARED_RULE:
                    full_shared_change = True
        if not path_matched:
            decisions["shared"] = True
            full_shared_change = True

    if decisions["workflow"] or full_shared_change:
        decisions["gpu"] = True
        decisions["storage_dashboard"] = True
        decisions["storage_agent"] = True

    decisions["apps_required"] = decisions["gpu"] or decisions["storage_dashboard"] or decisions["storage_agent"]
    return decisions


def read_paths_file(paths_file: str) -> list[str]:
    with open(paths_file, "r", encoding="utf-8") as handle:
        return handle.read().splitlines()


def read_tracked_paths() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.stdout.splitlines()


def merge_base(base: str, head: str) -> str:
    result = subprocess.run(
        ["git", "merge-base", base, head],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.stdout.strip()


def read_git_range(base: str, head: str, *, use_merge_base: bool = False) -> list[str]:
    diff_base = merge_base(base, head) if use_merge_base else base
    result = subprocess.run(
        ["git", "diff", "--name-status", "--find-renames", diff_base, head],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    paths: list[str] = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        fields = line.split("\t")
        status = fields[0]
        if status.startswith(("R", "C")) and len(fields) >= 3:
            paths.extend(fields[1:3])
        elif len(fields) >= 2:
            paths.append(fields[1])
    return paths


def write_github_outputs(decisions: dict[str, bool], output_path: str | None) -> None:
    if not output_path:
        return
    with open(output_path, "a", encoding="utf-8") as handle:
        for key in GITHUB_OUTPUT_KEYS:
            handle.write(f"{key}={str(decisions[key]).lower()}\n")


def build_payload(paths: Sequence[str]) -> dict[str, bool]:
    normalized_paths = normalize_paths(paths)
    return classify_paths(normalized_paths)


def error_message(exc: BaseException) -> str:
    if isinstance(exc, subprocess.CalledProcessError) and exc.stderr:
        return exc.stderr.strip()
    return str(exc)


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--paths-file", help="newline-delimited repository-relative changed paths")
    source.add_argument("--base", help="base Git revision for changed-path discovery")
    parser.add_argument("--head", help="head Git revision for changed-path discovery")
    parser.add_argument(
        "--merge-base",
        action="store_true",
        help="diff the merge-base of --base/--head against --head instead of diffing --base directly",
    )
    parser.add_argument(
        "--fallback-to-all-tracked",
        action="store_true",
        help="classify every tracked path if Git range discovery fails",
    )
    args = parser.parse_args(argv)
    if args.head and not args.base:
        parser.error("--head requires --base")
    if args.base and not args.head:
        parser.error("--base and --head must be supplied together")
    if args.merge_base and not args.base:
        parser.error("--merge-base requires --base and --head")
    if args.fallback_to_all_tracked and not args.base:
        parser.error("--fallback-to-all-tracked requires --base and --head")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        if args.paths_file:
            paths = read_paths_file(args.paths_file)
        else:
            try:
                paths = read_git_range(args.base, args.head, use_merge_base=args.merge_base)
            except subprocess.CalledProcessError:
                if not args.fallback_to_all_tracked:
                    raise
                paths = read_tracked_paths()
        payload = build_payload(paths)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(error_message(exc), file=sys.stderr)
        return 2

    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    write_github_outputs(payload, os.environ.get("GITHUB_OUTPUT"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
