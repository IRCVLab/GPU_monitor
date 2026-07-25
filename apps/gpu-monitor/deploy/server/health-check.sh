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
    systemctl_command=systemctl
    ss_command=ss
    retries=${GPU_MONITOR_HEALTH_RETRIES:-5}
    sleep_seconds=${GPU_MONITOR_HEALTH_SLEEP_SECONDS:-2}
    ;;
  *)
    fail "usage: health-check.sh <dev|live> | --test-mode <dev|live>"
    ;;
esac
if [[ "$test_mode" != true ]]; then
  systemctl_command=/usr/bin/systemctl
  ss_command=/usr/bin/ss
fi

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

check_websocket() {
  local host=$1 port=$2 path=$3 attempt=1
  while (( attempt <= retries )); do
    if python3 - "$host" "$port" "$path" <<'PY'
import base64
import hashlib
import os
import socket
import sys

host, port_text, path = sys.argv[1:]
port = int(port_text)
key = base64.b64encode(os.urandom(16)).decode("ascii")
request = (
    f"GET {path} HTTP/1.1\r\n"
    f"Host: {host}:{port}\r\n"
    "Connection: Upgrade\r\n"
    "Upgrade: websocket\r\n"
    "Sec-WebSocket-Version: 13\r\n"
    f"Sec-WebSocket-Key: {key}\r\n"
    "\r\n"
).encode("ascii")
expected_accept = base64.b64encode(
    hashlib.sha1(
        (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")
    ).digest()
).decode("ascii")

with socket.create_connection((host, port), timeout=2) as sock:
    sock.settimeout(2)
    sock.sendall(request)
    response = bytearray()
    while b"\r\n\r\n" not in response and len(response) <= 65536:
        chunk = sock.recv(4096)
        if not chunk:
            break
        response.extend(chunk)

head = bytes(response).split(b"\r\n\r\n", 1)[0].decode("latin1")
lines = head.split("\r\n")
if not lines or " 101 " not in lines[0]:
    raise SystemExit(1)
headers = {}
for line in lines[1:]:
    if ":" not in line:
        continue
    name, value = line.split(":", 1)
    headers[name.strip().lower()] = value.strip()
if headers.get("upgrade", "").lower() != "websocket":
    raise SystemExit(1)
if "upgrade" not in headers.get("connection", "").lower():
    raise SystemExit(1)
if headers.get("sec-websocket-accept") != expected_accept:
    raise SystemExit(1)
PY
    then
      return 0
    fi
    if (( attempt < retries )); then
      sleep "$sleep_seconds"
    fi
    attempt=$((attempt + 1))
  done
  return 1
}

managed_unit_snapshot() {
  local unit pid snapshot=
  local units=(
    "gpu-monitor-backend@${env_name}.service"
    "gpu-monitor-frontend@${env_name}.service"
  )
  if [[ "$env_name" == live ]]; then
    units+=("gpu-monitor-bridge@live.service")
  fi
  for unit in "${units[@]}"; do
    "$systemctl_command" is-active --quiet "$unit" ||
      fail "managed runtime unit is not active: $unit"
    pid=$("$systemctl_command" show "$unit" --property MainPID --value) ||
      fail "managed runtime unit PID is unavailable: $unit"
    [[ "$pid" =~ ^[0-9]+$ && "$pid" -gt 1 ]] ||
      fail "managed runtime unit has no valid main PID: $unit"
    snapshot+="${unit}:${pid}"$'\n'
  done
  printf '%s' "$snapshot"
}

check_listener_binding() {
  local expected_host=$1 port=$2 label=$3 output attempt=1
  local state recv_q send_q local_address peer_address remainder
  while (( attempt <= retries )); do
    output=$("$ss_command" -H -ltn "sport = :$port") ||
      fail "$label listener inspection failed"
    while read -r state recv_q send_q local_address peer_address remainder; do
      [[ "$local_address" == "$expected_host:$port" ]] && return 0
    done <<< "$output"
    if (( attempt < retries )); then
      sleep "$sleep_seconds"
    fi
    attempt=$((attempt + 1))
  done
  fail "$label listener is not bound to $expected_host:$port"
}

before_units=$(managed_unit_snapshot)
if [[ "$env_name" == dev ]]; then
  check_listener_binding 127.0.0.1 8101 "dev backend"
  check_listener_binding 127.0.0.1 5174 "dev frontend"
  check_url http://127.0.0.1:8101/health || fail "dev backend health failed"
  check_url http://127.0.0.1:5174/ || fail "dev frontend health failed"
  check_url http://127.0.0.1:5174/api/health || fail "dev frontend API proxy health failed"
  check_websocket 127.0.0.1 5174 /ws/metrics || fail "dev frontend WebSocket proxy health failed"
else
  check_listener_binding 127.0.0.1 8001 "live backend"
  check_listener_binding 0.0.0.0 5173 "live frontend"
  check_listener_binding 0.0.0.0 8000 "live bridge"
  check_url http://127.0.0.1:8001/health || fail "live backend health failed"
  check_url http://127.0.0.1:5173/ || fail "live frontend health failed"
  check_url http://127.0.0.1:5173/api/health || fail "live frontend API proxy health failed"
  check_websocket 127.0.0.1 5173 /ws/metrics || fail "live frontend WebSocket proxy health failed"
  check_url http://127.0.0.1:8000/health || fail "live bridge health failed"
fi
sleep 1
after_units=$(managed_unit_snapshot)
[[ "$after_units" == "$before_units" ]] ||
  fail "managed runtime units restarted during health verification"
