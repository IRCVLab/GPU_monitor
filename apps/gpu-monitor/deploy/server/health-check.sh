#!/usr/bin/env bash
set -euo pipefail

fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
[[ $# -eq 1 ]] || fail "usage: health-check.sh <dev|live>"
env_name=$1
case "$env_name" in dev|live) ;; *) fail "invalid environment" ;; esac

PATH="${GPU_MONITOR_TEST_PATH:-/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin}"
export PATH
retries=${GPU_MONITOR_HEALTH_RETRIES:-5}
sleep_seconds=${GPU_MONITOR_HEALTH_SLEEP_SECONDS:-2}

check_url() {
  local url=$1 attempt
  attempt=1
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
