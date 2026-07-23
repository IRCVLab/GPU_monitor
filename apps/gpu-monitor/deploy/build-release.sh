#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'USAGE'
Usage: apps/gpu-monitor/deploy/build-release.sh --sha <40-lowercase-hex-head-sha> --output-dir <dir>
USAGE
}

fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

sha=""
output_dir=""
while (($#)); do
  case "$1" in
    --sha)
      (($# >= 2)) || { usage; fail "--sha requires a value"; }
      sha="$2"; shift 2 ;;
    --output-dir)
      (($# >= 2)) || { usage; fail "--output-dir requires a value"; }
      output_dir="$2"; shift 2 ;;
    -h|--help)
      usage; exit 0 ;;
    *)
      usage; fail "unknown argument: $1" ;;
  esac
done

[[ -n "$sha" ]] || { usage; fail "--sha is required"; }
[[ -n "$output_dir" ]] || { usage; fail "--output-dir is required"; }
[[ "$sha" =~ ^[0-9a-f]{40}$ ]] || fail "sha must be exactly 40 lowercase hexadecimal characters"

script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
repo_root=$(cd "$script_dir/../../.." && pwd)
repo_root=$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$repo_root")
git_root=$(git -C "$repo_root" rev-parse --show-toplevel 2>/dev/null) || fail "builder must live inside a git checkout"
git_root=$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$git_root")
[[ "$git_root" == "$repo_root" ]] || fail "unexpected git root for builder: $git_root"
cd "$repo_root"

head_sha=$(git rev-parse HEAD) || fail "cannot resolve HEAD"
[[ "$sha" == "$head_sha" ]] || fail "requested sha must equal HEAD ($head_sha)"

# Fail closed on any tracked modifications and any nonignored untracked files.
# Ignored local runtime artifacts remain allowed because the release is built from the exact HEAD tree below.
git diff --quiet --exit-code -- . || fail "checkout must be clean before building release"
git diff --cached --quiet --exit-code -- . || fail "checkout index must be clean before building release"
untracked=$(git ls-files --others --exclude-standard)
[[ -z "$untracked" ]] || fail "checkout has nonignored untracked files; commit or remove them before building release"

required_tracked=(
  apps/gpu-monitor/backend/main.py
  apps/gpu-monitor/backend/requirements.txt
  apps/gpu-monitor/frontend/package.json
  apps/gpu-monitor/frontend/package-lock.json
  apps/gpu-monitor/frontend/server.mjs
  apps/gpu-monitor/frontend/svelte.config.js
  apps/gpu-monitor/frontend/vite.config.ts
  apps/gpu-monitor/frontend/tsconfig.json
)
for path in "${required_tracked[@]}"; do
  git ls-files --error-unmatch "$path" >/dev/null 2>&1 || fail "required tracked runtime input is missing: $path"
done

tmpdir=$(mktemp -d "${TMPDIR:-/tmp}/gpu-release-build.XXXXXX")
cleanup() { rm -rf "$tmpdir"; }
trap cleanup EXIT
source_root="$tmpdir/source"
mkdir -p "$source_root"
git archive --format=tar "$sha" apps/gpu-monitor | tar -xf - -C "$source_root"
for path in "${required_tracked[@]}"; do
  [[ -e "$source_root/$path" ]] || fail "required runtime input is absent from committed HEAD tree: $path"
done

release_now=$((16#${sha:0:12}))
cat > "$tmpdir/fixed-date.cjs" <<EOF_DATE
const fixedNow = $release_now;
Date.now = () => fixedNow;
EOF_DATE

frontend_dir="$source_root/apps/gpu-monitor/frontend"
(
  cd "$frontend_dir"
  npm ci
  NODE_OPTIONS="--require $tmpdir/fixed-date.cjs ${NODE_OPTIONS:-}" npm run check
  rm -rf build
  NODE_OPTIONS="--require $tmpdir/fixed-date.cjs ${NODE_OPTIONS:-}" npm run build
)
[[ -d "$frontend_dir/build" ]] || fail "frontend build did not produce apps/gpu-monitor/frontend/build"

mkdir -p "$output_dir"
output_dir=$(python3 -c 'import os,sys; print(os.path.abspath(sys.argv[1]))' "$output_dir")
artifact_name="gpu-monitor-$sha.tar.gz"
checksum_name="gpu-monitor-$sha.sha256"
manifest_name="release-manifest.json"
artifact_path="$output_dir/$artifact_name"
checksum_path="$output_dir/$checksum_name"
manifest_path="$output_dir/$manifest_name"

python3 - "$source_root" "$tmpdir/stage" "$output_dir" "$sha" "$artifact_name" "$artifact_path" "$checksum_path" "$manifest_path" <<'PY'
import fnmatch
import gzip
import hashlib
import json
import os
import tempfile
from pathlib import Path
import posixpath
import shutil
import stat
import sys
import tarfile

source = Path(sys.argv[1]).resolve()
stage = Path(sys.argv[2]).resolve()
outdir = Path(sys.argv[3]).resolve()
sha = sys.argv[4]
artifact_name = sys.argv[5]
artifact_path = Path(sys.argv[6]).resolve()
checksum_path = Path(sys.argv[7]).resolve()
manifest_path = Path(sys.argv[8]).resolve()
app_root = source / "apps" / "gpu-monitor"
stage_app = stage / "gpu-monitor"

EXCLUDED_NAMES = {
    ".env", ".venv", "node_modules", "__pycache__", ".pytest_cache", ".svelte-kit",
    ".mypy_cache", ".ruff_cache", "runtime-cache", "dist", "releases",
}
EXCLUDED_SUFFIXES = (".db", ".sqlite", ".sqlite3", ".pyc", ".pyo")
SECRET_PATTERNS = ("*.pem", "*.key", "*.crt", "*.p12")


def fail(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    raise SystemExit(1)


def require_under(path: Path, root: Path) -> Path:
    resolved = path.resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        fail(f"path escapes allowed root: {path}")
    return resolved


def is_excluded(rel: Path) -> bool:
    parts = rel.parts
    if any(part in EXCLUDED_NAMES or part.startswith(".env") for part in parts):
        return True
    name = parts[-1]
    if name.endswith(EXCLUDED_SUFFIXES):
        return True
    return any(fnmatch.fnmatch(name, pattern) for pattern in SECRET_PATTERNS)


def copy_file(src: Path, dest: Path, mode=None) -> None:
    if src.is_symlink():
        fail(f"symlink runtime input is not allowed: {src.relative_to(source)}")
    require_under(src, source)
    if not src.is_file():
        fail(f"runtime input is missing: {src.relative_to(source)}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dest)
    os.chmod(dest, mode if mode is not None else (src.stat().st_mode & 0o777))


def copy_tree(src: Path, dest: Path, *, include) -> None:
    root = require_under(src, source)
    if not root.is_dir():
        fail(f"runtime directory is missing: {src.relative_to(source)}")
    for current, dirs, files in os.walk(root, topdown=True):
        cur = Path(current)
        rel_dir = cur.relative_to(root)
        kept_dirs = []
        for dirname in sorted(dirs):
            drel = rel_dir / dirname
            dpath = cur / dirname
            if dpath.is_symlink():
                fail(f"symlink runtime directory is not allowed: {dpath.relative_to(source)}")
            if is_excluded(drel):
                continue
            kept_dirs.append(dirname)
        dirs[:] = kept_dirs
        for filename in sorted(files):
            fpath = cur / filename
            frel = rel_dir / filename
            if is_excluded(frel):
                continue
            if include(frel):
                copy_file(fpath, dest / frel)

if stage.exists():
    shutil.rmtree(stage)
stage_app.mkdir(parents=True)

# Explicit runtime allowlist: backend Python source/locked requirements, frontend package lock,
# static config needed by adapter-node runtime, and the freshly generated Svelte build.
copy_tree(app_root / "backend", stage_app / "backend", include=lambda rel: rel.suffix == ".py" and rel.parts[:1] != ("tests",) or rel.name == "requirements.txt")
for rel in [
    Path("frontend/package.json"),
    Path("frontend/package-lock.json"),
    Path("frontend/server.mjs"),
]:
    copy_file(app_root / rel, stage_app / rel)
copy_tree(app_root / "frontend" / "build", stage_app / "frontend" / "build", include=lambda rel: True)

# Final leakage scan over the staged tree. This catches future allowlist mistakes before writing partial artifacts.
for path in stage_app.rglob("*"):
    rel = path.relative_to(stage_app)
    if path.is_symlink():
        fail(f"staged release contains symlink: {rel}")
    if is_excluded(rel):
        fail(f"staged release contains excluded runtime/generated/secret path: {rel}")

outdir.mkdir(parents=True, exist_ok=True)
for path in (artifact_path, checksum_path, manifest_path):
    if path.exists() and path.is_dir():
        fail(f"refusing to overwrite directory: {path}")

def make_output_temp(final_path: Path) -> Path:
    fd, name = tempfile.mkstemp(prefix=f".{final_path.name}.", suffix=".tmp", dir=str(outdir))
    os.close(fd)
    return Path(name)

tmp_artifact = make_output_temp(artifact_path)
tmp_checksum = make_output_temp(checksum_path)
tmp_manifest = make_output_temp(manifest_path)
temps = [tmp_artifact, tmp_checksum, tmp_manifest]
try:
    entries = sorted(p for p in stage_app.rglob("*") if p.is_file())
    with open(tmp_artifact, "wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz:
            with tarfile.open(fileobj=gz, mode="w", format=tarfile.PAX_FORMAT) as tar:
                root_info = tarfile.TarInfo("gpu-monitor")
                root_info.type = tarfile.DIRTYPE
                root_info.mode = 0o755
                root_info.uid = root_info.gid = 0
                root_info.uname = root_info.gname = "root"
                root_info.mtime = 0
                tar.addfile(root_info)
                dirs_added = {Path(".")}
                for file_path in entries:
                    rel = file_path.relative_to(stage_app)
                    for parent in reversed(rel.parents):
                        if parent == Path(".") or parent in dirs_added:
                            continue
                        arcdir = posixpath.join("gpu-monitor", *parent.parts)
                        info = tarfile.TarInfo(arcdir)
                        info.type = tarfile.DIRTYPE
                        info.mode = 0o755
                        info.uid = info.gid = 0
                        info.uname = info.gname = "root"
                        info.mtime = 0
                        tar.addfile(info)
                        dirs_added.add(parent)
                    arcname = posixpath.join("gpu-monitor", *rel.parts)
                    info = tar.gettarinfo(str(file_path), arcname=arcname)
                    info.uid = info.gid = 0
                    info.uname = info.gname = "root"
                    info.mtime = 0
                    info.mode = 0o755 if (file_path.stat().st_mode & stat.S_IXUSR) else 0o644
                    with open(file_path, "rb") as fh:
                        tar.addfile(info, fh)

    digest = hashlib.sha256(tmp_artifact.read_bytes()).hexdigest()
    manifest = {
        "application": "gpu-monitor",
        "git_sha": sha,
        "artifact": artifact_name,
        "sha256": digest,
        "schema": 1,
    }
    tmp_manifest.write_text(json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    tmp_checksum.write_text(f"{digest}  {artifact_path}\n", encoding="utf-8")

    # Ensure no partial output escapes on failures above; publish atomically within output dir.
    # Existing outputs are accepted only when they are byte-identical to the newly computed release.
    for existing, candidate in ((artifact_path, tmp_artifact), (checksum_path, tmp_checksum), (manifest_path, tmp_manifest)):
        if existing.exists() and existing.read_bytes() != candidate.read_bytes():
            fail(f"refusing to overwrite different existing output: {existing}")
    os.replace(tmp_artifact, artifact_path)
    os.replace(tmp_checksum, checksum_path)
    os.replace(tmp_manifest, manifest_path)
    temps.clear()
finally:
    for temp_path in temps:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass
PY

[[ -s "$artifact_path" ]] || fail "artifact output is missing or empty"
[[ -s "$checksum_path" ]] || fail "checksum output is missing or empty"
[[ -s "$manifest_path" ]] || fail "manifest output is missing or empty"
printf 'Built %s\n' "$artifact_path"
