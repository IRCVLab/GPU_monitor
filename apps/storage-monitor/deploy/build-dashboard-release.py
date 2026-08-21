#!/usr/bin/env python3
"""Build a deterministic central dashboard runtime release artifact."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
from pathlib import PurePosixPath
import re
import subprocess
import sys
import tarfile
from pathlib import Path


APP_ROOT = "apps/storage-monitor"
ARCHIVE_ROOT = "storage-monitor"
FORMAT_VERSION = 1
SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")

INCLUDED_PATHS = tuple(sorted({
    "collector/__init__.py",
    "collector/inventory.py",
    "collector/jobs.py",
    "collector/service.py",
    "collector/snapshot.py",
    "collector/store.py",
    "collector/transport.py",
    "config/servers.example.yaml",
    "deploy/direct_proxy.py",
    "docs/schema-v1.md",
    "viewer/app.js",
    "viewer/data-client.js",
    "viewer/debug.html",
    "viewer/echarts.min.js",
    "viewer/index.html",
    "viewer/overview.js",
    "viewer/selection.js",
    "viewer/serve.py",
    "viewer/styles.css",
    "viewer/tables.js",
    "viewer/treemap.js",
    "viewer/users-chart.js",
}))


class ReleaseError(Exception):
    """A closed-fail release validation error."""


def _run_git(args: list[str], *, cwd: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=check,
    )


def _validate_relative_path(path: str) -> None:
    pure = PurePosixPath(path)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise ReleaseError(f"unsafe release path: {path!r}")


def _ensure_clean_head(repo: Path, requested_sha: str) -> None:
    head = _run_git(["rev-parse", "HEAD"], cwd=repo).stdout.strip()
    if requested_sha != head:
        raise ReleaseError(f"requested --sha does not match git HEAD ({head})")
    status = _run_git(["status", "--porcelain=v1", "--untracked-files=all"], cwd=repo).stdout
    if status:
        raise ReleaseError("git index and worktree must be clean before building a release")


def _load_tracked_file(repo: Path, sha: str, rel_path: str) -> tuple[bytes, int]:
    _validate_relative_path(rel_path)
    source_path = f"{APP_ROOT}/{rel_path}"
    listing = _run_git(["ls-tree", sha, "--", source_path], cwd=repo).stdout.strip()
    if not listing:
        raise ReleaseError(f"required tracked release file is missing: {source_path}")
    # Example: "100644 blob <object>\tapps/storage-monitor/viewer/app.js"
    meta, listed_path = listing.split("\t", 1)
    mode, object_type, _object_id = meta.split(" ", 2)
    if listed_path != source_path:
        raise ReleaseError(f"git returned an unexpected path for {source_path}")
    if object_type != "blob":
        raise ReleaseError(f"required release path is not a file: {source_path}")
    if mode == "120000":
        raise ReleaseError(f"required release path is a symlink: {source_path}")
    if mode not in {"100644", "100755"}:
        raise ReleaseError(f"unsupported git file mode {mode} for {source_path}")
    # Capture bytes directly so minified/browser assets are packaged exactly as stored by git.
    raw = subprocess.run(
        ["git", "show", f"{sha}:{source_path}"],
        cwd=repo,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    ).stdout
    return raw, (0o755 if mode == "100755" else 0o644)


def _manifest(source_sha: str, files: dict[str, bytes]) -> bytes:
    payload = {
        "artifact_format_version": FORMAT_VERSION,
        "source_sha": source_sha,
        "included_paths": sorted(files),
        "files": {path: hashlib.sha256(files[path]).hexdigest() for path in sorted(files)},
    }
    return (json.dumps(payload, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _tar_info(name: str, size: int, mode: int) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.size = size
    info.mtime = 0
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mode = mode
    return info


def _archive_bytes(source_sha: str, files: dict[str, bytes], modes: dict[str, int]) -> bytes:
    members: list[tuple[str, bytes, int]] = [
        (f"{ARCHIVE_ROOT}/RELEASE-MANIFEST.json", _manifest(source_sha, files), 0o644)
    ]
    members.extend((f"{ARCHIVE_ROOT}/{path}", files[path], modes[path]) for path in sorted(files))
    members.sort(key=lambda item: item[0])

    buffer = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=buffer, mtime=0) as gz:
        with tarfile.open(fileobj=gz, mode="w", format=tarfile.PAX_FORMAT) as tar:
            for name, data, mode in members:
                _validate_relative_path(name)
                tar.addfile(_tar_info(name, len(data), mode), io.BytesIO(data))
    return buffer.getvalue()


def build(repo: Path, source_sha: str, output_dir: Path) -> tuple[Path, Path]:
    _ensure_clean_head(repo, source_sha)
    files: dict[str, bytes] = {}
    modes: dict[str, int] = {}
    for rel_path in INCLUDED_PATHS:
        data, mode = _load_tracked_file(repo, source_sha, rel_path)
        files[rel_path] = data
        modes[rel_path] = mode

    output_dir.mkdir(parents=True, exist_ok=True)
    archive_name = f"storage-monitor-dashboard-{source_sha}.tar.gz"
    archive_path = output_dir / archive_name
    archive_data = _archive_bytes(source_sha, files, modes)
    archive_path.write_bytes(archive_data)

    metadata = {
        "artifact_format_version": FORMAT_VERSION,
        "archive": archive_name,
        "sha256": hashlib.sha256(archive_data).hexdigest(),
        "source_sha": source_sha,
    }
    metadata_path = output_dir / f"storage-monitor-dashboard-{source_sha}.sha256.json"
    metadata_path.write_text(json.dumps(metadata, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return archive_path, metadata_path


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sha", required=True, help="exact 40-character git commit SHA to package")
    parser.add_argument("--output-dir", required=True, type=Path, help="directory for release artifact outputs")
    args = parser.parse_args(argv)
    if not SHA_RE.fullmatch(args.sha):
        parser.error("--sha must be exactly 40 hexadecimal characters")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    repo = Path.cwd()
    try:
        archive_path, metadata_path = build(repo, args.sha, args.output_dir)
    except (ReleaseError, subprocess.CalledProcessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(archive_path)
    print(metadata_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
