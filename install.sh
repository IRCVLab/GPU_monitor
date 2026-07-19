#!/usr/bin/env bash
# storage-viz central dashboard installer.
# Dry-run/syntax-check without privileged writes: ./install.sh --dry-run
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_ROOT="${STORAGE_VIZ_SOURCE_ROOT:-$SCRIPT_DIR}"
SOURCE_ROOT="$(cd "$SOURCE_ROOT" && pwd)"

PREFIX="${PREFIX:-/}"
DRY_RUN=0
ENABLE_SERVICE=1
DASHBOARD_ROOT="${STORAGE_VIZ_DASHBOARD_ROOT:-/opt/storage-viz-dashboard}"
CONFIG_DIR="${STORAGE_VIZ_CONFIG_DIR:-/etc/storage-viz}"
INVENTORY_FILE="${STORAGE_VIZ_INVENTORY:-$CONFIG_DIR/servers.json}"
KEY_DIR="${STORAGE_VIZ_KEY_DIR:-$CONFIG_DIR/keys}"
KNOWN_HOSTS_FILE="${STORAGE_VIZ_KNOWN_HOSTS_FILE:-$CONFIG_DIR/known_hosts}"
DATA_DIR="${STORAGE_VIZ_DATA_DIR:-/var/lib/storage-viz-dashboard/data}"
STATE_DIR="${STORAGE_VIZ_STATE_DIR:-/var/lib/storage-viz-dashboard/state}"
DASHBOARD_BIND="${STORAGE_VIZ_BIND:-127.0.0.1}"
DASHBOARD_PORT="${STORAGE_VIZ_PORT:-8088}"
DASHBOARD_USER="${STORAGE_VIZ_DASHBOARD_USER:-storage-viz}"
DASHBOARD_GROUP="${STORAGE_VIZ_DASHBOARD_GROUP:-storage-viz}"
UNIT_DIR="${UNIT_DIR:-/etc/systemd/system}"
SYSTEMCTL="${SYSTEMCTL:-systemctl}"

usage() {
  cat <<USAGE
Usage: $0 [--dry-run] [--no-enable] [--prefix PATH]

Installs the central storage-viz dashboard only. Remote per-server agent setup is handled by the deploy scripts.

Environment overrides:
  STORAGE_VIZ_DASHBOARD_ROOT=/opt/storage-viz-dashboard  central app directory
  STORAGE_VIZ_CONFIG_DIR=/etc/storage-viz                 central config directory
  STORAGE_VIZ_INVENTORY=/etc/storage-viz/servers.json     server inventory file
  STORAGE_VIZ_KEY_DIR=/etc/storage-viz/keys               SSH identity directory
  STORAGE_VIZ_KNOWN_HOSTS_FILE=/etc/storage-viz/known_hosts strict known-hosts file
  STORAGE_VIZ_DATA_DIR=/var/lib/storage-viz-dashboard/data pulled snapshots
  STORAGE_VIZ_STATE_DIR=/var/lib/storage-viz-dashboard/state central runtime state
  STORAGE_VIZ_BIND=127.0.0.1                              loopback bind address
  STORAGE_VIZ_PORT=8088                                   dashboard port
  STORAGE_VIZ_DASHBOARD_USER=storage-viz                  service user
  STORAGE_VIZ_DASHBOARD_GROUP=storage-viz                 service group
  UNIT_DIR=/etc/systemd/system                            systemd unit output directory

Dry-run renders files under a temporary or requested prefix, verifies syntax when
tools are available, and does not call systemctl, connect to remote hosts, or
start scans.
USAGE
}

prefix_path() {
  local path="$1"
  if [[ "$PREFIX" == "/" ]]; then
    printf '%s\n' "$path"
  else
    printf '%s/%s\n' "${PREFIX%/}" "${path#/}"
  fi
}

install_file() {
  local src="$1" dest="$2" mode="$3"
  mkdir -p "$(dirname "$dest")"
  cp "$src" "$dest"
  chmod "$mode" "$dest"
}

render_service() {
  local dest="$1"
  sed \
    -e "s#/opt/storage-viz-dashboard#$DASHBOARD_ROOT#g" \
    -e "s#/etc/storage-viz/servers.json#$INVENTORY_FILE#g" \
    -e "s#/var/lib/storage-viz-dashboard/data#$DATA_DIR#g" \
    -e "s#/var/lib/storage-viz-dashboard/state#$STATE_DIR#g" \
    -e "s#STORAGE_VIZ_BIND=127.0.0.1#STORAGE_VIZ_BIND=$DASHBOARD_BIND#g" \
    -e "s#STORAGE_VIZ_PORT=8088#STORAGE_VIZ_PORT=$DASHBOARD_PORT#g" \
    -e "s#User=storage-viz#User=$DASHBOARD_USER#g" \
    -e "s#Group=storage-viz#Group=$DASHBOARD_GROUP#g" \
    "$SOURCE_ROOT/deploy/systemd/storage-viz-dashboard.service.in" > "$dest"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1 ;;
    --no-enable) ENABLE_SERVICE=0 ;;
    --prefix) shift; [[ $# -gt 0 ]] || { echo "ERROR: --prefix requires a value" >&2; exit 2; }; PREFIX="$1" ;;
    -h|--help) usage; exit 0 ;;
    *) echo "ERROR: unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

if [[ "$DRY_RUN" == "1" ]]; then
  ENABLE_SERVICE=0
  if [[ "$PREFIX" == "/" ]]; then
    PREFIX="$(mktemp -d "${TMPDIR:-/tmp}/storage-viz-dashboard-install.XXXXXX")"
  fi
elif [[ "${STORAGE_VIZ_INSTALL_TEST_ASSUME_ROOT:-0}" != "1" && $EUID -ne 0 ]]; then
  echo "ERROR: run as root for a real install, or use --dry-run for local verification" >&2
  exit 1
fi

UNIT_DEST="$(prefix_path "$UNIT_DIR/storage-viz-dashboard.service")"
APP_DEST="$(prefix_path "$DASHBOARD_ROOT")"
CONFIG_DEST="$(prefix_path "$CONFIG_DIR")"
KEY_DEST="$(prefix_path "$KEY_DIR")"
KNOWN_HOSTS_DEST="$(prefix_path "$KNOWN_HOSTS_FILE")"
DATA_DEST="$(prefix_path "$DATA_DIR")"
STATE_DEST="$(prefix_path "$STATE_DIR")"
ENV_DEST="$(prefix_path "$CONFIG_DIR/dashboard.env")"
INV_DEST="$(prefix_path "$INVENTORY_FILE")"

printf '[*] source      : %s\n' "$SOURCE_ROOT"
printf '[*] app root    : %s\n' "$DASHBOARD_ROOT"
printf '[*] bind/port   : %s:%s\n' "$DASHBOARD_BIND" "$DASHBOARD_PORT"
printf '[*] inventory   : %s\n' "$INVENTORY_FILE"
printf '[*] data/state  : %s / %s\n' "$DATA_DIR" "$STATE_DIR"
printf '[*] ssh paths   : %s / %s\n' "$KEY_DIR" "$KNOWN_HOSTS_FILE"
[[ "$DRY_RUN" == "1" ]] && printf '[*] mode        : dry-run under %s\n' "$PREFIX"

mkdir -p "$APP_DEST" "$CONFIG_DEST" "$KEY_DEST" "$(dirname "$KNOWN_HOSTS_DEST")" "$DATA_DEST" "$STATE_DEST" "$(dirname "$UNIT_DEST")"

if [[ "$SOURCE_ROOT" != "$APP_DEST" ]]; then
  for item in collector config docs viewer README.md; do
    if [[ -e "$SOURCE_ROOT/$item" ]]; then
      rm -rf "$APP_DEST/$item"
      cp -R "$SOURCE_ROOT/$item" "$APP_DEST/$item"
    fi
  done
  mkdir -p "$APP_DEST/data"
  for sample in "$SOURCE_ROOT"/data/hosts.json "$SOURCE_ROOT"/data/*.sample.json; do
    [[ -e "$sample" ]] || continue
    install_file "$sample" "$APP_DEST/data/$(basename "$sample")" 0644
  done
  install_file "$SOURCE_ROOT/install.sh" "$APP_DEST/install.sh" 0755
fi
render_service "$UNIT_DEST"
chmod 0644 "$UNIT_DEST"

if [[ ! -e "$INV_DEST" ]]; then
  install_file "$SOURCE_ROOT/config/servers.example.yaml" "$INV_DEST" 0640
fi
if [[ ! -e "$KNOWN_HOSTS_DEST" ]]; then
  : > "$KNOWN_HOSTS_DEST"
  chmod 0644 "$KNOWN_HOSTS_DEST"
fi
if [[ ! -e "$ENV_DEST" ]]; then
  cat > "$ENV_DEST" <<ENVEOF
# Required when exposing operator actions behind an authenticating reverse proxy.
# Set exact origins and operator identities for the deployment.
# STORAGE_VIZ_ALLOWED_ORIGINS=https://storage.example.test
# STORAGE_VIZ_OPERATOR_ALLOWLIST=operator-1,operator-2
ENVEOF
  chmod 0640 "$ENV_DEST"
fi

if command -v python3 >/dev/null 2>&1; then
  python3 -m py_compile "$APP_DEST/viewer/serve.py" "$APP_DEST/collector/inventory.py" "$APP_DEST/collector/service.py" "$APP_DEST/collector/transport.py"
fi
if command -v systemd-analyze >/dev/null 2>&1; then
  systemd-analyze verify "$UNIT_DEST"
fi

if [[ "$DRY_RUN" == "1" ]]; then
  cat <<DRYRUN

[✓] dry-run complete. Rendered central dashboard assets:
    $UNIT_DEST
    $INV_DEST
    $ENV_DEST

No systemctl calls, remote connections, unrelated-service changes, or scans were performed.
DRYRUN
  exit 0
fi

if command -v getent >/dev/null 2>&1 && ! getent group "$DASHBOARD_GROUP" >/dev/null; then
  groupadd --system "$DASHBOARD_GROUP"
fi
if command -v id >/dev/null 2>&1 && ! id "$DASHBOARD_USER" >/dev/null 2>&1; then
  useradd --system --home-dir "$DASHBOARD_ROOT" --shell /usr/sbin/nologin --gid "$DASHBOARD_GROUP" "$DASHBOARD_USER"
fi
chown -R "$DASHBOARD_USER:$DASHBOARD_GROUP" "$DATA_DEST" "$STATE_DEST"
chown -R "root:$DASHBOARD_GROUP" "$CONFIG_DEST"
chmod 0750 "$KEY_DEST"

if [[ "$ENABLE_SERVICE" == "1" ]]; then
  "$SYSTEMCTL" daemon-reload
  "$SYSTEMCTL" enable --now storage-viz-dashboard.service
fi

cat <<DONE

[✓] central dashboard installed.
    service   : storage-viz-dashboard.service
    dashboard : http://127.0.0.1:$DASHBOARD_PORT/ (publish through an authenticating reverse proxy)
    inventory : $INVENTORY_FILE
    logs      : journalctl -u storage-viz-dashboard.service
DONE
