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
  # tracked-files-only source list: git ls-files
  (cd "$ROOT" && python3 - "$archive" <<'PY'
import pathlib
import subprocess
import sys
import tarfile

archive = pathlib.Path(sys.argv[1])
files = subprocess.check_output(["git", "ls-files"], text=True).splitlines()
with tarfile.open(archive, "w:gz") as tar:
    for name in files:
        path = pathlib.Path(name)
        if path.is_file() or path.is_symlink():
            tar.add(path, arcname=name, recursive=False)
PY
  )
}

run_local() {
  local tmp rc=0
  tmp="$(mktemp -d /tmp/storage-viz-verify.XXXXXX)" || return 2
  append_artifact "temp_path=$tmp"
  # cleanup contract: trap cleanup_local EXIT; cleanup_local performs rm -rf -- "$tmp"
  cleanup_local() {
    rm -rf -- "$tmp"
    if [[ -e "$tmp" ]]; then
      append_artifact "cleanup=failed"
    else
      append_artifact "cleanup=removed"
    fi
  }
  trap cleanup_local EXIT

  local archive="$tmp/repo.tar.gz"
  make_archive "$archive" || return 2
  mkdir -p "$tmp/repo"
  tar -xzf "$archive" -C "$tmp/repo" || return 2
  run_commands_in_dir "$tmp/repo" || rc=$?
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
  local host port local_tmp archive remote_tmp rc=0
  host="${STORAGE_VIZ_LINUX_HOST:-}"
  port="${STORAGE_VIZ_LINUX_PORT:-22}"
  [[ -n "$host" ]] || fail 'set STORAGE_VIZ_LINUX_HOST for --remote'
  [[ "$port" =~ ^[0-9]+$ && "$port" -ge 1 && "$port" -le 65535 ]] || fail 'invalid STORAGE_VIZ_LINUX_PORT'

  local_tmp="$(mktemp -d "${TMPDIR:-/tmp}/storage-viz-verify-archive.XXXXXX")" || return 2
  cleanup_archive() { rm -rf -- "$local_tmp"; }
  trap cleanup_archive RETURN
  archive="$local_tmp/repo.tar.gz"
  make_archive "$archive" || return 2

  remote_tmp="$(ssh -p "$port" -o BatchMode=yes -o IdentitiesOnly=yes "$host" 'pwd_checked=$(pwd -P); case "$pwd_checked" in /home/ircv/workspace/monitoring*) exit 63;; esac; mktemp -d /tmp/storage-viz-verify.XXXXXX')" || return 2
  [[ "$remote_tmp" == /tmp/storage-viz-verify.* ]] || fail 'invalid remote temp path'
  case "$remote_tmp" in /home/ircv/workspace/monitoring*) fail 'remote temp path rejected' ;; esac
  append_artifact "remote_temp_path=$remote_tmp"

  scp -P "$port" -o BatchMode=yes -o IdentitiesOnly=yes "$archive" "$host:$remote_tmp/repo.tar.gz" >/dev/null || {
    ssh -p "$port" -o BatchMode=yes -o IdentitiesOnly=yes "$host" "rm -rf -- '$remote_tmp'" >/dev/null 2>&1 || true
    return 2
  }

  ssh -p "$port" -o BatchMode=yes -o IdentitiesOnly=yes "$host" "VERIFY_TMP='$remote_tmp' bash -s" >> "$ARTIFACT" <<'REMOTE'
set -u -o pipefail
work="$VERIFY_TMP"
cleanup() {
  rm -rf -- "$work"
  if [ -e "$work" ]; then
    printf 'cleanup=failed\n'
  else
    printf 'cleanup=removed\n'
  fi
}
trap cleanup EXIT
case "$(pwd -P)" in /home/ircv/workspace/monitoring*) printf 'remote_workdir_guard=rejected\n'; exit 63;; esac
mkdir -p "$work/repo"
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
  rc=$?
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
