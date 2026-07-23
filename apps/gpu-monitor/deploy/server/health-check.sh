#!/bin/bash -p
set -euo pipefail

fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

PRODUCTION_PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
test_mode=false
case "$#:${1:-}" in
  1:dev|1:live)
    env_name=$1
    command_path=$PRODUCTION_PATH
    retries=5
    sleep_seconds=2
    ;;
  2:--test-mode)
    case "$2" in dev|live) ;; *) fail "invalid test-mode environment" ;; esac
    test_mode=true
    env_name=$2
    command_path=${GPU_MONITOR_TEST_PATH:-$PRODUCTION_PATH}
    retries=${GPU_MONITOR_HEALTH_RETRIES:-5}
    sleep_seconds=${GPU_MONITOR_HEALTH_SLEEP_SECONDS:-2}
    ;;
  *)
    fail "usage: health-check.sh <dev|live> | --test-mode <dev|live>"
    ;;
esac

unset \
  BASH_ENV ENV CDPATH GLOBIGNORE PREFIX GPU_MONITOR_ALLOWED_ENV \
  GPU_MONITOR_MAX_UPLOAD_BYTES GPU_MONITOR_INTERNAL_PYTHON \
  GPU_MONITOR_MAX_ARCHIVE_FILES GPU_MONITOR_MAX_EXPANDED_BYTES \
  GPU_MONITOR_TEST_PATH GPU_MONITOR_HEALTH_RETRIES \
  GPU_MONITOR_HEALTH_SLEEP_SECONDS PYTHONHOME PYTHONPATH \
  PYTHONSTARTUP NODE_OPTIONS
IFS=$' \t\n'
PATH=$command_path
export PATH

if [[ "$test_mode" == true ]]; then
  [[ "$retries" =~ ^[0-9]+$ ]] && (( retries >= 1 && retries <= 20 )) ||
    fail "test health retries must be between 1 and 20"
  [[ "$sleep_seconds" =~ ^[0-9]+$ ]] &&
    (( sleep_seconds >= 1 && sleep_seconds <= 60 )) ||
    fail "test health sleep seconds must be between 1 and 60"
  IFS=: read -r -a test_path_parts <<< "$command_path"
  for test_path_part in "${test_path_parts[@]}"; do
    [[ "$test_path_part" == /* && -d "$test_path_part" ]] ||
      fail "test PATH entries must be existing absolute directories"
  done
  IFS=$' \t\n'
fi

check_url() {
  local url=$1 attempt=1
  while (( attempt <= retries )); do
    if curl -fsS --max-time 2 "$url" >/dev/null; then
      return 0
    fi
    if (( attempt < retries )); then
      sleep "$sleep_seconds"
    fi
    attempt=$((attempt + 1))
  done
  return 1
}

if [[ "$env_name" == dev ]]; then
  check_url http://127.0.0.1:8101/health || fail "dev backend health failed"
  check_url http://127.0.0.1:5174/ || fail "dev frontend health failed"
else
  check_url http://127.0.0.1:8001/health || fail "live backend health failed"
  check_url http://127.0.0.1:5173/ || fail "live frontend health failed"
  check_url http://127.0.0.1:8000/health || fail "live bridge health failed"
fi
