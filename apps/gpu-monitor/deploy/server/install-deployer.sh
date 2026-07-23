#!/usr/bin/env bash
set -euo pipefail
usage() { cat >&2 <<'USAGE'
Usage: install-deployer.sh [--dry-run] [--prefix <dir>] --dev-public-key <key> [--live-public-key <key>]
USAGE
}
fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
dry_run=false
prefix=""
dev_key=""
live_key=""
while (($#)); do
  case "$1" in
    --dry-run) dry_run=true; shift ;;
    --prefix) [[ $# -ge 2 ]] || fail "--prefix requires value"; prefix=$2; shift 2 ;;
    --dev-public-key) [[ $# -ge 2 ]] || fail "--dev-public-key requires value"; dev_key=$2; shift 2 ;;
    --live-public-key) [[ $# -ge 2 ]] || fail "--live-public-key requires value"; live_key=$2; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) usage; fail "unknown argument: $1" ;;
  esac
done
[[ -n "$dev_key" ]] || fail "--dev-public-key is required"
if [[ -z "$prefix" && $(id -u) -ne 0 ]]; then
  fail "real installation requires root; use --dry-run --prefix for tests"
fi
root="$prefix"
libexec="$root/usr/local/libexec"
systemd="$root/etc/systemd/system"
etc="$root/etc/gpu-monitor"
home="$root/home/gpu-deploy"
srv="$root/srv/gpu-monitor"
mkdir -p "$libexec" "$systemd" "$etc" "$home/.ssh" "$srv/dev/releases" "$srv/dev/incoming" "$srv/live/releases" "$srv/live/incoming"
: > "$etc/dev.env"
: > "$etc/live.env"
install -m 0755 "$(dirname "${BASH_SOURCE[0]}")/gpu-monitor-deploy-command" "$libexec/gpu-monitor-deploy-command"
install -m 0755 "$(dirname "${BASH_SOURCE[0]}")/activate-release.sh" "$libexec/activate-release.sh"
install -m 0755 "$(dirname "${BASH_SOURCE[0]}")/health-check.sh" "$libexec/health-check.sh"
for unit in "$(dirname "${BASH_SOURCE[0]}")"/systemd/*.service; do
  install -m 0644 "$unit" "$systemd/"
done
{
  printf 'restrict,command="/usr/local/libexec/gpu-monitor-deploy-command dev" %s\n' "$dev_key"
  if [[ -n "$live_key" ]]; then
    printf 'restrict,command="/usr/local/libexec/gpu-monitor-deploy-command live" %s\n' "$live_key"
  fi
} > "$home/.ssh/authorized_keys"
chmod 700 "$home/.ssh"
chmod 600 "$home/.ssh/authorized_keys"
if [[ "$dry_run" == false ]]; then
  if ! id gpu-deploy >/dev/null 2>&1; then
    useradd --system --home-dir /home/gpu-deploy --shell /usr/sbin/nologin gpu-deploy
  fi
  chown -R gpu-deploy:gpu-deploy "$root/home/gpu-deploy" "$root/srv/gpu-monitor"
fi
printf 'installed gpu-monitor deployer files under %s\n' "${root:-/}"
