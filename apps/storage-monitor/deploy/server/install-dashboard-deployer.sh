#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
MONOREPO_ROOT="$(cd "$ROOT/../.." && pwd)"
DEPLOY="$ROOT/deploy"
SERVER="$DEPLOY/server"
DRY_RUN=0
PREFIX=""
BOOTSTRAP_CUTOVER=0
CANDIDATE_SHA=""
EXPECTED_DIGEST=""
ARTIFACT=""
METADATA=""
SYSTEMCTL="${SYSTEMCTL:-systemctl}"
SS="${SS:-ss}"
CURL="${CURL:-curl}"
PYTHON="${PYTHON:-/usr/bin/python3.12}"
ACTIVATOR="${ACTIVATOR:-/usr/local/libexec/storage-dashboard-activate.py}"
KILL="${KILL:-kill}"

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
       $0 --bootstrap-cutover --candidate-sha SHA --expected-digest SHA256 --artifact PATH --metadata PATH

Installs Storage-owned dashboard release deployer assets. Real mode requires root.
Dry-run renders files under --prefix and performs no network, systemd, user, or service actions.
Bootstrap cutover is a separate approved-candidate path; it validates candidate 127.0.0.1:18088/1505 before touching live 8088/505.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    --prefix) PREFIX="${2:?missing --prefix value}"; shift 2 ;;
    --bootstrap-cutover) BOOTSTRAP_CUTOVER=1; shift ;;
    --candidate-sha) CANDIDATE_SHA="${2:?missing --candidate-sha value}"; shift 2 ;;
    --expected-digest) EXPECTED_DIGEST="${2:?missing --expected-digest value}"; shift 2 ;;
    --artifact) ARTIFACT="${2:?missing --artifact value}"; shift 2 ;;
    --metadata) METADATA="${2:?missing --metadata value}"; shift 2 ;;
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


validate_hex() {
  local value="$1" len="$2" label="$3"
  [[ "$value" =~ ^[0-9a-f]{$len}$ ]] || { echo "invalid $label" >&2; exit 2; }
}

run_cmd() {
  if [[ "$DRY_RUN" == 1 ]]; then
    printf 'cutover action:'
    printf ' %q' "$@"
    printf '\n'
    return 0
  fi
  "$@"
}

validate_candidate_args() {
  [[ -n "$CANDIDATE_SHA" && -n "$EXPECTED_DIGEST" && -n "$ARTIFACT" && -n "$METADATA" ]] || { echo "bootstrap cutover requires candidate sha, digest, artifact, and metadata" >&2; exit 2; }
  validate_hex "$CANDIDATE_SHA" 40 "candidate sha"
  validate_hex "$EXPECTED_DIGEST" 64 "expected digest"
  [[ -f "$ARTIFACT" && -f "$METADATA" ]] || { echo "candidate artifact and metadata must exist" >&2; exit 2; }
}

cleanup_candidate_topology() {
  [[ -z "${CANDIDATE_PROXY_PID:-}" ]] || "$KILL" -TERM "$CANDIDATE_PROXY_PID" 2>/dev/null || true
  [[ -z "${CANDIDATE_DASH_PID:-}" ]] || "$KILL" -TERM "$CANDIDATE_DASH_PID" 2>/dev/null || true
  [[ -z "${CANDIDATE_TMP:-}" ]] || rm -rf -- "$CANDIDATE_TMP"
}

start_candidate_topology() {
  CANDIDATE_TMP="$(mktemp -d "${TMPDIR:-/tmp}/storage-viz-candidate.XXXXXX")"
  trap cleanup_candidate_topology EXIT
  mkdir -p "$CANDIDATE_TMP/data" "$CANDIDATE_TMP/state"
  if [[ "$DRY_RUN" == 1 ]]; then
    run_cmd "$PYTHON" "$APP_PATH/viewer/serve.py" --bind 127.0.0.1 --port 18088 --inventory "$CONFIG_DIR/servers.json" --data-dir "$CANDIDATE_TMP/data" --state-dir "$CANDIDATE_TMP/state" --preflight --no-polling --no-scans
    STORAGE_VIZ_PROXY_BIND=127.0.0.1 STORAGE_VIZ_PROXY_PORT=1505 STORAGE_VIZ_PROXY_UPSTREAM_HOST=127.0.0.1 STORAGE_VIZ_PROXY_UPSTREAM_PORT=18088 STORAGE_VIZ_PROXY_OPERATOR=fixed-proxy-operator STORAGE_VIZ_PROXY_PUBLIC_ORIGIN=http://127.0.0.1:505 \
      run_cmd "$PYTHON" "$LIBEXEC_DIR/storage-viz-proxy-launcher.py" "$APP_PATH/deploy/direct_proxy.py" --candidate --public-host 127.0.0.1:505
    return
  fi
  "$PYTHON" "$APP_PATH/viewer/serve.py" --bind 127.0.0.1 --port 18088 --inventory "$CONFIG_DIR/servers.json" --data-dir "$CANDIDATE_TMP/data" --state-dir "$CANDIDATE_TMP/state" --preflight --no-polling --no-scans &
  CANDIDATE_DASH_PID="$!"
  STORAGE_VIZ_PROXY_BIND=127.0.0.1 STORAGE_VIZ_PROXY_PORT=1505 STORAGE_VIZ_PROXY_UPSTREAM_HOST=127.0.0.1 STORAGE_VIZ_PROXY_UPSTREAM_PORT=18088 STORAGE_VIZ_PROXY_OPERATOR=fixed-proxy-operator STORAGE_VIZ_PROXY_PUBLIC_ORIGIN=http://127.0.0.1:505 \
    "$PYTHON" "$LIBEXEC_DIR/storage-viz-proxy-launcher.py" "$APP_PATH/deploy/direct_proxy.py" --candidate --public-host 127.0.0.1:505 &
  CANDIDATE_PROXY_PID="$!"
  sleep 0.1
}

probe_candidate() {
  run_cmd "$CURL" -fsS -H 'Host: 127.0.0.1:505' -H 'Origin: http://127.0.0.1:505' http://127.0.0.1:1505/api/session >/dev/null
  run_cmd "$CURL" -fsS -H 'Host: 127.0.0.1:505' http://127.0.0.1:1505/api/servers >/dev/null
  run_cmd "$CURL" -fsS -X POST -H 'Host: 127.0.0.1:505' -H 'Origin: http://127.0.0.1:505' http://127.0.0.1:1505/api/servers/UNKNOWN_SERVER/rescan >/dev/null
}

listener_owner_pid() {
  local port="$1" line pid name out
  out="$($SS -H -ltnp "sport = :$port")"
  [[ -n "$out" ]] || { echo "missing listener for :$port" >&2; return 1; }
  [[ "$(printf '%s\n' "$out" | wc -l | awk '{print $1}')" == 1 ]] || { echo "ambiguous listener for :$port" >&2; return 1; }
  line="$out"
  pid="$(printf '%s' "$line" | sed -nE 's/.*pid=([0-9]+).*/\1/p')"
  name="$(printf '%s' "$line" | sed -nE 's/.*users:\(\("?([^",]+).*/\1/p')"
  [[ "$pid" =~ ^[0-9]+$ ]] || { echo "cannot parse listener PID for :$port" >&2; return 1; }
  case "$name" in
    *storage*|*python*|*direct_proxy*|*tmux*) ;;
    *) echo "unrelated listener owner for :$port: $name" >&2; return 1 ;;
  esac
  printf '%s\n' "$pid"
}

activate_candidate_release() {
  "$ACTIVATOR" --sha "$CANDIDATE_SHA" --expected-digest "$EXPECTED_DIGEST" --artifact-stdin --metadata "$METADATA" \
    --restart-argv /usr/bin/systemctl restart storage-viz-dashboard.service storage-viz-proxy.service \
    --health-argv /usr/local/libexec/storage-dashboard-health-check.py <"$ARTIFACT"
}

rollback_after_post_stop_failure() {
  "$ACTIVATOR" --rollback-state --state-path "$STATE_ROOT/activation-state.json" --app-path "$APP_PATH" || true
  "$SYSTEMCTL" start storage-viz-proxy.service || true
  "$CURL" -fsS -H 'Host: 127.0.0.1:505' http://127.0.0.1:505/api/servers >/dev/null || { echo "rollback previous inventory health failed" >&2; return 1; }
}

bootstrap_cutover() {
  validate_candidate_args
  if [[ "$DRY_RUN" != 1 && "${STORAGE_VIZ_INSTALL_TEST_ENABLE_CUTOVER:-0}" != 1 && "${EUID:-$(id -u)}" -ne 0 ]]; then
    echo "bootstrap cutover requires root" >&2
    exit 1
  fi
  start_candidate_topology
  probe_candidate
  local proxy_pid dash_pid
  proxy_pid="$(listener_owner_pid 505)"
  dash_pid="$(listener_owner_pid 8088)"
  run_cmd "$KILL" -TERM "$proxy_pid"
  run_cmd "$SYSTEMCTL" stop storage-viz-dashboard.service
  run_cmd "$KILL" -TERM "$dash_pid"
  set +e
  activate_candidate_release
  local activate_rc=$?
  if [[ "$activate_rc" -eq 0 ]]; then
    "$SYSTEMCTL" start storage-viz-dashboard.service storage-viz-proxy.service
    start_rc=$?
  else
    start_rc=$activate_rc
  fi
  if [[ "$start_rc" -eq 0 ]]; then
    "$PYTHON" /usr/local/libexec/storage-dashboard-health-check.py
    health_rc=$?
  else
    health_rc=$start_rc
  fi
  set -e
  if [[ "$health_rc" -ne 0 ]]; then
    rollback_after_post_stop_failure || true
    echo "bootstrap cutover failed after live stop; rollback attempted" >&2
    exit "$health_rc"
  fi
  "$SYSTEMCTL" enable --now storage-monitor-release-puller.timer
  printf 'bootstrap cutover complete; puller timer enabled after approved production health\n'
}

if [[ "$BOOTSTRAP_CUTOVER" == 1 ]]; then
  bootstrap_cutover
  exit 0
fi
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

# Candidate preflight/cutover executable path is implemented by --bootstrap-cutover above.
# It preserves legacy dashboard 127.0.0.1:8088 and public proxy :505 while using candidate dashboard 127.0.0.1:18088, candidate proxy 127.0.0.1:1505, public :505 Host/Origin,
# exact PID/port owner validation, first_cutover_order stop_exact_current_505_owner -> stop_legacy_8088_dashboard -> activate_release -> start_managed_8088_505,
# rollback to protected legacy backup with previous dashboard/inventory GET health; do not recreate tmux.
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
systemd action: do not enable/start storage-viz-proxy.service until approved cutover
systemd action: enable --now storage-monitor-release-puller.timer only after approved production health
systemd action: do not start storage-monitor-release-puller.service from installer
ACTIONS

if [[ "$DRY_RUN" == 1 || -n "$PREFIX" ]]; then
  exit 0
fi

"$SYSTEMCTL" daemon-reload
"$SYSTEMCTL" enable --now storage-viz-proxy.service
printf 'dashboard deployer installed; release puller timer intentionally not enabled until an approved release passes health\n'
