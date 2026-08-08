#!/bin/bash -p
set -euo pipefail

fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

PRODUCTION_PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
PRODUCTION_PYTHON=/usr/bin/python3
PRODUCTION_MAX_UPLOAD_BYTES=536870912
PRODUCTION_MAX_ARCHIVE_FILES=10000
PRODUCTION_MAX_EXPANDED_BYTES=2147483648
PRODUCTION_MAX_INCOMING_COUNT=8
PRODUCTION_MAX_INCOMING_BYTES=2147483648
PRODUCTION_TEMP_MAX_AGE=3600
PRODUCTION_FAILED_MAX_COUNT=2
test_mode=false

if [[ "${1:-}" == --test-mode ]]; then
  test_mode=true
  [[ $# -ge 3 ]] || fail "invalid test-mode arguments"
  env_name=$2; action=$3; sha=${4:-}; digest=${5:-}; argument_count=$#
  prefix=${PREFIX:-}
  command_path=${GPU_MONITOR_TEST_PATH:-$PRODUCTION_PATH}
  internal_python=${GPU_MONITOR_INTERNAL_PYTHON:-$PRODUCTION_PYTHON}
  max_upload=${GPU_MONITOR_MAX_UPLOAD_BYTES:-$PRODUCTION_MAX_UPLOAD_BYTES}
  max_archive_files=${GPU_MONITOR_MAX_ARCHIVE_FILES:-$PRODUCTION_MAX_ARCHIVE_FILES}
  max_expanded_bytes=${GPU_MONITOR_MAX_EXPANDED_BYTES:-$PRODUCTION_MAX_EXPANDED_BYTES}
  health_retries=${GPU_MONITOR_HEALTH_RETRIES:-5}
  health_sleep_seconds=${GPU_MONITOR_HEALTH_SLEEP_SECONDS:-2}
  max_incoming_count=${GPU_MONITOR_MAX_INCOMING_COUNT:-$PRODUCTION_MAX_INCOMING_COUNT}
  max_incoming_bytes=${GPU_MONITOR_MAX_INCOMING_BYTES:-$PRODUCTION_MAX_INCOMING_BYTES}
  temp_max_age=${GPU_MONITOR_UPLOAD_TEMP_MAX_AGE:-$PRODUCTION_TEMP_MAX_AGE}
  failed_max_count=${GPU_MONITOR_FAILED_ARTIFACT_MAX_COUNT:-$PRODUCTION_FAILED_MAX_COUNT}
  case "$action:$argument_count" in
    status:3|status-inner:3|rollback:3|rollback-inner:3|upload:5|upload-inner:5|discard:5|discard-inner:5|activate:5|activate-inner:5) ;;
    *) fail "invalid test-mode arguments" ;;
  esac
else
  [[ $# -ge 2 ]] || fail "invalid production arguments"
  action=$1; env_name=$2; sha=${3:-}; digest=${4:-}; argument_count=$#
  prefix=""; command_path=$PRODUCTION_PATH; internal_python=$PRODUCTION_PYTHON
  max_upload=$PRODUCTION_MAX_UPLOAD_BYTES
  max_archive_files=$PRODUCTION_MAX_ARCHIVE_FILES
  max_expanded_bytes=$PRODUCTION_MAX_EXPANDED_BYTES
  health_retries=5; health_sleep_seconds=2
  max_incoming_count=$PRODUCTION_MAX_INCOMING_COUNT
  max_incoming_bytes=$PRODUCTION_MAX_INCOMING_BYTES
  temp_max_age=$PRODUCTION_TEMP_MAX_AGE
  failed_max_count=$PRODUCTION_FAILED_MAX_COUNT
  case "$action:$argument_count" in
    status:2|status-inner:2|rollback:2|rollback-inner:2|upload:4|upload-inner:4|discard:4|discard-inner:4|activate:4|activate-inner:4) ;;
    *) fail "invalid production arguments" ;;
  esac
fi

case "$env_name" in dev|live) ;; *) fail "invalid environment" ;; esac
case "$action" in status|status-inner|upload|upload-inner|discard|discard-inner|activate|activate-inner|rollback|rollback-inner) ;; *) fail "invalid action" ;; esac
case "$action" in
  upload|upload-inner|discard|discard-inner|activate|activate-inner)
    [[ "$sha" =~ ^[0-9a-f]{40}$ ]] || fail "invalid sha"
    [[ "$digest" =~ ^[0-9a-f]{64}$ ]] || fail "invalid sha256"
    ;;
esac

if [[ "$test_mode" != true ]]; then
  expected_user="gpu-deploy-$env_name"
  effective_user=$(/usr/bin/id -un) || fail "unable to determine effective username"
  [[ "$effective_user" == "$expected_user" ]] ||
    fail "production activation requires effective user $expected_user"
fi

unset BASH_ENV ENV CDPATH GLOBIGNORE PREFIX GPU_MONITOR_ALLOWED_ENV \
  GPU_MONITOR_TEST_PATH GPU_MONITOR_MAX_UPLOAD_BYTES GPU_MONITOR_INTERNAL_PYTHON \
  GPU_MONITOR_MAX_ARCHIVE_FILES GPU_MONITOR_MAX_EXPANDED_BYTES \
  GPU_MONITOR_HEALTH_RETRIES GPU_MONITOR_HEALTH_SLEEP_SECONDS \
  GPU_MONITOR_MAX_INCOMING_COUNT GPU_MONITOR_MAX_INCOMING_BYTES \
  GPU_MONITOR_UPLOAD_TEMP_MAX_AGE GPU_MONITOR_FAILED_ARTIFACT_MAX_COUNT \
  PYTHONHOME PYTHONPATH PYTHONSTARTUP NODE_OPTIONS
IFS=$' \t\n'
PATH=$command_path
export PATH

validate_test_prefix() {
  local raw=$1 py=$2
  [[ "$raw" == /* && "$raw" != / && "$raw" != *$'\n'* ]] || return 1
  "$py" - "$raw" <<'PY'
import os, pathlib, sys
raw = sys.argv[1]
path = pathlib.Path(raw)
if os.path.normpath(raw) != raw:
    raise SystemExit(1)
existing = path
while not existing.exists():
    if existing.parent == existing:
        raise SystemExit(1)
    existing = existing.parent
if existing.stat().st_uid != os.getuid():
    raise SystemExit(1)
cursor = pathlib.Path('/')
for part in path.parts[1:]:
    cursor = cursor / part
    if cursor.exists() and cursor.is_symlink():
        raise SystemExit(1)
if str(path.resolve(strict=False)) != raw:
    raise SystemExit(1)
PY
}

validate_bound() {
  local name=$1 value=$2 maximum=$3
  [[ "$value" =~ ^[0-9]+$ ]] && (( value >= 1 && value <= maximum )) ||
    fail "$name must be a bounded positive integer"
}
if [[ "$test_mode" == true ]]; then
  [[ "$internal_python" == /* && -x "$internal_python" ]] || fail "invalid internal Python"
  validate_test_prefix "$prefix" "$internal_python" || fail "invalid test PREFIX"
  validate_bound upload "$max_upload" "$PRODUCTION_MAX_UPLOAD_BYTES"
  validate_bound archive-files "$max_archive_files" "$PRODUCTION_MAX_ARCHIVE_FILES"
  validate_bound expanded-bytes "$max_expanded_bytes" "$PRODUCTION_MAX_EXPANDED_BYTES"
  validate_bound health-retries "$health_retries" 20
  validate_bound health-sleep "$health_sleep_seconds" 60
  validate_bound incoming-count "$max_incoming_count" 100
  validate_bound incoming-bytes "$max_incoming_bytes" 8589934592
  validate_bound temp-age "$temp_max_age" 604800
  validate_bound failed-count "$failed_max_count" 20
  IFS=: read -r -a path_parts <<< "$command_path"
  for part in "${path_parts[@]}"; do [[ "$part" == /* && -d "$part" ]] || fail "invalid test PATH"; done
  test_tool_dir=${path_parts[0]}
  test_timeout_command="$test_tool_dir/timeout"
  [[ -x "$test_timeout_command" ]] || test_timeout_command=
  IFS=$' \t\n'
else
  [[ -x "$PRODUCTION_PYTHON" ]] || fail "required internal Python is unavailable"
  test_tool_dir=
  test_timeout_command=
fi

INTERNAL_PYTHON=$internal_python
base="${prefix}/srv/gpu-monitor/$env_name"
releases="$base/releases"
incoming="$base/incoming"
tmp_root="$base/tmp"
state="$base/deployments.jsonl"
generations="$base/generations"
lock_dir="${prefix}/var/lock/gpu-monitor/$env_name"
lock_file="$lock_dir/activation.lock"
script_dir=${BASH_SOURCE[0]%/*}
[[ "$script_dir" != "${BASH_SOURCE[0]}" ]] || script_dir=.
script_dir=$(cd -- "$script_dir" && /bin/pwd -P)

if [[ "$test_mode" == true ]]; then
  restart_broker="$script_dir/gpu-monitor-restart-broker"
  sudo_command=sudo
else
  restart_broker=/usr/local/libexec/gpu-monitor-restart-broker
  sudo_command=/usr/bin/sudo
fi

case "$action" in
  status)
    if [[ "$test_mode" == true ]]; then
      mkdir -p "$lock_dir"
    fi
    ;;
  status-inner) ;;
  *) mkdir -p "$releases" "$incoming" "$tmp_root" "$generations" "$lock_dir" ;;
esac

case "$action" in
  activate|activate-inner)
    if [[ "$test_mode" == true ]]; then
      runtime_gid=$("$INTERNAL_PYTHON" - "$tmp_root" <<'PY'
import os, sys
print(os.stat(sys.argv[1]).st_gid)
PY
)
    else
      runtime_gid=$("$INTERNAL_PYTHON" - "$env_name" <<'PY'
import grp, sys
print(grp.getgrnam("gpu-monitor-" + sys.argv[1]).gr_gid)
PY
)
    fi
    [[ "$runtime_gid" =~ ^[0-9]+$ ]] || fail "unable to determine runtime gid"
    ;;
  *) runtime_gid=0 ;;
esac

fsync_directory() {
  local directory=$1
  [[ -d "$directory" ]] || return 0
  "$INTERNAL_PYTHON" - "$directory" <<'PY'
import os, sys
flags = os.O_RDONLY
if hasattr(os, "O_DIRECTORY"):
    flags |= os.O_DIRECTORY
fd = os.open(sys.argv[1], flags)
try:
    os.fsync(fd)
finally:
    os.close(fd)
PY
}

fsync_regular_files() {
  local root=$1
  [[ -d "$root" ]] || return 0
  "$INTERNAL_PYTHON" - "$root" <<'PY'
import os, sys
root = sys.argv[1]
for current, dirs, files in os.walk(root):
    dirs.sort(); files.sort()
    for name in files:
        path = os.path.join(current, name)
        try:
            fd = os.open(path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0))
            try: os.fsync(fd)
            finally: os.close(fd)
        except FileNotFoundError:
            pass
PY
}

fsync_tree_bottom_up() {
  local root=$1
  [[ -d "$root" ]] || return 0
  "$INTERNAL_PYTHON" - "$root" <<'PY'
import os, sys
root = sys.argv[1]
for current, dirs, files in os.walk(root, topdown=False):
    try:
        fd = os.open(current, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0))
        try: os.fsync(fd)
        finally: os.close(fd)
    except FileNotFoundError:
        pass
PY
}

canonicalize_test_prefix() {
  [[ "$test_mode" == true ]] || return 0
  "$INTERNAL_PYTHON" - "$base" <<'PY'
import os, pathlib, sys
base = pathlib.Path(sys.argv[1])
for path in (base, base.parent, base.parent.parent):
    if path.exists() and path.is_symlink():
        print("ERROR: test prefix root must be canonical and non-symlink", file=sys.stderr); raise SystemExit(1)
PY
}
canonicalize_test_prefix

case "$action" in
  status)
    fsync_directory "$lock_dir"
    ;;
  status-inner)
    ;;
  *)
    fsync_directory "$base"
    fsync_directory "$releases"
    fsync_directory "$incoming"
    fsync_directory "$tmp_root"
    fsync_directory "$generations"
    fsync_directory "$lock_dir"
    ;;
esac

append_state() {
  local status_value=$1 release_sha=${2:-} release_digest=${3:-} message=${4:-}
  "$INTERNAL_PYTHON" - "$state" "$env_name" "$status_value" "$release_sha" "$release_digest" "$message" <<'PY'
import json, os, sys, time
path, environment, status, sha, digest, message = sys.argv[1:]
line = json.dumps({"ts": int(time.time()), "environment": environment, "status": status,
                   "sha": sha, "sha256": digest, "message": message},
                  sort_keys=True, separators=(",", ":")) + "\n"
created = not os.path.exists(path)
fd = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
with os.fdopen(fd, "a", encoding="utf-8") as handle:
    handle.write(line)
    handle.flush()
    os.fsync(handle.fileno())
if created:
    directory_fd = os.open(os.path.dirname(path), os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
PY
}

atomic_link() {
  local target=$1 link=$2 temporary="${2}.tmp.$$"
  rm -f "$temporary" || return 1
  ln -s "$target" "$temporary" || return 1
  if ! "$INTERNAL_PYTHON" - "$temporary" "$link" <<'PY'
import os, sys
os.replace(sys.argv[1], sys.argv[2])
PY
  then
    if ! rm -f "$temporary"; then
      printf 'ERROR: failed to clean temporary pointer %s\n' "$temporary" >&2
    fi
    return 1
  fi
  fsync_directory "${link%/*}" || return 1
}
restore_link() {
  local target=$1 link=$2
  if [[ -n "$target" ]]; then
    atomic_link "$target" "$link" || return 1
  else
    rm -f "$link" || return 1
    fsync_directory "${link%/*}" || return 1
  fi
}
release_target_from_link() {
  local link=$1 resolved name
  [[ -e "$link" || -L "$link" ]] || return 0
  resolved=$("$INTERNAL_PYTHON" - "$link" "$releases" <<'PY'
import os, pathlib, sys
link, releases = map(pathlib.Path, sys.argv[1:])
try:
    resolved = link.resolve(strict=True)
except FileNotFoundError:
    raise SystemExit(0)
try:
    rel = resolved.relative_to(releases.resolve(strict=True))
except Exception:
    raise SystemExit(0)
if len(rel.parts) == 1:
    print("releases/" + rel.parts[0])
PY
)
  if [[ -n "$resolved" ]]; then printf '%s\n' "$resolved"; fi
  return 0
}
current_target() { release_target_from_link "$base/current"; }
previous_target() { release_target_from_link "$base/previous"; }
ensure_root_generation_links() {
  if [[ ! -L "$base/current" || "$(readlink "$base/current")" != generations/active/current ]]; then
    atomic_link generations/active/current "$base/current" || return 1
  fi
  if [[ ! -L "$base/previous" || "$(readlink "$base/previous")" != generations/active/previous ]]; then
    atomic_link generations/active/previous "$base/previous" || return 1
  fi
}
write_generation() {
  local gen_dir=$1 current=$2 previous=$3
  mkdir -p "$gen_dir" || return 1
  chmod 0700 "$gen_dir" || return 1
  ln -s "../../$current" "$gen_dir/current" || return 1
  if [[ -n "$previous" ]]; then
    ln -s "../../$previous" "$gen_dir/previous" || return 1
  fi
  chmod 0550 "$gen_dir" || return 1
  fsync_directory "$gen_dir" || return 1
}
swap_generation() {
  local current=$1 previous=${2:-} gen_name gen_dir tmp_link
  gen_name="gen-$(date +%s)-$$-$RANDOM"
  gen_dir="$generations/$gen_name"
  tmp_link="$generations/active.tmp.$$"
  write_generation "$gen_dir" "$current" "$previous" || return 1
  rm -f "$tmp_link" || return 1
  ln -s "$gen_name" "$tmp_link" || return 1
  if ! "$INTERNAL_PYTHON" - "$tmp_link" "$generations/active" <<'PY'
import os, sys
os.replace(sys.argv[1], sys.argv[2])
PY
  then
    if ! rm -f "$tmp_link"; then
      printf 'ERROR: failed to clean temporary generation pointer %s\n' "$tmp_link" >&2
    fi
    return 1
  fi
  fsync_directory "$generations" || return 1
  ensure_root_generation_links || return 1
}

restart_units() {
  local env=$env_name
  if [[ "$test_mode" == true ]]; then
    GPU_MONITOR_TEST_PATH="$PATH" "$sudo_command" -n "$restart_broker" --test-mode "$env_name"
  else
    /usr/bin/sudo -n "$restart_broker" "$env"
  fi
}

stop_units() {
  local env=$env_name
  if [[ "$test_mode" == true ]]; then
    GPU_MONITOR_TEST_PATH="$PATH" "$sudo_command" -n "$restart_broker" --test-mode stop "$env_name"
  else
    /usr/bin/sudo -n "$restart_broker" stop "$env"
  fi
}

run_health() {
  if [[ "$test_mode" == true ]]; then
    env -i PATH="$PATH" PREFIX="$prefix" GPU_MONITOR_TEST_PATH="$PATH" \
      GPU_MONITOR_HEALTH_RETRIES="$health_retries" GPU_MONITOR_HEALTH_SLEEP_SECONDS="$health_sleep_seconds" \
      "$script_dir/health-check.sh" --test-mode "$env_name"
  else
    "$script_dir/health-check.sh" "$env_name"
  fi
}

prune_temps() {
  find "$tmp_root" -mindepth 1 -maxdepth 1 -name 'upload-*' -type f -mmin "+$(( (temp_max_age + 59) / 60 ))" -delete 2>/dev/null || true
  fsync_directory "$tmp_root"
}

incoming_usage() {
  "$INTERNAL_PYTHON" - "$incoming" <<'PY'
import os, sys
count = total = 0
for root, _, files in os.walk(sys.argv[1]):
    for name in files:
        if name.endswith(".tar.gz"):
            path = os.path.join(root, name)
            count += 1
            total += os.path.getsize(path)
print(count, total)
PY
}

do_upload() {
  local temp object_dir final actual_size usage_count usage_bytes existing
  prune_temps
  if [[ -d "$releases/$sha" ]]; then
    validate_existing_release "$releases/$sha"
  fi
  temp="$tmp_root/upload-${sha}-${digest}.$$"
  rm -f "$temp"
  if ! "$INTERNAL_PYTHON" -c 'import hashlib, os, sys
path, expected, maximum = sys.argv[1], sys.argv[2], int(sys.argv[3])
hasher = hashlib.sha256(); total = 0
try:
    with open(path, "xb") as output:
        while True:
            chunk = sys.stdin.buffer.read(1024 * 1024)
            if not chunk: break
            total += len(chunk)
            if total > maximum: raise RuntimeError("object exceeds limit")
            hasher.update(chunk); output.write(chunk)
        output.flush(); os.fsync(output.fileno())
    if hasher.hexdigest() != expected: raise RuntimeError("digest mismatch")
except BaseException:
    try: os.unlink(path)
    except FileNotFoundError: pass
    raise
' "$temp" "$digest" "$max_upload"
  then
    rm -f "$temp"
    fsync_directory "$tmp_root"
    fail "upload failed size or digest verification"
  fi
  object_dir="$incoming/$sha"
  final="$object_dir/$digest.tar.gz"
  existing=$(find "$object_dir" -maxdepth 1 -name '*.tar.gz' -type f 2>/dev/null | head -1 || true)
  if [[ -n "$existing" && "$existing" != "$final" ]]; then rm -f "$temp"; fsync_directory "$tmp_root"; fail "conflicting SHA/digest"; fi
  if [[ -f "$final" ]]; then rm -f "$temp"; fsync_directory "$tmp_root"; printf '{"environment":"%s","sha":"%s","sha256":"%s","status":"uploaded"}\n' "$env_name" "$sha" "$digest"; return; fi
  read -r usage_count usage_bytes <<< "$(incoming_usage)"
  actual_size=$(wc -c < "$temp" | tr -d ' ')
  (( usage_count + 1 <= max_incoming_count )) || { rm -f "$temp"; fsync_directory "$tmp_root"; fail "incoming count quota exceeded"; }
  (( usage_bytes + actual_size <= max_incoming_bytes )) || { rm -f "$temp"; fsync_directory "$tmp_root"; fail "incoming byte quota exceeded"; }
  mkdir -p "$object_dir"
  fsync_directory "$incoming"
  "$INTERNAL_PYTHON" - "$temp" "$final" "$object_dir" <<'PY'
import os, sys
os.replace(sys.argv[1], sys.argv[2])
directory_fd = os.open(sys.argv[3], os.O_RDONLY | os.O_DIRECTORY)
try: os.fsync(directory_fd)
finally: os.close(directory_fd)
PY
  fsync_directory "$tmp_root"
  printf '{"environment":"%s","sha":"%s","sha256":"%s","status":"uploaded"}\n' "$env_name" "$sha" "$digest"
}

do_discard() {
  local object_dir="$incoming/$sha" artifact="$incoming/$sha/$digest.tar.gz"
  if [[ -d "$object_dir" ]]; then
    rm -f -- "$artifact" "${artifact}.failed"
    fsync_directory "$object_dir"
    rmdir "$object_dir" 2>/dev/null || true
  fi
  fsync_directory "$incoming"
  printf '{"environment":"%s","sha":"%s","sha256":"%s","status":"discarded"}\n' "$env_name" "$sha" "$digest"
}

validate_and_extract() {
  local object_dir=$1 artifact_name=$2 destination=$3
  "$INTERNAL_PYTHON" - "$object_dir" "$artifact_name" "$destination" "$sha" "$digest" "$max_archive_files" "$max_expanded_bytes" "$runtime_gid" "$test_mode" <<'PY'
import hashlib, json, os, shutil, stat, sys, tarfile
from pathlib import Path, PurePosixPath
object_dir_path, artifact_name, destination_path, sha, expected, max_entries, max_bytes, expected_gid, test_mode = sys.argv[1:]
destination = Path(destination_path); max_entries = int(max_entries); max_bytes = int(max_bytes); expected_gid = int(expected_gid)
def reject(message):
    print("ERROR: " + message, file=sys.stderr); raise SystemExit(1)
def make_staging_directory(path):
    try:
        relative = path.relative_to(destination)
    except ValueError:
        reject("staging directory escaped destination")
    cursor = destination
    for part in relative.parts:
        cursor /= part
        try:
            os.mkdir(cursor, 0o2700)
        except FileExistsError:
            if not cursor.is_dir(): reject("staging path is not a directory")
        metadata = os.lstat(cursor)
        actual_gid = metadata.st_gid
        if actual_gid != expected_gid:
            reject(f"staging directory did not inherit runtime group: {cursor}: gid {actual_gid}, expected {expected_gid}")
        if test_mode != "true" and not metadata.st_mode & stat.S_ISGID:
            reject(f"staging directory lost setgid inheritance: {cursor}")
if destination.exists(): reject("temporary destination exists")
destination.mkdir(parents=True, mode=0o2700)
destination_metadata = os.lstat(destination)
if destination_metadata.st_gid != expected_gid: reject("temporary destination did not inherit runtime group")
if test_mode != "true" and not destination_metadata.st_mode & stat.S_ISGID:
    reject("temporary destination lost setgid inheritance")
seen = {}; count = total = 0
object_dir_fd = os.open(object_dir_path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0))
try:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0)
    artifact_fd = os.open(artifact_name, flags, dir_fd=object_dir_fd)
    st = os.fstat(artifact_fd)
    if not stat.S_ISREG(st.st_mode): reject("incoming artifact is not a regular file")
    artifact_file = os.fdopen(artifact_fd, "rb")
finally:
    os.close(object_dir_fd)
with artifact_file:
    hasher = hashlib.sha256()
    for chunk in iter(lambda: artifact_file.read(1024 * 1024), b""): hasher.update(chunk)
    if hasher.hexdigest() != expected: reject("artifact digest mismatch")
    artifact_file.seek(0)
    with tarfile.open(fileobj=artifact_file, mode="r|gz") as archive:
        for member in archive:
            count += 1
            if count > max_entries: reject("archive entry count exceeds bound")
            path = PurePosixPath(member.name)
            if not path.parts or path.is_absolute() or ".." in path.parts or path.parts[0] != "gpu-monitor":
                reject("unsafe archive path")
            normalized = "/".join(path.parts)
            if normalized in seen: reject("duplicate archive path")
            for parent in path.parents:
                parent_name = "/".join(parent.parts)
                if parent_name in seen and seen[parent_name] == "file": reject("archive parent/file conflict")
            if member.isfile() and any(name.startswith(normalized + "/") for name in seen):
                reject("archive file/child conflict")
            if not (member.isdir() or member.isfile()): reject("unsupported archive entry type")
            if member.mode & (stat.S_ISUID | stat.S_ISGID): reject("setid archive entry")
            seen[normalized] = "dir" if member.isdir() else "file"
            relative = Path(*path.parts[1:])
            output = destination / relative
            if member.isdir():
                make_staging_directory(output)
                continue
            total += member.size
            if total > max_bytes: reject("archive expanded size exceeds bound")
            make_staging_directory(output.parent)
            source = archive.extractfile(member)
            if source is None: reject("regular file has no data")
            written = 0
            with open(output, "xb") as target:
                while True:
                    chunk = source.read(min(1024 * 1024, member.size - written))
                    if not chunk: break
                    target.write(chunk); written += len(chunk)
                if written != member.size: reject("short archive member")
            os.chmod(output, member.mode & 0o777)
required = ["backend/main.py", "backend/slack_bridge.py", "backend/requirements.txt",
            "frontend/package.json", "frontend/package-lock.json",
            "frontend/server.mjs", "frontend/build/index.js"]
for relative in required:
    if not (destination / relative).is_file(): reject("archive missing required runtime file: " + relative)
manifest = {"application": "gpu-monitor", "artifact": f"gpu-monitor-{sha}.tar.gz",
            "git_sha": sha, "schema": 1, "sha256": expected}
(destination / "release-manifest.json").write_text(
    json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
PY
}

install_dependencies() {
  local release=$1 timeout_command python_command npm_command node_command npm_cli
  if [[ "$test_mode" == true ]]; then
    timeout_command=$test_timeout_command
    python_command="$test_tool_dir/python3"
    npm_command="$test_tool_dir/npm"
    node_command="$test_tool_dir/node"
    npm_cli=
  else
    timeout_command=/usr/bin/timeout
    python_command=/usr/bin/python3
    npm_command=
    node_command=/opt/gpu-monitor/node/bin/node
    npm_cli=/opt/gpu-monitor/node/lib/node_modules/npm/bin/npm-cli.js
  fi
  [[ -n "$timeout_command" && "$timeout_command" == /* && -x "$timeout_command" ]] ||
    { printf 'ERROR: trusted dependency timeout is required and unavailable\n' >&2; return 1; }
  [[ -x "$python_command" ]] ||
    { printf 'ERROR: trusted Python runtime is unavailable\n' >&2; return 1; }
  [[ -x "$node_command" ]] ||
    { printf 'ERROR: managed frontend Node runtime is unavailable\n' >&2; return 1; }
  if [[ "$test_mode" == true ]]; then
    [[ -x "$npm_command" ]] ||
      { printf 'ERROR: frontend npm test runtime is unavailable\n' >&2; return 1; }
  else
    [[ -f "$npm_cli" && ! -L "$npm_cli" ]] ||
      { printf 'ERROR: managed frontend npm runtime is unavailable\n' >&2; return 1; }
  fi
  "$timeout_command" 300 "$python_command" -m venv "$release/.venv" || return 1
  [[ -x "$release/.venv/bin/python" ]] ||
    { printf 'ERROR: venv interpreter was not created\n' >&2; return 1; }
  "$timeout_command" 300 "$release/.venv/bin/python" -m pip install \
    --disable-pip-version-check --no-cache-dir --requirement "$release/backend/requirements.txt" || return 1
  if [[ "$test_mode" == true ]]; then
    (cd "$release/frontend" &&
      "$timeout_command" 300 "$npm_command" ci --omit=dev --ignore-scripts --no-audit --no-fund \
        --cache "$release/.npm-cache") || return 1
  else
    (cd "$release/frontend" &&
      "$timeout_command" 300 "$node_command" "$npm_cli" ci --omit=dev --ignore-scripts --no-audit --no-fund \
        --cache "$release/.npm-cache") || return 1
  fi
  rm -rf "$release/.npm-cache" || return 1
  return 0
}

prepare_runtime_group_inheritance() {
  local release=$1
  "$INTERNAL_PYTHON" - "$release" "$runtime_gid" "$test_mode" <<'PY' || return 1
import os, stat, sys
root, expected_gid, test_mode = sys.argv[1], int(sys.argv[2]), sys.argv[3]
for current, dirs, _files in os.walk(root):
    metadata = os.lstat(current)
    if not stat.S_ISDIR(metadata.st_mode) or metadata.st_gid != expected_gid:
        print(f"ERROR: staging directory has unexpected runtime group: {current}", file=sys.stderr)
        raise SystemExit(1)
    if test_mode != "true" and not metadata.st_mode & stat.S_ISGID:
        print(f"ERROR: staging directory lost setgid inheritance: {current}", file=sys.stderr)
        raise SystemExit(1)
    dirs.sort()
PY
}

check_release_size_and_space() {
  local release=$1
  "$INTERNAL_PYTHON" - "$release" "$max_expanded_bytes" <<'PY'
import os, shutil, sys
root, maximum = sys.argv[1], int(sys.argv[2])
total = 0
for current, _, files in os.walk(root):
    for name in files:
        total += os.path.getsize(os.path.join(current, name))
        if total > maximum:
            print("ERROR: release tree exceeds byte bound", file=sys.stderr); raise SystemExit(1)
free = shutil.disk_usage(os.path.dirname(root)).free
if free < max(1048576, total // 10):
    print("ERROR: insufficient free space after release construction", file=sys.stderr); raise SystemExit(1)
PY
}

finalize_release_tree() {
  local release=$1
  "$INTERNAL_PYTHON" - "$release" "$runtime_gid" <<'PY' || return 1
import os, stat, sys
root = sys.argv[1]
for current, dirs, files in os.walk(root):
    os.chmod(current, 0o550)
    for name in files:
        path = os.path.join(current, name)
        mode = os.lstat(path).st_mode
        if stat.S_ISREG(mode):
            os.chmod(path, 0o550 if mode & stat.S_IXUSR else 0o440)
PY
  "$INTERNAL_PYTHON" - "$release" "$runtime_gid" <<'PY' || return 1
import os, stat, sys
root, expected_gid = sys.argv[1], int(sys.argv[2])

def reject(path, reason):
    print(f"ERROR: published inode metadata mismatch: {path}: {reason}", file=sys.stderr)
    raise SystemExit(1)

def verify(path):
    metadata = os.lstat(path)
    mode = stat.S_IMODE(metadata.st_mode)
    if metadata.st_gid != expected_gid:
        reject(path, f"gid {metadata.st_gid}, expected {expected_gid}")
    if stat.S_ISDIR(metadata.st_mode):
        if mode != 0o550:
            reject(path, f"directory mode {mode:04o}, expected 0550")
        with os.scandir(path) as entries:
            children = sorted((entry.path for entry in entries), key=os.fsencode)
        for child in children:
            verify(child)
    elif stat.S_ISREG(metadata.st_mode):
        if mode not in (0o440, 0o550):
            reject(path, f"regular-file mode {mode:04o}, expected 0440 or 0550")
    elif stat.S_ISLNK(metadata.st_mode):
        if mode != 0o777:
            reject(path, f"symlink mode {mode:04o}, expected 0777")
    else:
        reject(path, "unsupported inode type")

verify(root)
PY
  fsync_regular_files "$release" || return 1
  fsync_tree_bottom_up "$release" || return 1
  return 0
}

publish_release() {
  local temporary=$1 release=$2
  # Cross-parent directory renames update the candidate's ".." entry and
  # therefore require owner write permission on the candidate on Linux and
  # Darwin. The private tmp parent remains 2700 while this compatibility bit
  # is set, and the release root is restored before any pointer swap.
  chmod 0750 "$temporary" || return 1
  if ! mv "$temporary" "$release"; then
    chmod 0550 "$temporary" 2>/dev/null || true
    return 1
  fi
  chmod 0550 "$release" || return 1
  "$INTERNAL_PYTHON" - "$release" "$runtime_gid" <<'PY'
import os, stat, sys
path, expected_gid = sys.argv[1], int(sys.argv[2])
metadata = os.lstat(path)
if metadata.st_gid != expected_gid or stat.S_IMODE(metadata.st_mode) != 0o550:
    print("ERROR: published release root metadata mismatch", file=sys.stderr)
    raise SystemExit(1)
PY
}

validate_existing_release() {
  "$INTERNAL_PYTHON" - "$1/release-manifest.json" "$sha" "$digest" <<'PY'
import json, sys
path, sha, digest = sys.argv[1:]
expected = {"application":"gpu-monitor","artifact":f"gpu-monitor-{sha}.tar.gz",
            "git_sha":sha,"schema":1,"sha256":digest}
try:
    actual = json.load(open(path, encoding="utf-8"))
except Exception as error:
    print(f"ERROR: existing manifest unreadable: {error}", file=sys.stderr); raise SystemExit(1)
if actual != expected:
    print("ERROR: existing release conflicts with requested digest", file=sys.stderr); raise SystemExit(1)
PY
}

retain_successful_releases() {
  local current previous keep dir name preserve item
  current=$(current_target); previous=$(previous_target)
  keep=$("$INTERNAL_PYTHON" - "$state" <<'PY'
import json, sys
order = []
try: lines = open(sys.argv[1], encoding="utf-8")
except FileNotFoundError: lines = []
for line in lines:
    try: data = json.loads(line)
    except Exception: continue
    sha = data.get("sha")
    if data.get("status") == "success" and sha:
        if sha in order: order.remove(sha)
        order.append(sha)
print("\n".join(order[-3:]))
PY
)
  while IFS= read -r dir; do
    [[ -n "$dir" ]] || continue
    name=${dir##*/}; preserve=false
    [[ "releases/$name" == "$current" || "releases/$name" == "$previous" ]] && preserve=true
    while IFS= read -r item; do if [[ "$item" == "$name" ]]; then preserve=true; fi; done <<< "$keep"
    if [[ "$preserve" == false ]]; then chmod -R u+w "$dir" 2>/dev/null || true; rm -rf "$dir"; fsync_directory "$releases"; fi
  done < <(find "$releases" -mindepth 1 -maxdepth 1 -type d | LC_ALL=C sort)
}

mark_failed_and_prune() {
  local artifact=$1
  [[ -e "$artifact" ]] || return 0
  touch "${artifact}.failed"
  fsync_directory "${artifact%/*}"
  "$INTERNAL_PYTHON" - "$incoming" "$failed_max_count" <<'PY'
import os, pathlib, sys
root = pathlib.Path(sys.argv[1]); maximum = int(sys.argv[2])
markers = sorted(root.glob("*/*.tar.gz.failed"), key=lambda p: p.stat().st_mtime, reverse=True)
for marker in markers[maximum:]:
    artifact = pathlib.Path(str(marker)[:-7])
    try: artifact.unlink()
    except FileNotFoundError: pass
    marker.unlink()
    try: marker.parent.rmdir()
    except OSError: pass
PY
  fsync_directory "$incoming"
}

snapshot_targets_match() {
  local expected_current=$1 expected_previous=$2 actual_current actual_previous
  actual_current=$(current_target)
  actual_previous=$(previous_target)
  [[ "$actual_current" == "$expected_current" && "$actual_previous" == "$expected_previous" ]]
}

candidate_is_unreferenced() {
  local candidate=$1 actual_current actual_previous
  actual_current=$(current_target)
  actual_previous=$(previous_target)
  [[ "$actual_current" != "$candidate" && "$actual_previous" != "$candidate" ]]
}

remove_tree_and_fsync() {
  local tree=$1 parent=$2
  [[ -e "$tree" ]] || return 0
  chmod -R u+w "$tree" 2>/dev/null || return 1
  rm -rf "$tree" || return 1
  fsync_directory "$parent" || return 1
}

recover_snapshot() {
  local old_current=$1 old_previous=$2 failed_sha=${3:-} failed_digest=${4:-}
  local recovery_error=
  if [[ -n "$old_current" ]]; then
    if ! swap_generation "$old_current" "$old_previous"; then
      recovery_error=pointer_or_fsync_restore_failed
    fi
  else
    if ! rm -f "$base/current"; then recovery_error=current_pointer_remove_failed; fi
    if ! rm -f "$base/previous"; then recovery_error="${recovery_error:+$recovery_error,}previous_pointer_remove_failed"; fi
    if ! rm -f "$generations/active"; then recovery_error="${recovery_error:+$recovery_error,}active_generation_remove_failed"; fi
    if ! fsync_directory "$base"; then recovery_error="${recovery_error:+$recovery_error,}base_fsync_failed"; fi
    if ! fsync_directory "$generations"; then recovery_error="${recovery_error:+$recovery_error,}generation_fsync_failed"; fi
  fi
  if [[ -z "$recovery_error" ]] && ! snapshot_targets_match "$old_current" "$old_previous"; then
    recovery_error=pointer_restore_verification_failed
  fi
  if [[ -z "$recovery_error" && -z "$old_current" ]]; then
    if ! stop_units; then
      recovery_error=recovery_stop_failed
    elif append_state rollback_succeeded "$failed_sha" "$failed_digest" restored_absent; then
      return 0
    else
      recovery_error=rollback_success_log_failed
    fi
  fi
  if [[ -z "$recovery_error" ]]; then
    if ! restart_units; then
      recovery_error=recovery_restart_failed
    elif ! run_health; then
      recovery_error=recovery_health_failed
    fi
  fi
  if [[ -z "$recovery_error" ]]; then
    if append_state rollback_succeeded "$failed_sha" "$failed_digest" restored; then
      return 0
    fi
    recovery_error=rollback_success_log_failed
  fi
  if ! append_state rollback_failed "$failed_sha" "$failed_digest" "$recovery_error"; then
    printf 'ERROR: unable to durably record rollback_failed (%s)\n' "$recovery_error" >&2
  fi
  return 1
}

transition_pointers() {
  local candidate=$1 old_current=$2
  local next_previous=
  [[ -z "$old_current" || "$old_current" == "$candidate" ]] || next_previous=$old_current
  swap_generation "$candidate" "$next_previous"
}

do_activate() {
  local object_dir="$incoming/$sha" artifact="$incoming/$sha/$digest.tar.gz" artifact_name="$digest.tar.gz" release="$releases/$sha"
  local temporary old_current old_previous constructed=false recovery_succeeded=false
  old_current=$(current_target); old_previous=$(previous_target)
  if [[ ! -d "$release" ]]; then
    [[ -d "$object_dir" ]] || fail "incoming artifact is missing"
    temporary="$tmp_root/release-${sha}.$$"
    rm -rf "$temporary"
    if ! (validate_and_extract "$object_dir" "$artifact_name" "$temporary" &&
      prepare_runtime_group_inheritance "$temporary" &&
      install_dependencies "$temporary" &&
      check_release_size_and_space "$temporary" &&
      finalize_release_tree "$temporary"); then
      if ! remove_tree_and_fsync "$temporary" "$tmp_root"; then
        printf 'ERROR: failed to clean rejected release staging tree %s\n' "$temporary" >&2
      fi
      mark_failed_and_prune "$artifact"
      fail "release construction failed"
    fi
    if ! publish_release "$temporary" "$release"; then
      remove_tree_and_fsync "$temporary" "$tmp_root" ||
        printf 'ERROR: failed to clean unpublished staging tree %s\n' "$temporary" >&2
      remove_tree_and_fsync "$release" "$releases" ||
        printf 'ERROR: failed to clean invalid unpublished release %s\n' "$release" >&2
      fail "release publication rename failed"
    fi
    constructed=true
    fsync_directory "$tmp_root" || fail "staging parent fsync failed after publication"
    fsync_directory "$releases" || fail "release parent fsync failed after publication"
  else
    validate_existing_release "$release"
  fi
  if ! transition_pointers "releases/$sha" "$old_current"; then
    recovery_succeeded=false
    if recover_snapshot "$old_current" "$old_previous" "$sha" "$digest"; then
      recovery_succeeded=true
    else
      printf 'ERROR: generation publish and recovery failed for %s\n' "$sha" >&2
    fi
    if [[ "$constructed" == true && "$recovery_succeeded" == true ]]; then
      if candidate_is_unreferenced "releases/$sha"; then
        remove_tree_and_fsync "$release" "$releases" ||
          printf 'ERROR: failed to remove unreferenced candidate %s\n' "$sha" >&2
      else
        printf 'ERROR: recovered pointers still reference candidate %s; preserving release\n' "$sha" >&2
      fi
    fi
    mark_failed_and_prune "$artifact"
    return 1
  fi
  if ! restart_units || ! run_health; then
    recovery_succeeded=false
    if recover_snapshot "$old_current" "$old_previous" "$sha" "$digest"; then
      recovery_succeeded=true
    else
      printf 'ERROR: activation rollback failed for %s\n' "$sha" >&2
    fi
    if [[ "$constructed" == true && "$recovery_succeeded" == true ]]; then
      if candidate_is_unreferenced "releases/$sha"; then
        remove_tree_and_fsync "$release" "$releases" ||
          printf 'ERROR: failed to remove unreferenced candidate %s\n' "$sha" >&2
      else
        printf 'ERROR: recovered pointers still reference candidate %s; preserving release\n' "$sha" >&2
      fi
    fi
    mark_failed_and_prune "$artifact"
    return 1
  fi
  append_state success "$sha" "$digest" activated
  rm -f "$artifact" "${artifact}.failed"
  fsync_directory "$incoming/$sha"
  rmdir "$incoming/$sha" 2>/dev/null || true
  fsync_directory "$incoming"
  retain_successful_releases
  return 0
}

do_rollback() {
  local old_current old_previous
  old_current=$(current_target); old_previous=$(previous_target)
  [[ -n "$old_current" && -d "$base/$old_current" ]] || fail "current release unavailable"
  [[ -n "$old_previous" && -d "$base/$old_previous" ]] || fail "no previous release"
  if ! swap_generation "$old_previous" "$old_current"; then
    if ! recover_snapshot "$old_current" "$old_previous" "${old_previous##*/}" ""; then
      printf 'ERROR: manual rollback pointer publish and recovery failed\n' >&2
    fi
    return 1
  fi
  if ! restart_units || ! run_health; then
    if ! recover_snapshot "$old_current" "$old_previous" "${old_previous##*/}" ""; then
      printf 'ERROR: manual rollback recovery failed\n' >&2
    fi
    return 1
  fi
  append_state manual_rollback "${old_previous##*/}" "" activated
  return 0
}

do_status() {
  "$INTERNAL_PYTHON" - "$env_name" "$(current_target)" "$(previous_target)" "$state" "$releases" <<'PY'
import json, sys
from pathlib import Path

environment, current, previous, state, releases = sys.argv[1:]
current_sha256 = ""
if current.startswith("releases/"):
    release_sha = current.removeprefix("releases/")
    manifest = Path(releases) / release_sha / "release-manifest.json"
    try:
        payload = json.load(open(manifest, encoding="utf-8"))
    except FileNotFoundError:
        payload = {}
    digest = payload.get("sha256")
    if isinstance(digest, str):
        current_sha256 = digest
print(json.dumps({"current":current,"current_sha256":current_sha256,
                  "environment":environment,"previous":previous,"state":state},
                 sort_keys=True,separators=(",",":")))
PY
}

case "$action" in
  status|upload|discard|activate|rollback)
    if [[ "$action" == status ]]; then
      if [[ "$test_mode" != true && ! -d "$lock_dir" ]]; then
        do_status
        exit 0
      fi
      inner=status-inner
    else
      inner="${action}-inner"
    fi
    if [[ "$test_mode" == true ]]; then
      locked_command=(env -i PATH="$PATH" PREFIX="$prefix" GPU_MONITOR_TEST_PATH="$PATH" \
        GPU_MONITOR_INTERNAL_PYTHON="$internal_python" GPU_MONITOR_MAX_UPLOAD_BYTES="$max_upload" \
        GPU_MONITOR_MAX_ARCHIVE_FILES="$max_archive_files" GPU_MONITOR_MAX_EXPANDED_BYTES="$max_expanded_bytes" \
        GPU_MONITOR_HEALTH_RETRIES="$health_retries" GPU_MONITOR_HEALTH_SLEEP_SECONDS="$health_sleep_seconds" \
        GPU_MONITOR_MAX_INCOMING_COUNT="$max_incoming_count" GPU_MONITOR_MAX_INCOMING_BYTES="$max_incoming_bytes" \
        GPU_MONITOR_UPLOAD_TEMP_MAX_AGE="$temp_max_age" GPU_MONITOR_FAILED_ARTIFACT_MAX_COUNT="$failed_max_count" \
        "$script_dir/activate-release.sh" --test-mode "$env_name" "$inner" ${sha:+"$sha"} ${digest:+"$digest"})
      if command -v flock >/dev/null 2>&1; then
        exec flock "$lock_file" "${locked_command[@]}"
      fi
      exec "$internal_python" -c 'import fcntl, os, sys
fd = os.open(sys.argv[1], os.O_CREAT | os.O_RDWR, 0o600)
fcntl.flock(fd, fcntl.LOCK_EX)
os.set_inheritable(fd, True)
os.execvpe(sys.argv[2], sys.argv[2:], os.environ)
' "$lock_file" "${locked_command[@]}"
    fi
    exec /usr/bin/flock "$lock_file" "$script_dir/activate-release.sh" "$inner" "$env_name" ${sha:+"$sha"} ${digest:+"$digest"}
    ;;
  status-inner) do_status ;;
  upload-inner) do_upload ;;
  discard-inner) do_discard ;;
  activate-inner) prune_temps; do_activate ;;
  rollback-inner) prune_temps; do_rollback ;;
esac
