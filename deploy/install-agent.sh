#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DRY_RUN=0
PREFIX="/"
SERVER_ID="$(hostname -s 2>/dev/null || printf 'storage-viz-host')"
SYSTEMCTL="${SYSTEMCTL:-/usr/bin/systemctl}"
PYTHON_BIN="${PYTHON_BIN:-/usr/bin/python3}"
GROUP_NAME="storage-viz-collector"
MONITORING_USER="monitoring"

usage() {
  cat <<USAGE
Usage: $0 [--dry-run] [--prefix DIR] [--server-id ID]

Installs the hardened storage-viz scan agent. Dry-run renders into a temp prefix
(default) or supplied --prefix and performs no privileged or systemd changes.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1 ;;
    --prefix) [[ $# -ge 2 ]] || { echo "ERROR: --prefix requires a directory" >&2; exit 2; }; PREFIX="$2"; shift ;;
    --server-id) [[ $# -ge 2 ]] || { echo "ERROR: --server-id requires a value" >&2; exit 2; }; SERVER_ID="$2"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "ERROR: unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

if [[ ! "$SERVER_ID" =~ ^[A-Za-z0-9_.-]+$ ]]; then
  echo "ERROR: unsafe server_id; expected ^[A-Za-z0-9_.-]+$" >&2
  exit 2
fi

if [[ "$DRY_RUN" == "1" && "$PREFIX" == "/" ]]; then
  PREFIX="$(mktemp -d "${TMPDIR:-/tmp}/storage-viz-agent.XXXXXX")"
fi

if [[ "$DRY_RUN" != "1" && $EUID -ne 0 ]]; then
  if [[ "${STORAGE_VIZ_INSTALL_TEST_ASSUME_ROOT:-0}" == "1" && "$PREFIX" != "/" ]]; then
    : "test-only fake-root mode for temp-prefix verification"
  else
    echo "ERROR: real install requires root; rerun as root or use --dry-run" >&2
    exit 1
  fi
fi

case "$PREFIX" in
  /*) ;;
  *) echo "ERROR: --prefix must be absolute" >&2; exit 2 ;;
esac

require_source() {
  local path="$1"
  [[ -e "$path" ]] || { echo "ERROR: missing required source: $path" >&2; exit 1; }
}

require_source "$REPO_ROOT/agent/scan_runner.py"
require_source "$REPO_ROOT/agent/mount_policy.py"
require_source "$REPO_ROOT/scanner/hstscan.c"
require_source "$SCRIPT_DIR/systemd/storage-viz-scan.service.in"
require_source "$SCRIPT_DIR/systemd/storage-viz-scan.timer"
require_source "$SCRIPT_DIR/sudoers/storage-viz-monitoring"

prefix_path() {
  local absolute="$1"
  if [[ "$PREFIX" == "/" ]]; then
    printf '%s' "$absolute"
  else
    printf '%s%s' "$PREFIX" "$absolute"
  fi
}

atomic_write() {
  local dest="$1" mode="$2" owner_group="${3:-}"
  local dir tmp
  dir="$(dirname "$dest")"
  mkdir -p "$dir"
  tmp="$(mktemp "$dir/.tmp.XXXXXX")"
  cat > "$tmp"
  chmod "$mode" "$tmp"
  if [[ -n "$owner_group" && "$DRY_RUN" != "1" ]]; then
    chown "$owner_group" "$tmp"
  fi
  mv "$tmp" "$dest"
}

copy_tree() {
  local src="$1" dest="$2" mode="$3"
  mkdir -p "$dest"
  find "$src" -maxdepth 1 -type f | while IFS= read -r file; do
    install -m "$mode" "$file" "$dest/$(basename "$file")"
  done
}

OPT_DIR="$(prefix_path /opt/storage-viz)"
CONFIG_DIR="$(prefix_path /etc/storage-viz)"
CONFIG_FILE="$CONFIG_DIR/scanner.yaml"
DATA_DIR="$(prefix_path /var/lib/storage-viz)"
SNAPSHOT_DIR="$DATA_DIR/snapshots"
RUN_DIR="$(prefix_path /run/storage-viz)"
UNIT_DIR="$(prefix_path /etc/systemd/system)"
SUDOERS_DIR="$(prefix_path /etc/sudoers.d)"
SUDOERS_FILE="$SUDOERS_DIR/storage-viz-monitoring"
SCANNER_BIN="$OPT_DIR/scanner/hstscan"

printf '[*] mode: %s\n' "$([[ "$DRY_RUN" == "1" ]] && echo dry-run || echo install)"
printf '[*] prefix: %s\n' "$PREFIX"
printf '[*] server_id: %s\n' "$SERVER_ID"

if [[ "$DRY_RUN" != "1" ]]; then
  if ! id "$MONITORING_USER" >/dev/null 2>&1; then
    echo "ERROR: required existing monitoring account not found; provision a command-restricted SSH account first" >&2
    exit 1
  fi
  if ! getent group "$GROUP_NAME" >/dev/null 2>&1; then
    groupadd --system "$GROUP_NAME"
  fi
  usermod -a -G "$GROUP_NAME" "$MONITORING_USER"
fi

mkdir -p "$OPT_DIR/agent" "$OPT_DIR/scanner" "$CONFIG_DIR" "$DATA_DIR" "$SNAPSHOT_DIR" "$RUN_DIR" "$UNIT_DIR" "$SUDOERS_DIR"
copy_tree "$REPO_ROOT/agent" "$OPT_DIR/agent" 0644
install -m 0644 "$REPO_ROOT/scanner/hstscan.c" "$OPT_DIR/scanner/hstscan.c"
if [[ "$DRY_RUN" == "1" ]]; then
  atomic_write "$SCANNER_BIN" 0755 <<'STUB'
#!/usr/bin/env sh
echo "dry-run scanner placeholder" >&2
exit 1
STUB
else
  make -C "$REPO_ROOT/scanner" clean hstscan
  install -m 0755 "$REPO_ROOT/scanner/hstscan" "$SCANNER_BIN"
fi

if [[ "$DRY_RUN" != "1" ]]; then
  chown -R root:root "$OPT_DIR"
  chown root:"$GROUP_NAME" "$DATA_DIR" "$SNAPSHOT_DIR" "$RUN_DIR"
  chmod 0755 "$OPT_DIR" "$OPT_DIR/agent" "$OPT_DIR/scanner"
  chmod 0750 "$DATA_DIR" "$SNAPSHOT_DIR" "$RUN_DIR"
else
  chmod 0750 "$DATA_DIR" "$SNAPSHOT_DIR" "$RUN_DIR"
fi

atomic_write "$CONFIG_FILE" 0644 root:root <<JSON
{
  "server_id": "$SERVER_ID",
  "scanner_path": "/opt/storage-viz/scanner/hstscan",
  "data_dir": "/var/lib/storage-viz",
  "run_dir": "/run/storage-viz",
  "threads": 4,
  "prune_home_mb": 50,
  "prune_data_mb": 100,
  "top": 200,
  "stale_days": 180
}
JSON

sed \
  -e "s#/usr/bin/python3#$PYTHON_BIN#g" \
  "$SCRIPT_DIR/systemd/storage-viz-scan.service.in" | atomic_write "$UNIT_DIR/storage-viz-scan.service" 0644 root:root
install -m 0644 "$SCRIPT_DIR/systemd/storage-viz-scan.timer" "$UNIT_DIR/storage-viz-scan.timer"
install -m 0440 "$SCRIPT_DIR/sudoers/storage-viz-monitoring" "$SUDOERS_FILE"

python3 -m json.tool "$CONFIG_FILE" >/dev/null

if command -v visudo >/dev/null 2>&1; then
  visudo -cf "$SUDOERS_FILE"
else
  echo "[!] visudo not found; skipped sudoers validation"
fi

if command -v systemd-analyze >/dev/null 2>&1; then
  systemd-analyze verify "$UNIT_DIR/storage-viz-scan.service" "$UNIT_DIR/storage-viz-scan.timer"
else
  echo "[!] systemd-analyze not found; skipped unit validation"
fi

if [[ "$DRY_RUN" == "1" ]]; then
  echo "[✓] dry-run rendered agent install under $PREFIX"
  exit 0
fi

"$SYSTEMCTL" daemon-reload
"$SYSTEMCTL" enable --now storage-viz-scan.timer
echo "[✓] installed storage-viz scan agent; timer enabled and started"
