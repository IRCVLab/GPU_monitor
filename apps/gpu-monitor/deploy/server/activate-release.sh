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
    status:3|rollback:3|rollback-inner:3|upload:5|upload-inner:5|activate:5|activate-inner:5) ;;
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
    status:2|rollback:2|rollback-inner:2|upload:4|upload-inner:4|activate:4|activate-inner:4) ;;
    *) fail "invalid production arguments" ;;
  esac
fi

case "$env_name" in dev|live) ;; *) fail "invalid environment" ;; esac
case "$action" in status|upload|upload-inner|activate|activate-inner|rollback|rollback-inner) ;; *) fail "invalid action" ;; esac
case "$action" in
  upload|upload-inner|activate|activate-inner)
    [[ "$sha" =~ ^[0-9a-f]{40}$ ]] || fail "invalid sha"
    [[ "$digest" =~ ^[0-9a-f]{64}$ ]] || fail "invalid sha256"
    ;;
esac

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

validate_bound() {
  local name=$1 value=$2 maximum=$3
  [[ "$value" =~ ^[0-9]+$ ]] && (( value >= 1 && value <= maximum )) ||
    fail "$name must be a bounded positive integer"
}
if [[ "$test_mode" == true ]]; then
  [[ "$prefix" == /* && "$prefix" != / && "$prefix" != *$'\n'* ]] || fail "invalid test PREFIX"
  [[ "$internal_python" == /* && -x "$internal_python" ]] || fail "invalid internal Python"
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
  IFS=$' \t\n'
else
  [[ -x "$PRODUCTION_PYTHON" ]] || fail "required internal Python is unavailable"
fi

INTERNAL_PYTHON=$internal_python
base="${prefix}/srv/gpu-monitor/$env_name"
releases="$base/releases"
incoming="$base/incoming"
tmp_root="$base/tmp"
state="$base/deployments.jsonl"
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

if [[ "$action" != status ]]; then
  mkdir -p "$releases" "$incoming" "$tmp_root" "$lock_dir"
fi

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

if [[ "$action" != status ]]; then
  fsync_directory "$base"
  fsync_directory "$releases"
  fsync_directory "$incoming"
  fsync_directory "$tmp_root"
  fsync_directory "$lock_dir"
fi

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
  rm -f "$temporary"
  ln -s "$target" "$temporary"
  "$INTERNAL_PYTHON" - "$temporary" "$link" <<'PY'
import os, sys
os.replace(sys.argv[1], sys.argv[2])
PY
  fsync_directory "${link%/*}"
}
restore_link() {
  local target=$1 link=$2
  if [[ -n "$target" ]]; then
    atomic_link "$target" "$link"
  else
    rm -f "$link"
    fsync_directory "${link%/*}"
  fi
}
current_target() { [[ -L "$base/current" ]] && readlink "$base/current" || true; }
previous_target() { [[ -L "$base/previous" ]] && readlink "$base/previous" || true; }

restart_units() {
  local env=$env_name
  if [[ "$test_mode" == true ]]; then
    GPU_MONITOR_TEST_PATH="$PATH" "$sudo_command" -n "$restart_broker" --test-mode "$env_name"
  else
    /usr/bin/sudo -n "$restart_broker" "$env"
  fi
}

run_health() {
  if [[ "$test_mode" == true ]]; then
    env -i PATH="$PATH" GPU_MONITOR_TEST_PATH="$PATH" \
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

validate_and_extract() {
  local artifact=$1 destination=$2
  "$INTERNAL_PYTHON" - "$artifact" "$destination" "$sha" "$digest" "$max_archive_files" "$max_expanded_bytes" <<'PY'
import hashlib, json, os, shutil, stat, sys, tarfile
from pathlib import Path, PurePosixPath
artifact_path, destination_path, sha, expected, max_entries, max_bytes = sys.argv[1:]
destination = Path(destination_path); max_entries = int(max_entries); max_bytes = int(max_bytes)
def reject(message):
    print("ERROR: " + message, file=sys.stderr); raise SystemExit(1)
if destination.exists(): reject("temporary destination exists")
destination.mkdir(parents=True)
seen = {}; count = total = 0
with open(artifact_path, "rb") as artifact_file:
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
                output.mkdir(parents=True, exist_ok=True)
                os.chmod(output, member.mode & 0o777)
                continue
            total += member.size
            if total > max_bytes: reject("archive expanded size exceeds bound")
            output.parent.mkdir(parents=True, exist_ok=True)
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
            "frontend/package.json", "frontend/package-lock.json", "frontend/build/index.js"]
for relative in required:
    if not (destination / relative).is_file(): reject("archive missing required runtime file: " + relative)
manifest = {"application": "gpu-monitor", "artifact": f"gpu-monitor-{sha}.tar.gz",
            "git_sha": sha, "schema": 1, "sha256": expected}
(destination / "release-manifest.json").write_text(
    json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
PY
}

install_dependencies() {
  local release=$1
  python3 -m venv "$release/.venv"
  [[ ! -x "$release/.venv/bin/python" ]] || "$release/.venv/bin/python" -m pip install --requirement "$release/backend/requirements.txt"
  (cd "$release/frontend" && npm ci --omit=dev)
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
    while IFS= read -r item; do [[ "$item" == "$name" ]] && preserve=true; done <<< "$keep"
    if [[ "$preserve" == false ]]; then chmod -R u+w "$dir" 2>/dev/null || true; rm -rf "$dir"; fsync_directory "$releases"; fi
  done < <(find "$releases" -mindepth 1 -maxdepth 1 -type d | LC_ALL=C sort)
}

mark_failed_and_prune() {
  local artifact=$1
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

recover_snapshot() {
  local old_current=$1 old_previous=$2 failed_sha=${3:-} failed_digest=${4:-}
  restore_link "$old_current" "$base/current"
  restore_link "$old_previous" "$base/previous"
  if restart_units && run_health; then
    append_state rollback_succeeded "$failed_sha" "$failed_digest" restored
  else
    append_state rollback_failed "$failed_sha" "$failed_digest" recovery_failed
  fi
}

transition_pointers() {
  local candidate=$1 old_current=$2
  [[ -z "$old_current" || "$old_current" == "$candidate" ]] || atomic_link "$old_current" "$base/previous"
  atomic_link "$candidate" "$base/current"
}

do_activate() {
  local artifact="$incoming/$sha/$digest.tar.gz" release="$releases/$sha"
  local temporary old_current old_previous
  old_current=$(current_target); old_previous=$(previous_target)
  if [[ ! -d "$release" ]]; then
    [[ -f "$artifact" ]] || fail "incoming artifact is missing"
    temporary="$tmp_root/release-${sha}.$$"
    rm -rf "$temporary"
    if ! (validate_and_extract "$artifact" "$temporary" && install_dependencies "$temporary"); then
      chmod -R u+w "$temporary" 2>/dev/null || true; rm -rf "$temporary"
      fsync_directory "$tmp_root"
      mark_failed_and_prune "$artifact"
      fail "release construction failed"
    fi
    mv "$temporary" "$release"
    fsync_directory "$tmp_root"
    fsync_directory "$releases"
    chmod -R a-w "$release"
  else
    validate_existing_release "$release"
  fi
  transition_pointers "releases/$sha" "$old_current"
  if ! restart_units || ! run_health; then
    recover_snapshot "$old_current" "$old_previous" "$sha" "$digest" || true
    mark_failed_and_prune "$artifact"
    return 1
  fi
  append_state success "$sha" "$digest" activated
  rm -f "$artifact" "${artifact}.failed"
  fsync_directory "$incoming/$sha"
  rmdir "$incoming/$sha" 2>/dev/null || true
  fsync_directory "$incoming"
  retain_successful_releases
}

do_rollback() {
  local old_current old_previous
  old_current=$(current_target); old_previous=$(previous_target)
  [[ -n "$old_current" && -d "$base/$old_current" ]] || fail "current release unavailable"
  [[ -n "$old_previous" && -d "$base/$old_previous" ]] || fail "no previous release"
  atomic_link "$old_current" "$base/previous"
  atomic_link "$old_previous" "$base/current"
  if ! restart_units || ! run_health; then
    recover_snapshot "$old_current" "$old_previous" "${old_previous##*/}" "" || true
    return 1
  fi
  append_state manual_rollback "${old_previous##*/}" "" activated
}

do_status() {
  "$INTERNAL_PYTHON" - "$env_name" "$(current_target)" "$(previous_target)" "$state" <<'PY'
import json, sys
print(json.dumps({"environment":sys.argv[1],"current":sys.argv[2],
                  "previous":sys.argv[3],"state":sys.argv[4]},
                 sort_keys=True,separators=(",",":")))
PY
}

case "$action" in
  status) do_status ;;
  upload|activate|rollback)
    inner="${action}-inner"
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
  upload-inner) do_upload ;;
  activate-inner) prune_temps; do_activate ;;
  rollback-inner) prune_temps; do_rollback ;;
esac
