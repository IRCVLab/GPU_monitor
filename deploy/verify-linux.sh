#!/usr/bin/env bash
set -u -o pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ARTIFACT="$ROOT/output/verification/linux-verification.txt"
FORBIDDEN_REMOTE_WORKDIR='/home/ircv/workspace/monitoring*'

usage() {
  cat <<'USAGE' >&2
usage: deploy/verify-linux.sh --local|--remote

Runs repository-owned Linux verification in a unique /tmp/storage-viz-verify.*
directory populated from tracked files only.
USAGE
}

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 2
}

mode="${1:-}"
case "$mode" in
  --local|--remote) ;;
  *) usage; exit 2 ;;
esac
[[ $# -eq 1 ]] || { usage; exit 2; }

mkdir -p "$(dirname "$ARTIFACT")"

write_header() {
  : > "$ARTIFACT"
  {
    printf 'storage-viz Linux verification\n'
    printf 'mode=%s\n' "${mode#--}"
    printf 'forbidden_remote_workdir=%s\n' "$FORBIDDEN_REMOTE_WORKDIR"
  } >> "$ARTIFACT"
}

append_artifact() {
  printf '%s\n' "$*" >> "$ARTIFACT"
}

make_archive() {
  local archive="$1"
  # tracked-files-only source list: git ls-files -z
  (cd "$ROOT" && python3 - "$archive" <<'PY'
import pathlib
import subprocess
import sys
import tarfile
import time

archive = pathlib.Path(sys.argv[1])
manifest_name = ".storage-viz-tracked-files"
try:
    # tracked-files-only source list: git ls-files -z
    raw = subprocess.check_output(["git", "ls-files", "-z"])
except subprocess.CalledProcessError:
    manifest = pathlib.Path(manifest_name)
    if not manifest.is_file():
        raise
    raw = manifest.read_bytes()
names = raw.split(b"\0")

def safe_name(raw_name):
    if not raw_name:
        return None
    name = raw_name.decode("utf-8", "surrogateescape")
    if name.startswith("/") or "\x00" in name:
        raise SystemExit("unsafe tracked file name")
    parts = pathlib.PurePosixPath(name).parts
    if any(part in ("", ".", "..") for part in parts):
        raise SystemExit("unsafe tracked file name")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in name):
        raise SystemExit("unsafe tracked file name")
    return name

with tarfile.open(archive, "w:gz") as tar:
    for raw_name in names:
        name = safe_name(raw_name)
        if name is None:
            continue
        path = pathlib.Path(name)
        if path.is_file() or path.is_symlink():
            tar.add(path, arcname=name, recursive=False)
    info = tarfile.TarInfo(manifest_name)
    info.size = len(raw)
    info.mtime = int(time.time())
    info.mode = 0o600
    tar.addfile(info, fileobj=__import__("io").BytesIO(raw))
PY
  )
}

validate_tar_members() {
  python3 - "$1" <<'PY'
import pathlib
import sys
import tarfile

archive = sys.argv[1]
with tarfile.open(archive, "r:gz") as tar:
    for member in tar.getmembers():
        name = member.name
        parts = pathlib.PurePosixPath(name).parts
        if (
            not name
            or name.startswith("/")
            or any(part in ("", ".", "..") for part in parts)
            or any(ord(ch) < 32 or ord(ch) == 127 for ch in name)
        ):
            raise SystemExit("unsafe tar member")
PY
}

validate_linux_host() {
  local value="${1:-}" user host label
  [[ -n "$value" && ${#value} -le 255 ]] || return 1
  [[ "$value" != -* ]] || return 1
  [[ "$value" != *[[:space:]]* ]] || return 1
  [[ "$value" != *[[:cntrl:]]* ]] || return 1
  case "$value" in
    *";"*|*"|"*|*"&"*|*'`'*|*'$'*|*"("*|*")"*|*"<"*|*">"*|*"{"*|*"}"*|*"\\"*|*"\""*|*"'"*) return 1 ;;
  esac
  [[ "$value" != *@*@* ]] || return 1
  if [[ "$value" == *@* ]]; then
    user="${value%@*}"
    host="${value#*@}"
    [[ "$user" =~ ^[A-Za-z0-9._-]{1,64}$ ]] || return 1
    [[ "$user" != -* ]] || return 1
  else
    host="$value"
  fi
  [[ -n "$host" && "$host" != -* ]] || return 1
  if [[ "$host" =~ ^\[[0-9A-Fa-f:.]{2,64}\]$ ]]; then
    [[ "$host" == *:* ]] || return 1
    return 0
  fi
  if [[ "$host" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]]; then
    IFS=. read -r -a octets <<< "$host"
    for label in "${octets[@]}"; do
      [[ "$label" =~ ^[0-9]+$ && "$label" -le 255 ]] || return 1
    done
    return 0
  fi
  [[ "$host" =~ ^[A-Za-z0-9.-]{1,253}$ ]] || return 1
  [[ "$host" != .* && "$host" != *..* && "$host" != *. ]] || return 1
  IFS=. read -r -a labels <<< "$host"
  for label in "${labels[@]}"; do
    [[ ${#label} -ge 1 && ${#label} -le 63 ]] || return 1
    [[ "$label" =~ ^[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?$ ]] || return 1
  done
  return 0
}

run_local() {
  local tmp rc=0
  tmp="$(mktemp -d /tmp/storage-viz-verify.XXXXXX)" || return 2
  append_artifact "temp_path=$tmp"
  # cleanup contract: trap cleanup_local EXIT; cleanup_local performs rm -rf -- "$tmp"
  cleanup_local() {
    rm -rf -- "$tmp"
    if [[ -e "$tmp" ]]; then
      append_artifact "local_cleanup=failed"
      return 1
    else
      append_artifact "local_cleanup=removed"
      return 0
    fi
  }
  trap 'cleanup_local || exit 2' EXIT

  local archive="$tmp/repo.tar.gz"
  make_archive "$archive" || rc=2
  if [[ "$rc" -eq 0 ]]; then mkdir -p "$tmp/repo" || rc=2; fi
  if [[ "$rc" -eq 0 ]]; then validate_tar_members "$archive" || rc=2; fi
  if [[ "$rc" -eq 0 ]]; then tar -xzf "$archive" -C "$tmp/repo" || rc=2; fi
  if [[ "$rc" -eq 0 ]]; then run_commands_in_dir "$tmp/repo" || rc=$?; fi
  cleanup_local || rc=2
  trap - EXIT
  return "$rc"
}

run_one() {
  local workdir="$1" name="$2" rc
  shift 2
  append_artifact "command=$name"
  (cd "$workdir" && "$@") > /dev/null 2>&1
  rc=$?
  append_artifact "exit_code=$rc"
  return "$rc"
}

run_commands_in_dir() {
  local workdir="$1" rc=0
  run_one "$workdir" 'make -C scanner clean all test' make -C scanner clean all test || rc=1
  run_one "$workdir" 'python3 data/test_fixtures.py' python3 data/test_fixtures.py || rc=1
  run_one "$workdir" "python3 -m unittest discover -s agent -p 'test_*.py' -v" python3 -m unittest discover -s agent -p 'test_*.py' -v || rc=1
  run_one "$workdir" "python3 -m unittest discover -s collector -p 'test_*.py' -v" python3 -m unittest discover -s collector -p 'test_*.py' -v || rc=1
  run_one "$workdir" 'bash deploy/test_deploy_scripts.sh' bash deploy/test_deploy_scripts.sh || rc=1
  run_one "$workdir" 'deploy/install-agent.sh --dry-run' deploy/install-agent.sh --dry-run || rc=1
  return "$rc"
}

run_remote() {
  local host port local_tmp archive remote_tmp remote_out ssh_rc cleanup_rc rc=0
  host="${STORAGE_VIZ_LINUX_HOST:-}"
  port="${STORAGE_VIZ_LINUX_PORT:-22}"
  [[ -n "$host" ]] || fail 'set STORAGE_VIZ_LINUX_HOST for --remote'
  validate_linux_host "$host" || fail 'invalid STORAGE_VIZ_LINUX_HOST'
  [[ "$port" =~ ^[0-9]+$ && "$port" -ge 1 && "$port" -le 65535 ]] || fail 'invalid STORAGE_VIZ_LINUX_PORT'

  local_tmp="$(mktemp -d "${TMPDIR:-/tmp}/storage-viz-verify-archive.XXXXXX")" || return 2
  cleanup_archive() { rm -rf -- "$local_tmp"; }
  trap cleanup_archive RETURN
  archive="$local_tmp/repo.tar.gz"
  make_archive "$archive" || return 2
  validate_tar_members "$archive" || return 2

  remote_out="$(ssh -p "$port" -o BatchMode=yes -o IdentitiesOnly=yes "$host" 'pwd_checked=$(pwd -P); case "$pwd_checked" in /home/ircv/workspace/monitoring*) printf "remote_workdir_guard=rejected\n"; exit 63;; esac; mktemp -d /tmp/storage-viz-verify.XXXXXX')"
  ssh_rc=$?
  if [[ "$ssh_rc" -ne 0 ]]; then
    [[ -n "$remote_out" ]] && append_artifact "$remote_out"
    return "$ssh_rc"
  fi
  remote_tmp="$remote_out"
  [[ "$remote_tmp" == /tmp/storage-viz-verify.* ]] || fail 'invalid remote temp path'
  case "$remote_tmp" in /home/ircv/workspace/monitoring*) fail 'remote temp path rejected' ;; esac
  append_artifact "remote_temp_path=$remote_tmp"

  scp -P "$port" -o BatchMode=yes -o IdentitiesOnly=yes "$archive" "$host:$remote_tmp/repo.tar.gz" >/dev/null || {
    if ssh -p "$port" -o BatchMode=yes -o IdentitiesOnly=yes "$host" "rm -rf -- '$remote_tmp'" >/dev/null 2>&1; then
      append_artifact "remote_cleanup=removed"
    else
      append_artifact "remote_cleanup=failed"
    fi
    return 2
  }

  remote_out="$(ssh -p "$port" -o BatchMode=yes -o IdentitiesOnly=yes "$host" "VERIFY_TMP='$remote_tmp' bash -s" <<'REMOTE'
set -u -o pipefail
work="$VERIFY_TMP"
cleanup() {
  rm -rf -- "$work"
  if [ -e "$work" ]; then
    printf 'remote_cleanup=failed\n'
  else
    printf 'remote_cleanup=removed\n'
  fi
}
trap cleanup EXIT
case "$(pwd -P)" in /home/ircv/workspace/monitoring*) printf 'remote_workdir_guard=rejected\n'; exit 63;; esac
mkdir -p "$work/repo"
python3 - "$work/repo.tar.gz" <<'PY'
import pathlib
import sys
import tarfile

with tarfile.open(sys.argv[1], "r:gz") as tar:
    for member in tar.getmembers():
        name = member.name
        parts = pathlib.PurePosixPath(name).parts
        if (
            not name
            or name.startswith("/")
            or any(part in ("", ".", "..") for part in parts)
            or any(ord(ch) < 32 or ord(ch) == 127 for ch in name)
        ):
            raise SystemExit("unsafe tar member")
PY
tar -xzf "$work/repo.tar.gz" -C "$work/repo"
rc=0
run_one() {
  name="$1"; shift
  printf 'command=%s\n' "$name"
  (cd "$work/repo" && "$@") >/dev/null 2>&1
  code=$?
  printf 'exit_code=%s\n' "$code"
  [ "$code" -eq 0 ] || rc=1
}
run_one 'make -C scanner clean all test' make -C scanner clean all test
run_one 'python3 data/test_fixtures.py' python3 data/test_fixtures.py
run_one "python3 -m unittest discover -s agent -p 'test_*.py' -v" python3 -m unittest discover -s agent -p 'test_*.py' -v
run_one "python3 -m unittest discover -s collector -p 'test_*.py' -v" python3 -m unittest discover -s collector -p 'test_*.py' -v
run_one 'bash deploy/test_deploy_scripts.sh' bash deploy/test_deploy_scripts.sh
run_one 'deploy/install-agent.sh --dry-run' deploy/install-agent.sh --dry-run
exit "$rc"
REMOTE
)"
  ssh_rc=$?
  [[ -n "$remote_out" ]] && append_artifact "$remote_out"
  rc="$ssh_rc"
  if [[ "$rc" -ne 0 ]] && ! grep -Eq '^remote_cleanup=(removed|failed)$' "$ARTIFACT"; then
    if ssh -p "$port" -o BatchMode=yes -o IdentitiesOnly=yes "$host" "rm -rf -- '$remote_tmp'" >/dev/null 2>&1; then
      append_artifact "remote_cleanup=removed"
    else
      append_artifact "remote_cleanup=failed"
    fi
  fi
  if grep -Fqx 'remote_cleanup=failed' "$ARTIFACT"; then
    rc=2
  fi
  return "$rc"
}

write_header
case "$mode" in
  --local) run_local ;;
  --remote) run_remote ;;
esac
rc=$?
append_artifact "overall_exit_code=$rc"
exit "$rc"
