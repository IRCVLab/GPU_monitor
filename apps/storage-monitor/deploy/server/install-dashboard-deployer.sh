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
PYTHON="${PYTHON:-/usr/bin/python3.12}"
ACTIVATOR="${ACTIVATOR:-/usr/local/libexec/storage-dashboard-activate.py}"
KILL="${KILL:-kill}"
PROC_ROOT="${PROC_ROOT:-/proc}"
HEALTH_CHECKER="${HEALTH_CHECKER:-/usr/local/libexec/storage-dashboard-health-check.py}"
DASHBOARD_ENV="${DASHBOARD_ENV:-/etc/storage-viz/dashboard.env}"
PROXY_ENV="${PROXY_ENV:-/etc/storage-viz/proxy.env}"

LIBEXEC_DIR="/usr/local/libexec"
RELEASE_ROOT="${RELEASE_ROOT:-/srv/storage-viz-dashboard/releases}"
STATE_ROOT="${STATE_ROOT:-/var/lib/storage-viz-dashboard}"
# Storage-only state directories: /var/lib/storage-viz-dashboard/puller /var/lib/storage-viz-dashboard/builder /var/lib/storage-viz-dashboard/data /var/lib/storage-viz-dashboard/state
CONFIG_DIR="/etc/storage-viz"
APP_PATH="${APP_PATH:-/opt/storage-viz-dashboard}"
LEGACY_PROXY_PATH="${LEGACY_PROXY_PATH:-$APP_PATH/deploy/direct_proxy.py}"
LEGACY_DASHBOARD_PATH="${LEGACY_DASHBOARD_PATH:-$APP_PATH/viewer/serve.py}"
LEGACY_PYTHON_EXES="${LEGACY_PYTHON_EXES:-/usr/bin/python3.12:/usr/bin/python3}"
LEGACY_TMUX_EXE="${LEGACY_TMUX_EXE:-/usr/bin/tmux}"
RUNTIME_USER="storage-viz"
RUNTIME_GROUP="storage-viz"
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

env_file_value() {
  local path="$1" key="$2"
  awk -F= -v wanted="$key" '
    $1 == wanted {
      if (found) exit 3
      found = 1
      print substr($0, length($1) + 2)
    }
    END { if (!found) exit 2 }
  ' "$path"
}

parse_status_path() {
  local payload="$1" mode="$2"
  [[ "${#payload}" -le 8192 ]] || { echo "$mode status exceeds JSON bound" >&2; return 1; }
  printf '%s' "$payload" | "$PYTHON" -c '
import json, pathlib, sys
mode, sha, digest, release_root = sys.argv[1:]
try:
    payload = json.load(sys.stdin)
except Exception as exc:
    raise SystemExit(f"invalid {mode} JSON: {exc}")
if not isinstance(payload, dict) or payload.get("status") != mode:
    raise SystemExit(f"activator did not report {mode}")
if payload.get("source_sha") != sha or payload.get("archive_digest") != digest:
    raise SystemExit(f"activator {mode} identity mismatch")
key = "candidate_release" if mode == "prepared" else "release"
raw = payload.get(key)
if not isinstance(raw, str):
    raise SystemExit(f"activator {mode} path missing")
path = pathlib.Path(raw).resolve()
expected = pathlib.Path(release_root).resolve() / sha / "storage-monitor"
if path != expected.resolve() or not path.is_dir():
    raise SystemExit(f"activator {mode} path mismatch")
print(raw)
' "$mode" "$CANDIDATE_SHA" "$EXPECTED_DIGEST" "$RELEASE_ROOT"
}

prepare_candidate_release() {
  local status
  if [[ "$DRY_RUN" == 1 ]]; then
    CANDIDATE_RELEASE="$RELEASE_ROOT/$CANDIDATE_SHA/storage-monitor"
    run_cmd "$ACTIVATOR" --prepare-only --sha "$CANDIDATE_SHA" --expected-digest "$EXPECTED_DIGEST" --artifact-stdin --metadata "$METADATA"
    return
  fi
  status="$("$ACTIVATOR" --prepare-only --sha "$CANDIDATE_SHA" --expected-digest "$EXPECTED_DIGEST" --artifact-stdin --metadata "$METADATA" <"$ARTIFACT")"
  CANDIDATE_RELEASE="$(parse_status_path "$status" prepared)"
}

cleanup_candidate_topology() {
  [[ -z "${CANDIDATE_PROXY_PID:-}" ]] || "$KILL" -TERM "$CANDIDATE_PROXY_PID" 2>/dev/null || true
  [[ -z "${CANDIDATE_DASH_PID:-}" ]] || "$KILL" -TERM "$CANDIDATE_DASH_PID" 2>/dev/null || true
  [[ -z "${CANDIDATE_TMP:-}" ]] || rm -rf -- "$CANDIDATE_TMP"
}

start_candidate_topology() {
  local inventory public_origin operator cookie_secure
  inventory="$(env_file_value "$DASHBOARD_ENV" STORAGE_VIZ_INVENTORY)" || { echo "candidate inventory config is unavailable" >&2; return 1; }
  public_origin="$(env_file_value "$PROXY_ENV" STORAGE_VIZ_PROXY_PUBLIC_ORIGIN)" || { echo "candidate public origin config is unavailable" >&2; return 1; }
  operator="$(env_file_value "$PROXY_ENV" STORAGE_VIZ_PROXY_OPERATOR)" || { echo "candidate proxy operator config is unavailable" >&2; return 1; }
  cookie_secure="$(env_file_value "$DASHBOARD_ENV" STORAGE_VIZ_SESSION_COOKIE_SECURE)" || { echo "candidate cookie config is unavailable" >&2; return 1; }
  [[ "$operator" == "fixed-proxy-operator" ]] || { echo "candidate proxy operator must be fixed-proxy-operator" >&2; return 1; }
  CANDIDATE_TMP="$(mktemp -d "${TMPDIR:-/tmp}/storage-viz-candidate.XXXXXX")"
  trap cleanup_candidate_topology EXIT
  mkdir -p "$CANDIDATE_TMP/data" "$CANDIDATE_TMP/state"
  if [[ "$DRY_RUN" == 1 ]]; then
    run_cmd env STORAGE_VIZ_ROOT="$CANDIDATE_RELEASE" STORAGE_VIZ_BIND=127.0.0.1 STORAGE_VIZ_PORT=18088 STORAGE_VIZ_INVENTORY="$inventory" STORAGE_VIZ_DATA_DIR="$CANDIDATE_TMP/data" STORAGE_VIZ_STATE_DIR="$CANDIDATE_TMP/state" STORAGE_VIZ_PREFLIGHT_BACKEND=1 STORAGE_VIZ_TRUSTED_PROXY=1 STORAGE_VIZ_ALLOWED_ORIGINS="$public_origin" STORAGE_VIZ_OPERATOR_ALLOWLIST="$operator" STORAGE_VIZ_SESSION_COOKIE_SECURE="$cookie_secure" "$PYTHON" "$CANDIDATE_RELEASE/viewer/serve.py"
    run_cmd env STORAGE_VIZ_PROXY_BIND=127.0.0.1 STORAGE_VIZ_PROXY_PORT=1505 STORAGE_VIZ_PROXY_UPSTREAM_HOST=127.0.0.1 STORAGE_VIZ_PROXY_UPSTREAM_PORT=18088 STORAGE_VIZ_PROXY_OPERATOR="$operator" STORAGE_VIZ_PROXY_PUBLIC_ORIGIN="$public_origin" "$PYTHON" "$CANDIDATE_RELEASE/deploy/direct_proxy.py"
    return
  fi
  STORAGE_VIZ_ROOT="$CANDIDATE_RELEASE" STORAGE_VIZ_BIND=127.0.0.1 STORAGE_VIZ_PORT=18088 STORAGE_VIZ_INVENTORY="$inventory" STORAGE_VIZ_DATA_DIR="$CANDIDATE_TMP/data" STORAGE_VIZ_STATE_DIR="$CANDIDATE_TMP/state" STORAGE_VIZ_PREFLIGHT_BACKEND=1 STORAGE_VIZ_TRUSTED_PROXY=1 STORAGE_VIZ_ALLOWED_ORIGINS="$public_origin" STORAGE_VIZ_OPERATOR_ALLOWLIST="$operator" STORAGE_VIZ_SESSION_COOKIE_SECURE="$cookie_secure" \
    "$PYTHON" "$CANDIDATE_RELEASE/viewer/serve.py" &
  CANDIDATE_DASH_PID="$!"
  STORAGE_VIZ_PROXY_BIND=127.0.0.1 STORAGE_VIZ_PROXY_PORT=1505 STORAGE_VIZ_PROXY_UPSTREAM_HOST=127.0.0.1 STORAGE_VIZ_PROXY_UPSTREAM_PORT=18088 STORAGE_VIZ_PROXY_OPERATOR="$operator" STORAGE_VIZ_PROXY_PUBLIC_ORIGIN="$public_origin" \
    "$PYTHON" "$CANDIDATE_RELEASE/deploy/direct_proxy.py" &
  CANDIDATE_PROXY_PID="$!"
  sleep 0.1
}

probe_candidate() {
  run_cmd "$PYTHON" "$HEALTH_CHECKER" --dashboard-env "$DASHBOARD_ENV" --proxy-env "$PROXY_ENV" --connect-host 127.0.0.1 --connect-port 1505 --skip-service-check
}

allowed_python_executable() {
  local exe="$1" candidate
  IFS=: read -r -a candidates <<<"$LEGACY_PYTHON_EXES"
  for candidate in "${candidates[@]}"; do
    [[ "$exe" == "$candidate" ]] && return 0
  done
  return 1
}

cmdline_has_exact_path() {
  local cmdline="$1" expected="$2" token
  while IFS= read -r token; do
    [[ "$token" == "$expected" ]] && return 0
  done < <(tr '\0' '\n' <"$cmdline")
  return 1
}

validate_listener_process() {
  local port="$1" pid="$2" proc="$PROC_ROOT/$pid" exe cmdline cgroup main_pid
  [[ -L "$proc/exe" && -f "$proc/cmdline" && -f "$proc/cgroup" ]] || { echo "listener process metadata unavailable for :$port pid=$pid" >&2; return 1; }
  exe="$(readlink "$proc/exe")"
  cmdline="$proc/cmdline"
  cgroup="$(cat "$proc/cgroup")"
  if [[ "$port" == 505 ]] && allowed_python_executable "$exe" && cmdline_has_exact_path "$cmdline" "$LEGACY_PROXY_PATH"; then
    return 0
  fi
  if [[ "$port" == 8088 ]] && allowed_python_executable "$exe" && cmdline_has_exact_path "$cmdline" "$LEGACY_DASHBOARD_PATH"; then
    main_pid="$("$SYSTEMCTL" show -p MainPID --value storage-viz-dashboard.service)"
    if [[ "$main_pid" == "$pid" && "$cgroup" == *"/storage-viz-dashboard.service"* ]]; then
      return 0
    fi
  fi
  if [[ "$exe" == "$LEGACY_TMUX_EXE" ]]; then
    if [[ "$port" == 505 ]] && cmdline_has_exact_path "$cmdline" "$LEGACY_PROXY_PATH"; then return 0; fi
    if [[ "$port" == 8088 ]] && cmdline_has_exact_path "$cmdline" "$LEGACY_DASHBOARD_PATH"; then return 0; fi
  fi
  echo "listener owner for :$port pid=$pid does not match an approved legacy target" >&2
  return 1
}

listener_owner_pid() {
  local port="$1" line pid out pid_tokens
  out="$($SS -H -ltnp "sport = :$port")"
  [[ -n "$out" ]] || { echo "missing listener for :$port" >&2; return 1; }
  [[ "$(printf '%s\n' "$out" | wc -l | awk '{print $1}')" == 1 ]] || { echo "ambiguous listener for :$port" >&2; return 1; }
  line="$out"
  pid_tokens="$(printf '%s' "$line" | awk '
    {
      rest = $0
      while (match(rest, /pid=[0-9]+/)) {
        print substr(rest, RSTART + 4, RLENGTH - 4)
        rest = substr(rest, RSTART + RLENGTH)
      }
    }
  ')"
  [[ "$(printf '%s\n' "$pid_tokens" | awk 'NF { count++ } END { print count + 0 }')" == 1 ]] || { echo "listener for :$port must expose exactly one pid token" >&2; return 1; }
  pid="$pid_tokens"
  [[ "$pid" =~ ^[0-9]+$ ]] || { echo "cannot parse listener PID for :$port" >&2; return 1; }
  validate_listener_process "$port" "$pid" || return 1
  printf '%s\n' "$pid"
}

activate_candidate_release() {
  local status activated
  status="$("$ACTIVATOR" --sha "$CANDIDATE_SHA" --expected-digest "$EXPECTED_DIGEST" --artifact-stdin --metadata "$METADATA" <"$ARTIFACT")" || return
  activated="$(parse_status_path "$status" active)" || return
  [[ "$activated" == "$CANDIDATE_RELEASE" ]] || { echo "activated release differs from prepared candidate" >&2; return 1; }
}

is_no_state_rollback_error() {
  local payload="$1"
  [[ "${#payload}" -le 8192 ]] || return 1
  printf '%s' "$payload" | "$PYTHON" -c '
import json, sys
try:
    payload = json.load(sys.stdin)
except Exception:
    raise SystemExit(1)
if payload != {"status": "error", "error": "activation state is unavailable for rollback"}:
    raise SystemExit(1)
'
}

validate_recovery_status() {
  local payload="$1"
  [[ "${#payload}" -le 8192 ]] || { echo "restored legacy status exceeds JSON bound" >&2; return 1; }
  printf '%s' "$payload" | "$PYTHON" -c '
import json, pathlib, sys
try:
    payload = json.load(sys.stdin)
except Exception as exc:
    raise SystemExit(f"invalid restored legacy JSON: {exc}")
app = pathlib.Path(sys.argv[1]).resolve(strict=True)
expected_proxy = app / "deploy/direct_proxy.py"
if payload.get("status") != "rolled_back":
    raise SystemExit("activator did not report restored legacy rollback")
if payload.get("restored_legacy_target") != str(app) or payload.get("managed_legacy_proxy_target") != str(expected_proxy):
    raise SystemExit("activator restored legacy path mismatch")
if any(key in payload for key in ("release", "current", "source_sha", "failed_release")):
    raise SystemExit("activator restored legacy state retained candidate identity")
' "$APP_PATH"
}

rollback_after_post_stop_failure() {
  local rollback_status recovery_status
  if ! rollback_status="$("$ACTIVATOR" --rollback-state --state-path "$STATE_ROOT/activation-state.json" --app-path "$APP_PATH" --release-root "$RELEASE_ROOT" --lock-path "$STATE_ROOT/activation.lock" --incoming-dir "$STATE_ROOT/incoming" 2>&1)"; then
    is_no_state_rollback_error "$rollback_status" || { printf '%s\n' "$rollback_status" >&2; return 1; }
    [[ -d "$APP_PATH" && ! -L "$APP_PATH" && -f "$APP_PATH/viewer/serve.py" && ! -L "$APP_PATH/viewer/serve.py" && -f "$APP_PATH/deploy/direct_proxy.py" && ! -L "$APP_PATH/deploy/direct_proxy.py" ]] || {
      echo "activation state is unavailable and exact restored legacy app is not present" >&2
      return 1
    }
    recovery_status="$("$ACTIVATOR" --record-restored-legacy --state-path "$STATE_ROOT/activation-state.json" --app-path "$APP_PATH" --release-root "$RELEASE_ROOT" --lock-path "$STATE_ROOT/activation.lock" --incoming-dir "$STATE_ROOT/incoming")" || return 1
    validate_recovery_status "$recovery_status" || return 1
  fi
  "$SYSTEMCTL" start storage-viz-dashboard.service storage-viz-proxy.service || return 1
  "$PYTHON" "$HEALTH_CHECKER" --dashboard-env "$DASHBOARD_ENV" --proxy-env "$PROXY_ENV" || { echo "rollback previous inventory health failed" >&2; return 1; }
}

bootstrap_cutover() {
  validate_candidate_args
  if [[ "$DRY_RUN" != 1 && "${STORAGE_VIZ_INSTALL_TEST_ENABLE_CUTOVER:-0}" != 1 && "${EUID:-$(id -u)}" -ne 0 ]]; then
    echo "bootstrap cutover requires root" >&2
    exit 1
  fi
  prepare_candidate_release
  start_candidate_topology
  probe_candidate
  local proxy_pid dash_pid
  proxy_pid="$(listener_owner_pid 505)"
  dash_pid="$(listener_owner_pid 8088)"
  run_cmd "$KILL" -TERM "$proxy_pid"
  run_cmd "$KILL" -TERM "$dash_pid"
  run_cmd "$SYSTEMCTL" stop storage-viz-dashboard.service
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
    "$PYTHON" "$HEALTH_CHECKER" --dashboard-env "$DASHBOARD_ENV" --proxy-env "$PROXY_ENV"
    health_rc=$?
  else
    health_rc=$start_rc
  fi
  set -e
  if [[ "$health_rc" -ne 0 ]]; then
    if ! rollback_after_post_stop_failure; then
      echo "bootstrap cutover failed after live stop and rollback health did not recover" >&2
      exit 70
    fi
    echo "bootstrap cutover failed after live stop; rollback completed" >&2
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

cat <<'CUTOVER_PLAN'
cutover plan 1: prepare supplied artifact as an immutable candidate release
cutover plan 2: start candidate dashboard 127.0.0.1:18088 and direct proxy 127.0.0.1:1505
cutover plan 3: run full candidate health/session/inventory/UNKNOWN_SERVER readiness
cutover plan 4: validate exact listener owners for live 505 and 8088
cutover plan 5: stop only validated live owners and legacy dashboard service
cutover plan 6: activate the exact prepared release
cutover plan 7: start managed dashboard and proxy services
cutover plan 8: run production health through public 505
cutover plan 9: enable --now storage-monitor-release-puller.timer
CUTOVER_PLAN

if [[ "$DRY_RUN" == 1 || ( -n "$PREFIX" && "${STORAGE_VIZ_INSTALL_TEST_REAL_WITH_PREFIX:-0}" != 1 ) ]]; then
  exit 0
fi

"$SYSTEMCTL" daemon-reload
printf 'dashboard deployer installed; release puller timer intentionally not enabled until an approved release passes health\n'
