#!/usr/bin/env python3
"""Emit a deterministic, content-free inventory of Git history refs."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


class InventoryError(RuntimeError):
    pass


class RepositoryInfo:
    def __init__(self, path: Path, bare: bool) -> None:
        self.path = path
        self.bare = bare


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=check,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def git_stdout(repo: Path, *args: str) -> str:
    return git(repo, *args).stdout


def validate_repo(repo: Path) -> RepositoryInfo:
    repo = repo.resolve()
    if not repo.exists():
        raise InventoryError(f"repository path does not exist: {repo}")
    if not repo.is_dir():
        raise InventoryError(f"repository path is not a directory: {repo}")
    result = git(repo, "rev-parse", "--is-inside-work-tree", check=False)
    if result.returncode == 0 and result.stdout.strip() == "true":
        return RepositoryInfo(repo, bare=False)
    result = git(repo, "rev-parse", "--is-bare-repository", check=False)
    if result.returncode == 0 and result.stdout.strip() == "true":
        return RepositoryInfo(repo, bare=True)
    raise InventoryError(f"not a Git work tree or bare repository: {repo}")


def repository_status(repo: Path, bare: bool) -> dict[str, Any]:
    if bare:
        return {"bare": True, "dirty": False, "entries": []}
    porcelain = git_stdout(repo, "status", "--porcelain=v1", "--untracked-files=all")
    entries: list[dict[str, str]] = []
    for line in porcelain.splitlines():
        if not line:
            continue
        entries.append({"code": line[:2], "path": line[3:] if len(line) > 3 else ""})
    entries.sort(key=lambda entry: (entry["path"], entry["code"]))
    return {"bare": False, "dirty": bool(entries), "entries": entries}


def commit_count(repo: Path, refname: str) -> int:
    result = git(repo, "rev-list", "--count", refname)
    return int(result.stdout.strip() or "0")


def authors_for_ref(repo: Path, refname: str) -> list[str]:
    result = git(repo, "log", "--format=%aN <%aE>", refname)
    return sorted({line for line in result.stdout.splitlines() if line})


def all_authors(repo: Path) -> list[str]:
    result = git(repo, "log", "--format=%aN <%aE>", "--all")
    return sorted({line for line in result.stdout.splitlines() if line})


def peeled_target(repo: Path, refname: str, object_type: str) -> dict[str, str] | None:
    if object_type != "tag":
        return None
    target_id = git_stdout(repo, "rev-parse", f"{refname}^{{}}").strip()
    target_type = git_stdout(repo, "cat-file", "-t", target_id).strip()
    return {"object_id": target_id, "object_type": target_type}


def history_metadata(repo: Path, refname: str, object_type: str, tag_target: dict[str, str] | None) -> tuple[int, list[str]]:
    history_type = tag_target["object_type"] if tag_target is not None else object_type
    if history_type != "commit":
        if refname.startswith("refs/tags/"):
            return 0, []
        raise InventoryError(f"non-commit ref cannot provide history metadata: {refname}")
    return commit_count(repo, refname), authors_for_ref(repo, refname)


def refs_inventory(repo: Path) -> dict[str, dict[str, Any]]:
    refs: dict[str, dict[str, Any]] = {}
    output = git_stdout(
        repo,
        "for-each-ref",
        "--sort=refname",
        "--format=%(refname)%00%(objectname)%00%(objecttype)",
        "refs/heads",
        "refs/tags",
    )
    for raw_line in output.splitlines():
        if not raw_line:
            continue
        refname, object_id, object_type = raw_line.split("\0", 2)
        tag_target = peeled_target(repo, refname, object_type)
        count, authors = history_metadata(repo, refname, object_type, tag_target)
        refs[refname] = {
            "object_id": object_id,
            "object_type": object_type,
            "commit_count": count,
            "authors": authors,
            "annotated_tag_target": tag_target,
        }
    return refs


def build_inventory(repo: Path, allow_dirty: bool) -> dict[str, Any]:
    repo_info = validate_repo(repo)
    repo = repo_info.path
    status = repository_status(repo, repo_info.bare)
    if status["dirty"] and not allow_dirty:
        raise InventoryError("source repository is dirty; re-run with --allow-dirty to inventory it")
    head = git_stdout(repo, "rev-parse", "HEAD").strip()
    return {
        "repository": str(repo),
        "head": head,
        "status": status,
        "authors": all_authors(repo),
        "refs": refs_inventory(repo),
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repository", type=Path, help="source Git repository path")
    parser.add_argument("output", type=Path, help="JSON inventory output path")
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="allow inventorying a repository with uncommitted or untracked changes",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        inventory = build_inventory(args.repository, args.allow_dirty)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(inventory, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except (InventoryError, subprocess.CalledProcessError, OSError, ValueError) as exc:
        print(f"history_inventory: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
