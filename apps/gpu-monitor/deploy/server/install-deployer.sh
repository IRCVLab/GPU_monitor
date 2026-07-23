#!/bin/bash -p
set -euo pipefail

PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export PATH

usage() {
  cat >&2 <<'USAGE'
Usage: install-deployer.sh [--dry-run] [--prefix <dir>] --dev-public-key <key> [--live-public-key <key>]
USAGE
}

fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

dry_run=false
force_non_root=false
prefix=""
dev_key=""
live_key=""
while (($#)); do
  case "$1" in
    --dry-run)
      dry_run=true
      shift
      ;;
    --prefix)
      [[ $# -ge 2 ]] || fail "--prefix requires value"
      prefix=$2
      shift 2
      ;;
    --dev-public-key)
      [[ $# -ge 2 ]] || fail "--dev-public-key requires value"
      dev_key=$2
      shift 2
      ;;
    --live-public-key)
      [[ $# -ge 2 ]] || fail "--live-public-key requires value"
      live_key=$2
      shift 2
      ;;
    --test-mode-force-non-root)
      force_non_root=true
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      usage
      fail "unknown argument: $1"
      ;;
  esac
done

[[ -n "$dev_key" ]] || fail "--dev-public-key is required"
[[ "$prefix" != *$'\n'* ]] || fail "--prefix must not contain a newline"
if [[ -n "$prefix" ]]; then
  [[ "$prefix" == /* && "$prefix" != / ]] || fail "--prefix must be an absolute non-root path"
fi
if [[ "$dry_run" == true && -z "$prefix" ]]; then
  fail "--dry-run requires --prefix"
fi
if [[ "$dry_run" == false ]]; then
  if [[ "$force_non_root" == true ]] || [[ "$(/usr/bin/id -u)" -ne 0 ]]; then
    fail "non-dry-run installation requires root even when --prefix is used"
  fi
fi

validate_public_key() {
  local key_name=$1 key_value=$2
  [[ "$key_value" != *$'\n'* && "$key_value" != *$'\r'* ]] ||
    fail "$key_name must be one public key line"
  [[ "$key_value" =~ ^(ssh-ed25519|sk-ssh-ed25519@openssh.com|ecdsa-sha2-nistp256|sk-ecdsa-sha2-nistp256@openssh.com|ssh-rsa)[[:space:]]+[A-Za-z0-9+/]+={0,3}([[:space:]][^[:cntrl:]]*)?$ ]] ||
    fail "$key_name must start with a supported key type and cannot contain authorized_keys options"
}

validate_public_key "development public key" "$dev_key"
if [[ -n "$live_key" ]]; then
  validate_public_key "live public key" "$live_key"
fi

root=$prefix
libexec="$root/usr/local/libexec"
systemd="$root/etc/systemd/system"
etc="$root/etc/gpu-monitor"
home="$root/home/gpu-deploy"
srv="$root/srv/gpu-monitor"
script_dir=${BASH_SOURCE[0]%/*}
[[ "$script_dir" != "${BASH_SOURCE[0]}" ]] || script_dir=.
script_dir=$(cd -- "$script_dir" && /bin/pwd -P)

if [[ "$dry_run" == false ]] && ! /usr/bin/id gpu-deploy >/dev/null 2>&1; then
  /usr/sbin/useradd --system --home-dir /home/gpu-deploy --shell /usr/sbin/nologin gpu-deploy
fi

install -d -m 0755 "$libexec" "$systemd" "$etc"
install -d -m 0750 \
  "$home" "$srv/dev" "$srv/dev/releases" "$srv/dev/incoming" \
  "$srv/live" "$srv/live/releases" "$srv/live/incoming"
install -d -m 0700 "$home/.ssh"

install_env_file() {
  local path=$1 contents=$2
  if [[ ! -e "$path" ]]; then
    printf '%s' "$contents" > "$path"
    chmod 0640 "$path"
  fi
}

install_env_file "$etc/dev.env" $'GPU_MONITOR_BACKEND_PORT=8101\nPORT=5174\n'
install_env_file "$etc/live.env" $'GPU_MONITOR_BACKEND_PORT=8001\nGPU_MONITOR_BRIDGE_PORT=8000\nPORT=5173\n'

install -m 0755 "$script_dir/gpu-monitor-deploy-command" "$libexec/gpu-monitor-deploy-command"
install -m 0755 "$script_dir/activate-release.sh" "$libexec/activate-release.sh"
install -m 0755 "$script_dir/health-check.sh" "$libexec/health-check.sh"
for unit in "$script_dir"/systemd/*.service; do
  install -m 0644 "$unit" "$systemd/"
done

key_options='no-agent-forwarding,no-port-forwarding,no-pty,no-user-rc,no-X11-forwarding'
{
  printf 'restrict,command="/usr/local/libexec/gpu-monitor-deploy-command dev",%s %s\n' "$key_options" "$dev_key"
  if [[ -n "$live_key" ]]; then
    printf 'restrict,command="/usr/local/libexec/gpu-monitor-deploy-command live",%s %s\n' "$key_options" "$live_key"
  fi
} > "$home/.ssh/authorized_keys"
chmod 0600 "$home/.ssh/authorized_keys"

if [[ "$dry_run" == false ]]; then
  chown -R gpu-deploy:gpu-deploy "$home" "$srv"
  chown root:root "$etc/dev.env" "$etc/live.env"
fi

printf 'installed gpu-monitor deployer files under %s\n' "${root:-/}"
