#!/usr/bin/env python3
"""Build the deterministic storage-viz agent release bundle for one commit.

The bundle is the release tier of a storage host (target-design §4.5):
`agent/` (the non-test modules), `storage_fstypes.py`, `bin/storage-viz-agent`,
`bin/hstscan` and `RELEASE-MANIFEST.json`. CI signs the archive afterwards with
`deploy/sign-agent-release.sh`; this script never touches a key.

The bundle's `source_sha` is the newest commit at or before `--sha` that changed
an agent input, so every commit without agent changes yields the same bytes and
hosts update only when the agent changed. `release_seq` is that commit's
`git rev-list --count`, so the checkout must have full history.

hstscan is compiled in a pinned Ubuntu 20.04 image (glibc 2.31, the oldest
fleet host) through `docker run` with no bind mounts: source on stdin, binary on
stdout. Without docker, pass `--hstscan-binary` with a binary built on glibc
2.31 or older; the result is then only as reproducible as that binary.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import re
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

APP = "apps/storage-monitor"
APPLICATION = "storage-viz-agent"
BOOTSTRAP_VERSION = 1
ARCHIVE_NAME = "storage-viz-agent.tar.gz"
METADATA_NAME = "storage-viz-agent.json"
FLOOR_FILE = f"{APP}/agent/RELEASE-FLOOR.json"
FLOOR_TRAIL = 20  # releases a host may still roll back across
BUILDER_IMAGE = "ubuntu:20.04@sha256:8feb4d8ca5354def3d8fce243717141ce31e2c428701f6682bd2fafe15388214"
HSTSCAN_BUILD = (
    "set -e; export DEBIAN_FRONTEND=noninteractive; "
    "apt-get update -qq >&2; apt-get install -y -qq --no-install-recommends gcc libc6-dev >&2; "
    "mkdir /src; cat > /src/hstscan.c; cd /src; "
    "gcc -O2 -Wall -Wextra -pthread -std=gnu11 -o hstscan hstscan.c -pthread >&2; cat hstscan"
)
# Pathspecs whose history defines an agent release. Tests and the bootstrap
# tier do not change what a host runs, so they do not cut a release.
AGENT_INPUTS = (
    f"{APP}/agent",
    f"{APP}/scanner/hstscan.c",
    f"{APP}/storage_fstypes.py",
    f"{APP}/deploy/build-agent-release.py",
    f":(exclude){APP}/agent/bootstrap",
    f":(exclude){APP}/agent/testdata",
    f":(exclude,glob){APP}/agent/**/test_*.py",
)
SHA_RE = re.compile(r"^[0-9a-f]{40}$")


class BuildError(Exception):
    pass


def git(repo: Path, *args: str) -> bytes:
    result = subprocess.run(["git", *args], cwd=repo, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if result.returncode != 0:
        raise BuildError(f"git {' '.join(args)}: {result.stderr.decode(errors='replace').strip()}")
    return result.stdout


def agent_commits(repo: Path, sha: str) -> List[str]:
    """Commits at or before sha that changed an agent input, newest first."""
    return git(repo, "rev-list", sha, "--", *AGENT_INPUTS).decode().split()


def release_seq(repo: Path, sha: str) -> int:
    return int(git(repo, "rev-list", "--count", sha).decode())


def rollback_floor(repo: Path, commits: List[str], floor_file: dict) -> int:
    trailing = release_seq(repo, commits[FLOOR_TRAIL]) if len(commits) > FLOOR_TRAIL else 0
    minimum = floor_file.get("min_rollback_floor_seq")
    if not isinstance(minimum, int) or isinstance(minimum, bool) or minimum < 0:
        raise BuildError("RELEASE-FLOOR.json min_rollback_floor_seq must be a non-negative integer")
    return max(trailing, minimum)


def bundle_sources(repo: Path, source_sha: str) -> Dict[str, Tuple[bytes, int]]:
    """Map bundle path to (bytes, mode) from git, without hstscan."""
    listing = git(repo, "ls-tree", "--name-only", source_sha, f"{APP}/agent/").decode().split()
    listing.extend(git(repo, "ls-tree", "--name-only", source_sha,
                       f"{APP}/agent/storage_viz_agent/").decode().split())
    modules = sorted(
        path for path in listing
        if path.endswith(".py") and not Path(path).name.startswith("test_")
    )
    files: Dict[str, Tuple[bytes, int]] = {}
    for path in modules:
        files[str(Path(path).relative_to(APP))] = (git(repo, "show", f"{source_sha}:{path}"), 0o644)
    files["storage_fstypes.py"] = (git(repo, "show", f"{source_sha}:{APP}/storage_fstypes.py"), 0o644)
    files["bin/storage-viz-agent"] = (git(repo, "show", f"{source_sha}:{APP}/agent/bin/storage-viz-agent"), 0o755)
    return files


def compile_hstscan(source: bytes) -> bytes:
    if shutil.which("docker") is None:
        raise BuildError("docker is unavailable; pass --hstscan-binary with a binary built on glibc <= 2.31")
    result = subprocess.run(
        ["docker", "run", "--rm", "-i", BUILDER_IMAGE, "sh", "-c", HSTSCAN_BUILD],
        input=source, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    if result.returncode != 0 or not result.stdout.startswith(b"\x7fELF"):
        raise BuildError(f"hstscan build failed: {result.stderr.decode(errors='replace')[-2000:]}")
    return result.stdout


def manifest_bytes(fields: dict, files: Dict[str, Tuple[bytes, int]]) -> bytes:
    payload = dict(fields)
    payload["files"] = {path: hashlib.sha256(files[path][0]).hexdigest() for path in sorted(files)}
    return (json.dumps(payload, sort_keys=True, indent=2) + "\n").encode()


def pack(files: Dict[str, Tuple[bytes, int]]) -> bytes:
    """A gzip tar whose bytes depend only on the file paths, bytes and modes."""
    buffer = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=buffer, mtime=0) as gz:
        with tarfile.open(fileobj=gz, mode="w", format=tarfile.USTAR_FORMAT) as tar:
            for path in sorted(files):
                data, mode = files[path]
                info = tarfile.TarInfo(path)
                info.size, info.mode, info.mtime = len(data), mode, 0
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def build(repo: Path, sha: str, output_dir: Path, hstscan_binary: Optional[Path] = None) -> dict:
    if git(repo, "rev-parse", "--is-shallow-repository").strip() != b"false":
        raise BuildError("release_seq needs full history; fetch with depth 0")
    commits = agent_commits(repo, sha)
    if not commits:
        raise BuildError(f"no agent inputs at {sha}")
    source_sha = commits[0]
    floor_file = json.loads(git(repo, "show", f"{source_sha}:{FLOOR_FILE}"))
    fields = {
        "application": APPLICATION,
        "source_sha": source_sha,
        "release_seq": release_seq(repo, source_sha),
        "rollback_floor_seq": rollback_floor(repo, commits, floor_file),
        "bootstrap_version": BOOTSTRAP_VERSION,
    }
    files = bundle_sources(repo, source_sha)
    if hstscan_binary is not None:
        files["bin/hstscan"] = (hstscan_binary.read_bytes(), 0o755)
    else:
        files["bin/hstscan"] = (compile_hstscan(git(repo, "show", f"{source_sha}:{APP}/scanner/hstscan.c")), 0o755)
    archive = pack({**files, "RELEASE-MANIFEST.json": (manifest_bytes(fields, files), 0o644)})
    metadata = dict(fields, archive=ARCHIVE_NAME, sha256=hashlib.sha256(archive).hexdigest(), size=len(archive))
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / ARCHIVE_NAME).write_bytes(archive)
    (output_dir / METADATA_NAME).write_text(json.dumps(metadata, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return metadata


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sha", required=True, help="commit being released (40 hex)")
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--hstscan-binary", type=Path, help="prebuilt hstscan instead of the docker build")
    args = parser.parse_args(argv)
    if not SHA_RE.fullmatch(args.sha):
        parser.error("--sha must be 40 lowercase hex characters")
    try:
        metadata = build(args.repo, args.sha, args.output_dir, args.hstscan_binary)
    except (BuildError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(metadata, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
