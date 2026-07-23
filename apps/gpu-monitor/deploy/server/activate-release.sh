#!/bin/bash -p
set -euo pipefail

fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

PRODUCTION_PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
PRODUCTION_PYTHON=/usr/bin/python3
PRODUCTION_MAX_ARCHIVE_FILES=10000
PRODUCTION_MAX_EXPANDED_BYTES=2147483648
test_mode=false

if [[ "${1:-}" == --test-mode ]]; then
  test_mode=true
  [[ $# -ge 3 ]] || fail "usage: activate-release.sh --test-mode <dev|live> <action> [sha] [sha256]"
  env_name=$2
  action=$3
  sha=${4:-}
  digest=${5:-}
  argument_count=$#
  prefix=${PREFIX:-}
  command_path=${GPU_MONITOR_TEST_PATH:-$PRODUCTION_PATH}
  internal_python=${GPU_MONITOR_INTERNAL_PYTHON:-$PRODUCTION_PYTHON}
  max_archive_files=${GPU_MONITOR_MAX_ARCHIVE_FILES:-$PRODUCTION_MAX_ARCHIVE_FILES}
  max_expanded_bytes=${GPU_MONITOR_MAX_EXPANDED_BYTES:-$PRODUCTION_MAX_EXPANDED_BYTES}
  health_retries=${GPU_MONITOR_HEALTH_RETRIES:-5}
  health_sleep_seconds=${GPU_MONITOR_HEALTH_SLEEP_SECONDS:-2}
  case "$action:$argument_count" in
    status:3|rollback:3|activate:5|activate-inner:5|rollback-inner:3) ;;
    *) fail "invalid test-mode activation arguments" ;;
  esac
else
  [[ $# -ge 2 ]] || fail "usage: activate-release.sh <action> <dev|live> [sha] [sha256]"
  action=$1
  env_name=$2
  sha=${3:-}
  digest=${4:-}
  prefix=""
  command_path=$PRODUCTION_PATH
  internal_python=$PRODUCTION_PYTHON
  max_archive_files=$PRODUCTION_MAX_ARCHIVE_FILES
  max_expanded_bytes=$PRODUCTION_MAX_EXPANDED_BYTES
  health_retries=5
  health_sleep_seconds=2
  case "$action:$#" in
    status:2|rollback:2|activate:4|activate-inner:4|rollback-inner:2) ;;
    *) fail "invalid production activation arguments" ;;
  esac
fi

case "$action" in activate|rollback|status|activate-inner|rollback-inner) ;; *) fail "invalid action" ;; esac
case "$env_name" in dev|live) ;; *) fail "invalid environment" ;; esac
if [[ "$action" == activate || "$action" == activate-inner ]]; then
  [[ "$sha" =~ ^[0-9a-f]{40}$ ]] || fail "invalid sha"
  [[ "$digest" =~ ^[0-9a-f]{64}$ ]] || fail "invalid sha256"
fi

unset \
  BASH_ENV ENV CDPATH GLOBIGNORE PREFIX GPU_MONITOR_ALLOWED_ENV \
  GPU_MONITOR_TEST_PATH GPU_MONITOR_MAX_UPLOAD_BYTES \
  GPU_MONITOR_INTERNAL_PYTHON GPU_MONITOR_MAX_ARCHIVE_FILES \
  GPU_MONITOR_MAX_EXPANDED_BYTES GPU_MONITOR_HEALTH_RETRIES \
  GPU_MONITOR_HEALTH_SLEEP_SECONDS PYTHONHOME PYTHONPATH \
  PYTHONSTARTUP NODE_OPTIONS
IFS=$' \t\n'
PATH=$command_path
export PATH

validate_positive_bound() {
  local name=$1 value=$2 maximum=$3
  [[ "$value" =~ ^[0-9]+$ ]] && (( value >= 1 && value <= maximum )) ||
    fail "$name must be a positive integer no greater than $maximum"
}

if [[ "$test_mode" == true ]]; then
  [[ "$prefix" == /* && "$prefix" != / && "$prefix" != *$'\n'* ]] ||
    fail "test PREFIX must be an absolute non-root path"
  [[ "$internal_python" == /* && -x "$internal_python" ]] ||
    fail "test internal Python must be an executable absolute path"
  validate_positive_bound "test archive file bound" "$max_archive_files" "$PRODUCTION_MAX_ARCHIVE_FILES"
  validate_positive_bound "test expanded byte bound" "$max_expanded_bytes" "$PRODUCTION_MAX_EXPANDED_BYTES"
  validate_positive_bound "test health retry bound" "$health_retries" 20
  validate_positive_bound "test health sleep bound" "$health_sleep_seconds" 60
  IFS=: read -r -a test_path_parts <<< "$command_path"
  for test_path_part in "${test_path_parts[@]}"; do
    [[ "$test_path_part" == /* && -d "$test_path_part" ]] ||
      fail "test PATH entries must be existing absolute directories"
  done
  IFS=$' \t\n'
  export \
    PREFIX="$prefix" \
    GPU_MONITOR_TEST_PATH="$command_path" \
    GPU_MONITOR_INTERNAL_PYTHON="$internal_python" \
    GPU_MONITOR_MAX_ARCHIVE_FILES="$max_archive_files" \
    GPU_MONITOR_MAX_EXPANDED_BYTES="$max_expanded_bytes" \
    GPU_MONITOR_HEALTH_RETRIES="$health_retries" \
    GPU_MONITOR_HEALTH_SLEEP_SECONDS="$health_sleep_seconds"
else
  [[ -x "$PRODUCTION_PYTHON" ]] || fail "required internal Python is unavailable"
fi

INTERNAL_PYTHON=$internal_python
if [[ "$test_mode" == true ]]; then
  VENV_PYTHON=python3
else
  VENV_PYTHON=$PRODUCTION_PYTHON
fi
base="${prefix}/srv/gpu-monitor/${env_name}"
releases="$base/releases"
incoming="$base/incoming"
locks="${prefix}/var/lock/gpu-monitor"
state="$base/deployments.jsonl"
lock_file="$locks/${env_name}.lock"
script_dir=${BASH_SOURCE[0]%/*}
[[ "$script_dir" != "${BASH_SOURCE[0]}" ]] || script_dir=.
script_dir=$(cd -- "$script_dir" && /bin/pwd -P)

if [[ "$action" != status ]]; then
  mkdir -p "$releases" "$incoming" "$locks" "$base/tmp"
fi

append_state() {
  local status=$1 release_sha=${2:-} release_digest=${3:-} message=${4:-}
  "$INTERNAL_PYTHON" - "$state" "$env_name" "$status" "$release_sha" "$release_digest" "$message" <<'PY'
import json
import os
import sys
import time

path, environment, status, sha, digest, message = sys.argv[1:]
line = json.dumps({
    "ts": int(time.time()),
    "environment": environment,
    "status": status,
    "sha": sha,
    "sha256": digest,
    "message": message,
}, sort_keys=True, separators=(",", ":")) + "\n"
fd = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
with os.fdopen(fd, "a", encoding="utf-8") as handle:
    handle.write(line)
    handle.flush()
    os.fsync(handle.fileno())
PY
}

atomic_link() {
  local target=$1 link=$2 tmp_link
  tmp_link="${link}.tmp.$$"
  rm -f "$tmp_link"
  ln -s "$target" "$tmp_link"
  "$INTERNAL_PYTHON" - "$tmp_link" "$link" <<'PY'
import os
import sys
os.replace(sys.argv[1], sys.argv[2])
PY
}

restore_link() {
  local target=$1 link=$2
  if [[ -n "$target" ]]; then
    atomic_link "$target" "$link"
  else
    rm -f "$link"
  fi
}

current_target() { [[ -L "$base/current" ]] && readlink "$base/current" || true; }
previous_target() { [[ -L "$base/previous" ]] && readlink "$base/previous" || true; }

restart_units() {
  local selected_env=$1
  systemctl restart "gpu-monitor-backend@${selected_env}.service" "gpu-monitor-frontend@${selected_env}.service"
  if [[ "$selected_env" == live ]]; then
    systemctl restart "gpu-monitor-bridge@${selected_env}.service"
  fi
}

run_health() {
  if [[ "$test_mode" == true ]]; then
    "$script_dir/health-check.sh" --test-mode "$env_name"
  else
    "$script_dir/health-check.sh" "$env_name"
  fi
}

validate_and_extract() {
  local artifact=$1 dest=$2 expected_sha=$3 expected_digest=$4
  "$INTERNAL_PYTHON" - \
    "$artifact" "$dest" "$expected_sha" "$expected_digest" \
    "$max_archive_files" "$max_expanded_bytes" <<'PY'
import hashlib
import json
import shutil
import stat
import sys
import tarfile
from pathlib import Path, PurePosixPath

artifact = Path(sys.argv[1])
dest = Path(sys.argv[2])
sha = sys.argv[3]
expected = sys.argv[4]
max_files = int(sys.argv[5])
max_expanded = int(sys.argv[6])

def fail(msg):
    print(f"ERROR: {msg}", file=sys.stderr)
    raise SystemExit(1)

hasher = hashlib.sha256()
with artifact.open("rb") as handle:
    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
        hasher.update(chunk)
digest = hasher.hexdigest()
if digest != expected:
    fail("artifact digest mismatch")
if dest.exists():
    fail("temporary extraction destination already exists")
dest.mkdir(parents=True)
root = dest / "root"
root.mkdir()
count = 0
expanded = 0
with tarfile.open(artifact, "r:gz") as tar:
    members = tar.getmembers()
    for member in members:
        count += 1
        if count > max_files:
            fail("archive contains too many entries")
        name = member.name
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts:
            fail(f"unsafe archive path: {name}")
        if not path.parts or path.parts[0] != "gpu-monitor":
            fail(f"invalid archive root: {name}")
        mode = member.mode or 0
        if mode & (stat.S_ISUID | stat.S_ISGID):
            fail(f"setuid/setgid archive entry rejected: {name}")
        if member.issym() or member.islnk() or member.isdev() or member.isfifo():
            fail(f"unsafe archive entry type rejected: {name}")
        if member.isfile():
            expanded += member.size
            if expanded > max_expanded:
                fail("archive expanded size exceeds bound")
    tar.extractall(root, members=members)
app_root = root / "gpu-monitor"
if not app_root.is_dir():
    fail("archive missing gpu-monitor root")
required = [
    "backend/main.py",
    "backend/slack_bridge.py",
    "backend/requirements.txt",
    "frontend/package.json",
    "frontend/package-lock.json",
    "frontend/build/index.js",
]
for relative in required:
    if not (app_root / relative).is_file():
        fail(f"archive missing required runtime file: {relative}")
for child in app_root.iterdir():
    shutil.move(str(child), str(dest / child.name))
shutil.rmtree(root)
manifest = {
    "application": "gpu-monitor",
    "artifact": f"gpu-monitor-{sha}.tar.gz",
    "git_sha": sha,
    "schema": 1,
    "sha256": digest,
}
(dest / "release-manifest.json").write_text(
    json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n",
    encoding="utf-8",
)
PY
}

install_dependencies() {
  local release=$1
  "$VENV_PYTHON" -m venv "$release/.venv"
  if [[ -x "$release/.venv/bin/python" ]]; then
    "$release/.venv/bin/python" -m pip install --requirement "$release/backend/requirements.txt"
  fi
  (cd "$release/frontend" && npm ci --omit=dev)
}

validate_existing_release() {
  local release=$1 expected_sha=$2 expected_digest=$3
  "$INTERNAL_PYTHON" - "$release/release-manifest.json" "$expected_sha" "$expected_digest" <<'PY'
import json
import sys

manifest_path, sha, digest = sys.argv[1:]
try:
    with open(manifest_path, encoding="utf-8") as handle:
        manifest = json.load(handle)
except (OSError, ValueError) as exc:
    print(f"ERROR: existing release manifest is unreadable: {exc}", file=sys.stderr)
    raise SystemExit(1)
expected = {
    "application": "gpu-monitor",
    "artifact": f"gpu-monitor-{sha}.tar.gz",
    "git_sha": sha,
    "schema": 1,
    "sha256": digest,
}
if manifest != expected:
    print("ERROR: existing release does not match requested SHA and digest", file=sys.stderr)
    raise SystemExit(1)
PY
}

retain_successful_releases() {
  local keep current previous name relative preserve kept
  current=$(current_target)
  previous=$(previous_target)
  keep=$("$INTERNAL_PYTHON" - "$state" <<'PY'
import json
import sys
seen = []
try:
    lines = open(sys.argv[1], encoding="utf-8")
except FileNotFoundError:
    lines = []
for line in lines:
    try:
        data = json.loads(line)
    except Exception:
        continue
    if data.get("status") == "success" and data.get("sha") and data["sha"] not in seen:
        seen.append(data["sha"])
print("\n".join(seen[-3:]))
PY
)
  while IFS= read -r dir; do
    [[ -n "$dir" ]] || continue
    name="${dir##*/}"
    relative="releases/${name}"
    preserve=false
    [[ "$relative" == "$current" || "$relative" == "$previous" ]] && preserve=true
    while IFS= read -r kept; do
      [[ "$kept" == "$name" ]] && preserve=true
    done <<< "$keep"
    if [[ "$preserve" == false ]]; then
      chmod -R u+w "$dir" 2>/dev/null || true
      rm -rf "$dir"
    fi
  done < <(find "$releases" -mindepth 1 -maxdepth 1 -type d | LC_ALL=C sort)
}

do_status() {
  "$INTERNAL_PYTHON" - "$env_name" "$(current_target)" "$(previous_target)" "$state" <<'PY'
import json
import sys
print(json.dumps({
    "environment": sys.argv[1],
    "current": sys.argv[2],
    "previous": sys.argv[3],
    "state": sys.argv[4],
}, sort_keys=True, separators=(",", ":")))
PY
}

do_rollback() {
  local current previous
  previous=$(previous_target)
  [[ -n "$previous" && -d "$base/$previous" ]] || fail "no previous release to roll back to"
  current=$(current_target)
  [[ -n "$current" && -d "$base/$current" ]] || fail "current release is unavailable"
  atomic_link "$current" "$base/previous"
  atomic_link "$previous" "$base/current"
  restart_units "$env_name"
  run_health
  append_state rollback "${previous##*/}" "" manual
}

do_activate() {
  local artifact release_dir tmp_dir old_current old_previous actual
  artifact="$incoming/$sha.tar.gz"
  [[ -f "$artifact" ]] || fail "incoming artifact is missing"
  actual=$("$INTERNAL_PYTHON" - "$artifact" <<'PY'
import hashlib
import sys
hasher = hashlib.sha256()
with open(sys.argv[1], "rb") as handle:
    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
        hasher.update(chunk)
print(hasher.hexdigest())
PY
)
  [[ "$actual" == "$digest" ]] || fail "incoming artifact digest mismatch"
  release_dir="$releases/$sha"
  old_current=$(current_target)
  old_previous=$(previous_target)
  if [[ ! -d "$release_dir" ]]; then
    tmp_dir="$base/tmp/release-${sha}.$$"
    rm -rf "$tmp_dir"
    if ! (validate_and_extract "$artifact" "$tmp_dir" "$sha" "$digest" && install_dependencies "$tmp_dir"); then
      chmod -R u+w "$tmp_dir" 2>/dev/null || true
      rm -rf "$tmp_dir"
      fail "release construction failed"
    fi
    mv "$tmp_dir" "$release_dir"
    chmod -R a-w "$release_dir"
  else
    validate_existing_release "$release_dir" "$sha" "$digest"
  fi
  if [[ -n "$old_current" && "$old_current" != "releases/$sha" ]]; then
    atomic_link "$old_current" "$base/previous"
  fi
  atomic_link "releases/$sha" "$base/current"
  restart_units "$env_name"
  if run_health; then
    append_state success "$sha" "$digest" activated
    retain_successful_releases
    return
  fi

  restore_link "$old_current" "$base/current"
  restore_link "$old_previous" "$base/previous"
  restart_units "$env_name" || true
  run_health || true
  append_state rollback "$sha" "$digest" health_failed
  fail "activation health failed; rolled back"
}

case "$action" in
  status)
    do_status
    ;;
  rollback)
    if [[ "$test_mode" == true ]]; then
      flock "$lock_file" "$0" --test-mode "$env_name" rollback-inner
    else
      flock "$lock_file" "$0" rollback-inner "$env_name"
    fi
    ;;
  activate)
    if [[ "$test_mode" == true ]]; then
      flock "$lock_file" "$0" --test-mode "$env_name" activate-inner "$sha" "$digest"
    else
      flock "$lock_file" "$0" activate-inner "$env_name" "$sha" "$digest"
    fi
    ;;
  rollback-inner)
    do_rollback
    ;;
  activate-inner)
    do_activate
    ;;
esac
