#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
MONOREPO_ROOT="$(cd "$ROOT/../.." && pwd)"
DEPLOY="$ROOT/deploy"
SERVER="$DEPLOY/server"
DRY_RUN=0
PREFIX=""
SYSTEMCTL="${SYSTEMCTL:-systemctl}"

LIBEXEC_DIR="/usr/local/libexec"
RELEASE_ROOT="/srv/storage-viz-dashboard/releases"
STATE_ROOT="/var/lib/storage-viz-dashboard"
# Storage-only state directories: /var/lib/storage-viz-dashboard/puller /var/lib/storage-viz-dashboard/builder /var/lib/storage-viz-dashboard/data /var/lib/storage-viz-dashboard/state
CONFIG_DIR="/etc/storage-viz"
APP_PATH="/opt/storage-viz-dashboard"
RUNTIME_USER="storage"
RUNTIME_GROUP="storage"
BUILDER_USER="storage-viz-builder"
BUILDER_GROUP="storage-viz-builder"

usage() {
  cat <<USAGE
Usage: $0 [--dry-run] [--prefix PATH]

Installs Storage-owned dashboard release deployer assets. Real mode requires root.
Dry-run renders files under --prefix and performs no network, systemd, user, or service actions.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    --prefix) PREFIX="${2:?missing --prefix value}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ "$DRY_RUN" != 1 && "${STORAGE_VIZ_INSTALL_TEST_ASSUME_ROOT:-0}" != 1 && "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "real install requires root; use --dry-run --prefix for non-root rendering" >&2
  exit 1
fi
if [[ "$DRY_RUN" == 1 && -z "$PREFIX" ]]; then
  echo "--dry-run requires --prefix" >&2
  exit 2
fi
if [[ -n "$PREFIX" && "$PREFIX" != /* ]]; then
  echo "--prefix must be absolute" >&2
  exit 2
fi

prefix_path() {
  local path="$1"
  if [[ -n "$PREFIX" ]]; then
    printf '%s%s' "$PREFIX" "$path"
  else
    printf '%s' "$path"
  fi
}

sha256_file() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  else
    shasum -a 256 "$1" | awk '{print $1}'
  fi
}

install_file() {
  local src="$1" dest="$2" mode="$3" owner="$4" digest copied_digest actual_dest
  digest="$(sha256_file "$src")"
  actual_dest="$(prefix_path "$dest")"
  mkdir -p "$(dirname "$actual_dest")"
  install -m "$mode" "$src" "$actual_dest"
  copied_digest="$(sha256_file "$actual_dest")"
  if [[ "$copied_digest" != "$digest" ]]; then
    echo "hash mismatch after installing $dest" >&2
    exit 1
  fi
  printf '%s  %s\n' "$digest" "$actual_dest" >"$actual_dest.sha256"
  chmod 0644 "$actual_dest.sha256"
  if [[ "$DRY_RUN" != 1 && -z "$PREFIX" ]]; then
    chown "$owner" "$actual_dest" "$actual_dest.sha256"
  fi
  printf 'install file: %s owner=%s mode=%s sha256=%s source=%s\n' "$dest" "$owner" "$mode" "$digest" "$src"
}

ensure_dir() {
  local dest="$1" mode="$2" owner="$3" actual_dest
  actual_dest="$(prefix_path "$dest")"
  mkdir -p "$actual_dest"
  chmod "$mode" "$actual_dest"
  if [[ "$DRY_RUN" != 1 && -z "$PREFIX" ]]; then
    chown "$owner" "$actual_dest"
  fi
  printf 'ensure dir: %s owner=%s mode=%s\n' "$dest" "$owner" "$mode"
}

ensure_identity() {
  local user="$1" group="$2" home="$3"
  if [[ "$DRY_RUN" == 1 || -n "$PREFIX" ]]; then
    printf 'identity action: ensure user=%s group=%s home=%s shell=/usr/sbin/nologin\n' "$user" "$group" "$home"
    return
  fi
  if ! getent group "$group" >/dev/null; then
    groupadd --system "$group"
  fi
  if ! id "$user" >/dev/null 2>&1; then
    useradd --system --gid "$group" --home-dir "$home" --create-home --shell /usr/sbin/nologin "$user"
  fi
}

render_proxy_service() {
  local dest="$1" actual_dest
  actual_dest="$(prefix_path "$dest")"
  mkdir -p "$(dirname "$actual_dest")"
  sed \
    -e 's#ExecStart=.*#ExecStart=/usr/bin/python3.12 /usr/local/libexec/storage-viz-proxy-launcher.py /opt/storage-viz-dashboard/deploy/direct_proxy.py#' \
    "$SERVER/systemd/storage-viz-proxy.service" >"$actual_dest"
  chmod 0644 "$actual_dest"
  if [[ "$DRY_RUN" != 1 && -z "$PREFIX" ]]; then
    chown root:root "$actual_dest"
  fi
  printf 'install file: %s owner=root:root mode=0644 sha256=%s source=%s\n' "$dest" "$(sha256_file "$actual_dest")" "$SERVER/systemd/storage-viz-proxy.service"
}

# Candidate preflight/cutover contract for the first approved activation:
# - legacy dashboard remains 127.0.0.1:8088 and current public proxy remains :505.
# - candidate dashboard binds 127.0.0.1:18088 with production inventory, temporary data/state, and preflight mode.
# - candidate proxy binds 127.0.0.1:1505 targeting 127.0.0.1:18088 while preserving real public Host/Origin :505.
# - health probes are nonmutating: session, inventory, and UNKNOWN_SERVER rescan through candidate topology.
# - first_cutover_order: stop_exact_current_505_owner -> stop_legacy_8088_dashboard -> activate_release -> start_managed_8088_505.
# - rollback uses the recorded protected legacy backup, previous dashboard/inventory GET health on :505 is required,
#   and rollback must not recreate tmux / do not recreate tmux.
# - exact PID and port owner validation is required; broad process killers are forbidden.
prepare_candidate_preflight_contract() { : candidate 18088 1505 preflight UNKNOWN_SERVER 505 127.0.0.1:8088; }
first_cutover_order() { : stop_exact_current_505_owner stop_legacy_8088_dashboard activate_release start_managed_8088_505; }
rollback_to_protected_legacy_backup_contract() { : rollback protected legacy backup previous dashboard/inventory GET health not recreate tmux; }

ensure_identity "$BUILDER_USER" "$BUILDER_GROUP" "$STATE_ROOT/builder"
ensure_identity "$RUNTIME_USER" "$RUNTIME_GROUP" "$STATE_ROOT"

ensure_dir "$RELEASE_ROOT" 0755 root:root
ensure_dir "$STATE_ROOT" 0750 root:"$RUNTIME_GROUP"
ensure_dir "$STATE_ROOT/puller" 0750 root:root
ensure_dir "$STATE_ROOT/builder" 0750 "$BUILDER_USER":"$BUILDER_GROUP"
ensure_dir "$STATE_ROOT/data" 0750 "$RUNTIME_USER":"$RUNTIME_GROUP"
ensure_dir "$STATE_ROOT/state" 0750 "$RUNTIME_USER":"$RUNTIME_GROUP"
ensure_dir "$CONFIG_DIR" 0750 root:"$RUNTIME_GROUP"
ensure_dir "$LIBEXEC_DIR" 0755 root:root

# Preserve existing /etc/storage-viz inventory, keys, known_hosts; preserve data/state and real /opt until activation.
install_file "$DEPLOY/build-dashboard-release.py" "$LIBEXEC_DIR/storage-dashboard-build-release.py" 0755 root:root
install_file "$SERVER/activate-dashboard-release.py" "$LIBEXEC_DIR/storage-dashboard-activate.py" 0755 root:root
install_file "$SERVER/health-check-dashboard.py" "$LIBEXEC_DIR/storage-dashboard-health-check.py" 0755 root:root
install_file "$SERVER/storage-viz-proxy-launcher.py" "$LIBEXEC_DIR/storage-viz-proxy-launcher.py" 0755 root:root
install_file "$SERVER/storage-monitor-release-puller.py" "$LIBEXEC_DIR/storage-monitor-release-puller.py" 0755 root:root
install_file "$MONOREPO_ROOT/scripts/authorize_gpu_release.py" "$LIBEXEC_DIR/storage-release-authorizer.py" 0755 root:root
install_file "$SERVER/systemd/storage-monitor-release-puller.service" "/etc/systemd/system/storage-monitor-release-puller.service" 0644 root:root
install_file "$SERVER/systemd/storage-monitor-release-puller.timer" "/etc/systemd/system/storage-monitor-release-puller.timer" 0644 root:root
render_proxy_service "/etc/systemd/system/storage-viz-proxy.service"

cat <<ACTIONS
systemd action: daemon-reload
systemd action: enable --now storage-viz-proxy.service
systemd action: enable storage-monitor-release-puller.timer only after approved health
systemd action: do not start storage-monitor-release-puller.service from installer
ACTIONS

if [[ "$DRY_RUN" == 1 || -n "$PREFIX" ]]; then
  exit 0
fi

"$SYSTEMCTL" daemon-reload
"$SYSTEMCTL" enable --now storage-viz-proxy.service
printf 'dashboard deployer installed; release puller timer intentionally not enabled until an approved release passes health\n'
