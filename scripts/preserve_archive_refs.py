#!/usr/bin/env python3
"""Preserve legacy archive branches as verified annotated tags."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from typing import NamedTuple


class SafetyError(RuntimeError):
    """Raised when remote refs no longer match the frozen migration inventory."""


class ArchiveRef(NamedTuple):
    branch: str
    oid: str
    tag: str


_BRANCH_OIDS = (
    ("archive/gpu-dev/codex/task5-failure-veil", "7aa30626cf0ceda3b1d5aada4c19d834ecd4b834"),
    ("archive/gpu-dev/develop", "cf70ad07bda5b9b2efb7fb3b06869cc080f95c9a"),
    ("archive/gpu-dev/feature/apple-dashboard-refinement", "ca9ec6614458a6049041dca3c3b874ae4f34bf6f"),
    ("archive/gpu-dev/feature/compact-gpu-dashboard", "64c4b838d6e1293daf52ab0039084a2b9f84bc59"),
    ("archive/gpu-dev/main", "c50f9d2aa9465d742c870ba47793589807832efa"),
    ("archive/gpu-live/main", "f2ea62f5ba4dc6a791bf0faf3fee4153e83462ce"),
    ("archive/gpu-live/old", "b18c78fd7adda3c6065df32d183524f281fa94fe"),
    ("archive/storage/checkpoint/ai-advisor-workspace-20260717", "0685b5f2161041ccce7025a8e5d2b4dd140d6590"),
    ("archive/storage/feature/multiserver-storage-dashboard", "0d7e1dcf2cfd9cfe819851e37384e8bb80930365"),
    ("archive/storage/master", "ea59cb591fbf408c583bdfad570726d8787cc25a"),
)
ARCHIVE_REFS = tuple(
    ArchiveRef(
        branch=branch,
        oid=oid,
        tag=f"archive/branch/{branch.removeprefix('archive/')}",
    )
    for branch, oid in _BRANCH_OIDS
)
OID_RE = re.compile(r"^[0-9a-f]{40}$")
REMOTE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")


def run_git(
    args: list[str],
    *,
    check: bool = True,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        check=check,
        input=input_text,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def parse_ls_remote(output: str) -> dict[str, str]:
    refs: dict[str, str] = {}
    for raw_line in output.splitlines():
        if not raw_line:
            continue
        try:
            oid, ref = raw_line.split("\t", 1)
        except ValueError as error:
            raise SafetyError(f"malformed ls-remote line: {raw_line!r}") from error
        if not OID_RE.fullmatch(oid):
            raise SafetyError(f"malformed remote object id for {ref}: {oid}")
        if ref in refs and refs[ref] != oid:
            raise SafetyError(f"remote ref appeared with conflicting object ids: {ref}")
        refs[ref] = oid
    return refs


def validate_remote(remote: str) -> str:
    if not REMOTE_RE.fullmatch(remote) or ".." in remote or "//" in remote:
        raise SafetyError(f"unsafe remote name: {remote!r}")
    return remote


def validate_archive_refs(refs: tuple[ArchiveRef, ...]) -> None:
    branch_names = [item.branch for item in refs]
    tag_refs = [f"refs/tags/{item.tag}" for item in refs]
    if len(branch_names) != len(set(branch_names)):
        raise SafetyError("duplicate archive branch in frozen inventory")
    if len(tag_refs) != len(set(tag_refs)):
        raise SafetyError("duplicate archive tag in frozen inventory")
    for item, tag_ref in zip(refs, tag_refs, strict=True):
        if not item.branch.startswith("archive/"):
            raise SafetyError(f"branch is outside archive namespace: {item.branch}")
        if not OID_RE.fullmatch(item.oid):
            raise SafetyError(f"invalid frozen object id for {item.branch}: {item.oid}")
        expected_tag = f"archive/branch/{item.branch.removeprefix('archive/')}"
        if item.tag != expected_tag:
            raise SafetyError(
                f"unexpected tag mapping for {item.branch}: "
                f"expected {expected_tag}, found {item.tag}"
            )
        if run_git(["check-ref-format", tag_ref], check=False).returncode != 0:
            raise SafetyError(f"invalid archive tag ref: {tag_ref}")
    ordered = sorted(tag_refs)
    for parent, child in zip(ordered, ordered[1:], strict=False):
        if child.startswith(f"{parent}/"):
            raise SafetyError(
                f"archive tag directory/file conflict: {parent} conflicts with {child}"
            )


def read_remote_snapshot(remote: str) -> dict[str, str]:
    validate_remote(remote)
    result = run_git(["ls-remote", "--heads", "--tags", remote])
    return parse_ls_remote(result.stdout)


def verify_branches(snapshot: dict[str, str]) -> None:
    errors = []
    for item in ARCHIVE_REFS:
        ref = f"refs/heads/{item.branch}"
        actual = snapshot.get(ref)
        if actual != item.oid:
            errors.append(f"{ref}: expected {item.oid}, found {actual or 'missing'}")
    if errors:
        raise SafetyError("archive branch verification failed:\n" + "\n".join(errors))


def verify_tags(snapshot: dict[str, str]) -> None:
    errors = []
    for item in ARCHIVE_REFS:
        tag_ref = f"refs/tags/{item.tag}"
        peeled_ref = f"{tag_ref}^{{}}"
        tag_object = snapshot.get(tag_ref)
        peeled = snapshot.get(peeled_ref)
        if tag_object is None:
            errors.append(f"{tag_ref}: missing")
        if peeled is None:
            errors.append(f"{tag_ref}: not an annotated tag or missing peeled ref")
        elif peeled != item.oid:
            errors.append(f"{peeled_ref}: expected {item.oid}, found {peeled}")
    if errors:
        raise SafetyError("archive tag verification failed:\n" + "\n".join(errors))


def verify_snapshot(snapshot: dict[str, str]) -> None:
    verify_branches(snapshot)
    verify_tags(snapshot)


def create_local_annotated_tag(item: ArchiveRef) -> str:
    date_output = run_git(
        ["show", "-s", "--format=%ct%n%ci", item.oid]
    ).stdout.splitlines()
    if len(date_output) != 2 or not date_output[0].isdigit():
        raise SafetyError(f"unable to determine tagger date for {item.oid}")
    timezone = date_output[1].rsplit(" ", 1)[-1]
    if not re.fullmatch(r"[+-][0-9]{4}", timezone):
        raise SafetyError(f"invalid tagger timezone for {item.oid}: {timezone!r}")
    message = f"Preserve {item.branch} at {item.oid}"
    payload = (
        f"object {item.oid}\n"
        "type commit\n"
        f"tag {item.tag}\n"
        "tagger GPU Monitor Archive Preserver "
        f"<archive-preserver@example.invalid> {date_output[0]} {timezone}\n"
        "\n"
        f"{message}\n"
    )
    expected_tag_oid = run_git(
        ["hash-object", "-t", "tag", "--stdin"],
        input_text=payload,
    ).stdout.strip()
    if not OID_RE.fullmatch(expected_tag_oid):
        raise SafetyError(
            f"invalid deterministic tag object id for {item.tag}: {expected_tag_oid}"
        )

    state = local_tag_state(item)
    tag_ref = f"refs/tags/{item.tag}"
    if state == "missing":
        created_tag_oid = run_git(["mktag"], input_text=payload).stdout.strip()
        if created_tag_oid != expected_tag_oid:
            raise SafetyError(
                f"deterministic tag object mismatch for {item.tag}: "
                f"expected {expected_tag_oid}, created {created_tag_oid}"
            )
        run_git(["update-ref", tag_ref, created_tag_oid, ""])
    tag_object_oid = run_git(["rev-parse", tag_ref]).stdout.strip()
    if not OID_RE.fullmatch(tag_object_oid):
        raise SafetyError(f"invalid local tag object id for {item.tag}: {tag_object_oid}")
    if tag_object_oid != expected_tag_oid:
        raise SafetyError(
            f"non-deterministic local tag object for {item.tag}: "
            f"expected {expected_tag_oid}, found {tag_object_oid}"
        )
    return tag_object_oid


def build_tag_push_command(remote: str, tag_objects: dict[str, str]) -> list[str]:
    validate_remote(remote)
    known_tags = {item.tag for item in ARCHIVE_REFS}
    unknown_tags = sorted(set(tag_objects) - known_tags)
    if unknown_tags:
        raise SafetyError("unknown archive tags requested for push: " + ", ".join(unknown_tags))
    ordered = [item for item in ARCHIVE_REFS if item.tag in tag_objects]
    leases = []
    refspecs = []
    for item in ordered:
        tag_object_oid = tag_objects[item.tag]
        if not OID_RE.fullmatch(tag_object_oid):
            raise SafetyError(
                f"invalid tag object id for {item.tag}: {tag_object_oid}"
            )
        tag_ref = f"refs/tags/{item.tag}"
        leases.append(f"--force-with-lease={tag_ref}:")
        refspecs.append(f"{tag_object_oid}:{tag_ref}")
    return ["push", "--atomic", *leases, remote, *refspecs]


def build_delete_command(remote: str) -> list[str]:
    validate_remote(remote)
    leases = [
        f"--force-with-lease=refs/heads/{item.branch}:{item.oid}"
        for item in ARCHIVE_REFS
    ]
    deletions = [f":refs/heads/{item.branch}" for item in ARCHIVE_REFS]
    return ["push", "--atomic", *leases, remote, *deletions]


def local_tag_state(item: ArchiveRef) -> str:
    ref = f"refs/tags/{item.tag}"
    exists = run_git(["show-ref", "--verify", "--quiet", ref], check=False)
    if exists.returncode == 1:
        return "missing"
    if exists.returncode != 0:
        raise SafetyError(f"unable to inspect local tag: {item.tag}")
    object_type = run_git(["cat-file", "-t", ref]).stdout.strip()
    peeled = run_git(["rev-parse", f"{ref}^{{}}"]).stdout.strip()
    if object_type != "tag" or peeled != item.oid:
        raise SafetyError(
            f"local tag mismatch: {item.tag} type={object_type} peeled={peeled}"
        )
    return "valid"


def ensure_local_objects() -> None:
    for item in ARCHIVE_REFS:
        result = run_git(["cat-file", "-e", f"{item.oid}^{{commit}}"], check=False)
        if result.returncode != 0:
            raise SafetyError(f"missing local commit object: {item.oid}")


def create_and_push_tags(remote: str, snapshot: dict[str, str]) -> dict[str, str]:
    verify_branches(snapshot)
    ensure_local_objects()
    tag_objects = {}
    for item in ARCHIVE_REFS:
        tag_ref = f"refs/tags/{item.tag}"
        peeled_ref = f"{tag_ref}^{{}}"
        remote_tag = snapshot.get(tag_ref)
        remote_peeled = snapshot.get(peeled_ref)
        if remote_tag is not None or remote_peeled is not None:
            if remote_tag is None or remote_peeled != item.oid:
                raise SafetyError(f"remote tag is missing or mismatched: {item.tag}")
            continue
        tag_objects[item.tag] = create_local_annotated_tag(item)
    if tag_objects:
        run_git(build_tag_push_command(remote, tag_objects))
    verified = read_remote_snapshot(remote)
    verify_snapshot(verified)
    return verified


def count_commits(item: ArchiveRef) -> int:
    value = run_git(["rev-list", "--count", item.oid]).stdout.strip()
    if not value.isdigit():
        raise SafetyError(f"invalid reachable commit count for {item.oid}: {value!r}")
    return int(value)


def report(mode: str, remote: str, snapshot: dict[str, str]) -> dict[str, object]:
    records = []
    for item in ARCHIVE_REFS:
        records.append(
            {
                "branch": item.branch,
                "branch_oid": snapshot.get(f"refs/heads/{item.branch}"),
                "tag": item.tag,
                "tag_object_oid": snapshot.get(f"refs/tags/{item.tag}"),
                "peeled_oid": snapshot.get(f"refs/tags/{item.tag}^{{}}"),
                "reachable_commits": count_commits(item),
            }
        )
    return {"mode": mode, "remote": remote, "records": records, "schema": 1}


def delete_verified_branches(remote: str, snapshot: dict[str, str]) -> dict[str, str]:
    verify_snapshot(snapshot)
    run_git(build_delete_command(remote))
    after = read_remote_snapshot(remote)
    remaining = [
        f"refs/heads/{item.branch}"
        for item in ARCHIVE_REFS
        if f"refs/heads/{item.branch}" in after
    ]
    if remaining:
        raise SafetyError("archive branches remain after deletion: " + ", ".join(remaining))
    verify_tags(after)
    return after


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--remote", required=True)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--dry-run", action="store_true")
    modes.add_argument("--create-tags", action="store_true")
    modes.add_argument("--verify", action="store_true")
    modes.add_argument("--delete-verified-branches", action="store_true")
    return parser.parse_args(argv)


def selected_mode(args: argparse.Namespace) -> str:
    if args.dry_run:
        return "dry-run"
    if args.create_tags:
        return "create-tags"
    if args.verify:
        return "verify"
    return "delete-verified-branches"


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    remote = validate_remote(args.remote)
    mode = selected_mode(args)
    validate_archive_refs(ARCHIVE_REFS)
    snapshot = read_remote_snapshot(remote)
    verify_branches(snapshot)
    if mode == "create-tags":
        snapshot = create_and_push_tags(remote, snapshot)
    elif mode == "verify":
        verify_tags(snapshot)
    elif mode == "delete-verified-branches":
        snapshot = delete_verified_branches(remote, snapshot)
    print(json.dumps(report(mode, remote, snapshot), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (SafetyError, subprocess.CalledProcessError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
