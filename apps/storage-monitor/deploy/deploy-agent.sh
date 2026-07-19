#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DRY_RUN=0
START_SCAN=0
HOST=""
PORT=22
RUNTIME_USER="monitoring"
ADMIN_USER="shchoi"
IDENTITY_FILE="${STORAGE_VIZ_IDENTITY_FILE:-$HOME/.ssh/storage-viz_ed25519}"
KNOWN_HOSTS_FILE="${STORAGE_VIZ_KNOWN_HOSTS_FILE:-$HOME/.ssh/storage-viz_known_hosts}"
CONNECT_TIMEOUT="${STORAGE_VIZ_CONNECT_TIMEOUT:-10}"
SERVER_ID=""
REMOTE_TMP=""
LIST_SUDO=(LC_ALL=C sudo -n -l)
START_CMD=(sudo -n /usr/bin/systemctl start storage-viz-scan.service)
APPROVED_SUDO_ENTRY="(root) NOPASSWD: /usr/bin/systemctl start storage-viz-scan.service"

usage() {
  cat <<USAGE
Usage: $0 --host HOST [--port PORT] [--identity-file FILE] [--known-hosts-file FILE]
          [--runtime-user monitoring] [--admin-user shchoi] [--server-id ID]
          [--start-scan] [--dry-run]

Default deployment is side-effect-free with respect to scans: it verifies the
fixed monitoring permission and installs when needed, but does not start a scan
unless --start-scan is explicitly supplied.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1 ;;
    --start-scan) START_SCAN=1 ;;
    --host) [[ $# -ge 2 ]] || { echo "ERROR: --host requires a value" >&2; exit 2; }; HOST="$2"; shift ;;
    --port) [[ $# -ge 2 ]] || { echo "ERROR: --port requires a value" >&2; exit 2; }; PORT="$2"; shift ;;
    --runtime-user) [[ $# -ge 2 ]] || { echo "ERROR: --runtime-user requires a value" >&2; exit 2; }; RUNTIME_USER="$2"; shift ;;
    --admin-user) [[ $# -ge 2 ]] || { echo "ERROR: --admin-user requires a value" >&2; exit 2; }; ADMIN_USER="$2"; shift ;;
    --identity-file) [[ $# -ge 2 ]] || { echo "ERROR: --identity-file requires a file" >&2; exit 2; }; IDENTITY_FILE="$2"; shift ;;
    --known-hosts-file) [[ $# -ge 2 ]] || { echo "ERROR: --known-hosts-file requires a file" >&2; exit 2; }; KNOWN_HOSTS_FILE="$2"; shift ;;
    --connect-timeout) [[ $# -ge 2 ]] || { echo "ERROR: --connect-timeout requires seconds" >&2; exit 2; }; CONNECT_TIMEOUT="$2"; shift ;;
    --server-id) [[ $# -ge 2 ]] || { echo "ERROR: --server-id requires a value" >&2; exit 2; }; SERVER_ID="$2"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "ERROR: unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

valid_user_re='^[A-Za-z_][A-Za-z0-9_.-]*$'
valid_host() {
  local host="$1" label octets
  [[ -n "$host" && ${#host} -le 253 ]] || return 1
  if [[ "$host" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]]; then
    IFS=. read -r -a octets <<< "$host"
    for label in "${octets[@]}"; do
      ((10#$label <= 255)) || return 1
    done
    return 0
  fi
  [[ "$host" != *..* ]] || return 1
  IFS=. read -r -a labels <<< "$host"
  for label in "${labels[@]}"; do
    [[ -n "$label" && ${#label} -le 63 ]] || return 1
    [[ "$label" =~ ^[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?$ ]] || return 1
  done
}
if ! valid_host "$HOST"; then
  echo "ERROR: invalid HOST" >&2
  exit 2
fi
if [[ ! "$PORT" =~ ^[0-9]+$ || "$PORT" -lt 1 || "$PORT" -gt 65535 ]]; then
  echo "ERROR: invalid PORT" >&2
  exit 2
fi
if [[ ! "$RUNTIME_USER" =~ $valid_user_re || ! "$ADMIN_USER" =~ $valid_user_re ]]; then
  echo "ERROR: invalid user" >&2
  exit 2
fi
if [[ ! "$CONNECT_TIMEOUT" =~ ^[0-9]+$ || "$CONNECT_TIMEOUT" -lt 1 || "$CONNECT_TIMEOUT" -gt 120 ]]; then
  echo "ERROR: invalid connect timeout" >&2
  exit 2
fi
if [[ -n "$SERVER_ID" && ! "$SERVER_ID" =~ ^[A-Za-z0-9_.-]+$ ]]; then
  echo "ERROR: invalid server id" >&2
  exit 2
fi
case "$IDENTITY_FILE" in /*) ;; *) echo "ERROR: identity file path must be absolute" >&2; exit 2 ;; esac
case "$KNOWN_HOSTS_FILE" in /*) ;; *) echo "ERROR: known-hosts file path must be absolute" >&2; exit 2 ;; esac
if [[ ! -f "$IDENTITY_FILE" ]]; then
  echo "ERROR: missing identity file: $IDENTITY_FILE" >&2
  exit 2
fi
if [[ ! -f "$KNOWN_HOSTS_FILE" ]]; then
  echo "ERROR: missing known-hosts file: $KNOWN_HOSTS_FILE" >&2
  exit 2
fi

canonical_path() {
  python3 - "$1" <<'PY'
import os, sys
print(os.path.realpath(sys.argv[1]))
PY
}

path_is_under() {
  local child="$1" parent="$2"
  [[ "$child" == "$parent" || "$child" == "$parent"/* ]]
}

validate_secret_file_outside_viewer() {
  local path="$1" name="$2" canonical viewer_root
  canonical="$(canonical_path "$path")"
  viewer_root="$(canonical_path "$REPO_ROOT/viewer")"
  if path_is_under "$canonical" "$viewer_root"; then
    echo "ERROR: $name must resolve outside the served viewer web root" >&2
    exit 2
  fi
}
validate_secret_file_outside_viewer "$IDENTITY_FILE" "identity file"
validate_secret_file_outside_viewer "$KNOWN_HOSTS_FILE" "known-hosts file"

MONITOR_DEST="$RUNTIME_USER@$HOST"
ADMIN_DEST="$ADMIN_USER@$HOST"
SSH_BASE=(ssh -p "$PORT" -i "$IDENTITY_FILE" -o StrictHostKeyChecking=yes -o IdentitiesOnly=yes -o UserKnownHostsFile="$KNOWN_HOSTS_FILE" -o ConnectTimeout="$CONNECT_TIMEOUT")
SSH_MONITOR=("${SSH_BASE[@]}" -o BatchMode=yes "$MONITOR_DEST")
SSH_ADMIN=("${SSH_BASE[@]}" -t "$ADMIN_DEST")
SCP_BASE=(scp -P "$PORT" -i "$IDENTITY_FILE" -o StrictHostKeyChecking=yes -o IdentitiesOnly=yes -o UserKnownHostsFile="$KNOWN_HOSTS_FILE" -o ConnectTimeout="$CONNECT_TIMEOUT")
SSH_BASE_REDACTED=(ssh -p "$PORT" -i "[identity-file]" -o StrictHostKeyChecking=yes -o IdentitiesOnly=yes -o UserKnownHostsFile="[known-hosts-file]" -o ConnectTimeout="$CONNECT_TIMEOUT")
SSH_MONITOR_REDACTED=("${SSH_BASE_REDACTED[@]}" -o BatchMode=yes "$MONITOR_DEST")
SSH_ADMIN_REDACTED=("${SSH_BASE_REDACTED[@]}" -t "$ADMIN_DEST")

print_argv() {
  local label="$1"; shift
  printf '%s:' "$label"
  local arg
  for arg in "$@"; do printf ' %q' "$arg"; done
  printf '\n'
}

remote_tmp_valid() {
  [[ "$1" =~ ^/tmp/storage-viz-agent-bootstrap\.[A-Za-z0-9._-]+$ ]]
}

remote_cleanup_tmp() {
  if [[ -n "$REMOTE_TMP" ]] && remote_tmp_valid "$REMOTE_TMP"; then
    "${SSH_BASE[@]}" "$ADMIN_DEST" "rm -rf -- '$REMOTE_TMP'" >/dev/null 2>&1 || true
  fi
}

remote_mktemp() {
  local tmp
  tmp="$("${SSH_BASE[@]}" "$ADMIN_DEST" 'umask 077; mktemp -d /tmp/storage-viz-agent-bootstrap.XXXXXX')"
  tmp="${tmp%%$'\r'}"
  tmp="${tmp%%$'\n'*}"
  if ! remote_tmp_valid "$tmp"; then
    echo "ERROR: remote bootstrap temp path failed validation" >&2
    exit 1
  fi
  REMOTE_TMP="$tmp"
}

parse_monitoring_sudo_policy() {
  local policy="$1"
  local line trimmed in_command_list=0
  local approved_entry_count=0 command_entry_count=0
  while IFS= read -r line; do
    trimmed="${line#"${line%%[![:space:]]*}"}"
    trimmed="${trimmed%"${trimmed##*[![:space:]]}"}"
    [[ -z "$trimmed" ]] && continue
    case "$trimmed" in
      Matching\ Defaults*|Defaults\ entries*|Runas\ and\ Command-specific\ defaults*) continue ;;
      User\ *may\ run\ the\ following\ commands*) in_command_list=1; continue ;;
      Sorry,*|sudo:*|lecture*) return 1 ;;
    esac
    if [[ "$in_command_list" == "1" ]]; then
      command_entry_count=$((command_entry_count + 1))
      if [[ "$trimmed" == "$APPROVED_SUDO_ENTRY" ]]; then
        approved_entry_count=$((approved_entry_count + 1))
      else
        return 1
      fi
    fi
  done <<< "$policy"
  [[ "$command_entry_count" -eq 1 && "$approved_entry_count" -eq 1 ]]
}

check_monitoring_permission() {
  local policy_output
  if ! policy_output="$("${SSH_MONITOR[@]}" "${LIST_SUDO[@]}" 2>&1)"; then
    echo "[!] monitoring privilege policy unavailable or not noninteractive" >&2
    return 1
  fi
  if ! parse_monitoring_sudo_policy "$policy_output"; then
    echo "[!] monitoring privilege policy is not the exact approved storage-viz rule" >&2
    return 1
  fi
}

maybe_start_scan() {
  if [[ "$START_SCAN" == "1" ]]; then
    "${SSH_MONITOR[@]}" "${START_CMD[@]}"
  fi
}

if [[ "$DRY_RUN" == "1" ]]; then
  echo "DRY-RUN: no local or remote changes will be made"
  print_argv "monitoring policy listing" "${SSH_MONITOR_REDACTED[@]}" "${LIST_SUDO[@]}"
  print_argv "remote private temp" "${SSH_ADMIN_REDACTED[@]}" "umask 077; mktemp -d /tmp/storage-viz-agent-bootstrap.XXXXXX"
  print_argv "admin bootstrap ssh" "${SSH_ADMIN_REDACTED[@]}" "tmp=\$1; server_id=\$2; set -e; trap 'rm -rf -- \"\$tmp\"' EXIT; tar -xzf \"\$tmp/source.tar.gz\" -C \"\$tmp\"; sudo \"\$tmp/deploy/install-agent.sh\" --server-id \"\$server_id\"" "sh" "/tmp/storage-viz-agent-bootstrap.[redacted]" "${SERVER_ID:-$HOST}"
  print_argv "post-bootstrap policy recheck" "${SSH_MONITOR_REDACTED[@]}" "${LIST_SUDO[@]}"
  if [[ "$START_SCAN" == "1" ]]; then
    print_argv "explicit scan start" "${SSH_MONITOR_REDACTED[@]}" "${START_CMD[@]}"
  else
    echo "scan start: disabled by default (use --start-scan to run)"
  fi
  exit 0
fi

if check_monitoring_permission; then
  echo "[*] monitoring fixed permission verified"
  maybe_start_scan
  exit 0
fi

ARCHIVE="$(mktemp "${TMPDIR:-/tmp}/storage-viz-agent.XXXXXX")"
cleanup() { rm -f "$ARCHIVE"; }
cleanup_all() { cleanup; remote_cleanup_tmp; }
trap cleanup EXIT

tar -C "$REPO_ROOT" \
  --exclude .git \
  --exclude 'data/*.json' \
  --exclude 'scanner/hstscan' \
  -czf "$ARCHIVE" agent scanner deploy config

remote_mktemp
trap cleanup_all EXIT
"${SCP_BASE[@]}" "$ARCHIVE" "$ADMIN_DEST:$REMOTE_TMP/source.tar.gz"
remote_install="tmp='$REMOTE_TMP'; server_id='${SERVER_ID:-$HOST}'; set -e; trap 'rm -rf -- \"\$tmp\"' EXIT; tar -xzf \"\$tmp/source.tar.gz\" -C \"\$tmp\"; sudo \"\$tmp/deploy/install-agent.sh\" --server-id \"\$server_id\""
"${SSH_ADMIN[@]}" "$remote_install"
REMOTE_TMP=""
trap cleanup EXIT

check_monitoring_permission
maybe_start_scan
echo "[*] monitoring runtime permission verified; administrator credentials will not be used for runtime scans"
