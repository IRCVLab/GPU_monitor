#!/usr/bin/env bash
# One-time bootstrap of a storage host's agent update channel (target-design §4.5).
#
#   bootstrap-agent.sh --central-speaks-gate [options] HOST   install the bootstrap tier
#   bootstrap-agent.sh --revert [options] HOST                restore what the bootstrap replaced
#
# After the bootstrap, the monitoring key runs only the gate, and today's central
# (SFTP and a Python probe, until M3b) can no longer read the host. Install only
# with the release whose central speaks the gate transport, and say so with
# --central-speaks-gate; without it the host refuses. Everything that can fail
# (bundle signature, self-test, central key, server id, a running scan) is
# checked before anything changes, and a failure after that reverts at once.
#
# Run it from a machine that can reach HOST as an admin account with sudo. It
# copies a payload (the bootstrap tier, deploy/bootstrap_host.py and the signed
# agent bundle) to a private temp directory on HOST and runs
# `sudo python3 bootstrap_host.py install|revert` there. sudo may ask for the
# admin password. Rerunning converges; the first run's backup is kept for --revert.
#
# Options:
#   --central-speaks-gate central runs the gate transport (M3b); required to install
#   --admin USER          admin account on HOST (default: $USER)
#   --bundle-dir DIR      signed bundle: storage-viz-agent.tar.gz{,.sig} and .json
#                         (default: /opt/storage-viz-dashboard/agent, the bundle
#                         of the active dashboard release on central)
#   --monitoring-key FILE central's public key for HOST; by default the one
#                         active key in monitoring's authorized_keys. Only that
#                         key gets the forced command; other lines stay as they are
#   --server-id ID        must match scanner.yaml when it exists
#   --ssh-opt OPT         extra ssh/scp option, repeatable (for example -p 2222)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ACTION=install
ADMIN="${USER:-}"
BUNDLE_DIR=/opt/storage-viz-dashboard/agent
MONITORING_KEY=""
SERVER_ID=""
SSH_OPTS=()
CENTRAL_SPEAKS_GATE=0

usage() { sed -n '2,32s/^# \{0,1\}//p' "$0"; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --revert) ACTION=revert ;;
    --central-speaks-gate) CENTRAL_SPEAKS_GATE=1 ;;
    --admin) ADMIN="${2:?--admin needs a user}"; shift ;;
    --bundle-dir) BUNDLE_DIR="${2:?--bundle-dir needs a directory}"; shift ;;
    --monitoring-key) MONITORING_KEY="${2:?--monitoring-key needs a file}"; shift ;;
    --server-id) SERVER_ID="${2:?--server-id needs a value}"; shift ;;
    --ssh-opt) SSH_OPTS+=("${2:?--ssh-opt needs a value}"); shift ;;
    -h|--help) usage; exit 0 ;;
    -*) echo "ERROR: unknown option: $1" >&2; usage >&2; exit 2 ;;
    *) break ;;
  esac
  shift
done
[[ $# -eq 1 ]] || { usage >&2; exit 2; }
HOST="$1"
[[ "$HOST" =~ ^[A-Za-z0-9._-]+$ ]] || { echo "ERROR: unsafe host name: $HOST" >&2; exit 2; }
[[ "$ADMIN" =~ ^[a-z_][a-z0-9_-]*$ ]] || { echo "ERROR: unsafe admin user: $ADMIN" >&2; exit 2; }
[[ -z "$SERVER_ID" || "$SERVER_ID" =~ ^[A-Za-z0-9_.-]+$ ]] || { echo "ERROR: unsafe server id" >&2; exit 2; }

payload="$(mktemp -d)"
trap 'rm -rf "$payload"' EXIT
mkdir -p "$payload/bootstrap/systemd" "$payload/bootstrap/sudoers" "$payload/agent"
cp "$SCRIPT_DIR/bootstrap_host.py" "$payload/"
cp "$APP_ROOT/agent/bootstrap/gate.py" "$APP_ROOT/agent/bootstrap/update.py" "$APP_ROOT/agent/bootstrap/allowed_signers" "$payload/bootstrap/"
cp "$APP_ROOT"/agent/bootstrap/systemd/storage-viz-* "$payload/bootstrap/systemd/"
cp "$APP_ROOT/agent/bootstrap/sudoers/storage-viz" "$payload/bootstrap/sudoers/"
remote_args=("$ACTION")
if [[ "$ACTION" == install ]]; then
  for name in storage-viz-agent.tar.gz storage-viz-agent.tar.gz.sig storage-viz-agent.json; do
    [[ -f "$BUNDLE_DIR/$name" ]] || { echo "ERROR: missing $BUNDLE_DIR/$name" >&2; exit 2; }
    cp "$BUNDLE_DIR/$name" "$payload/agent/"
  done
  if [[ -n "$MONITORING_KEY" ]]; then
    cp "$MONITORING_KEY" "$payload/monitoring.pub"
    remote_args+=(--monitoring-key monitoring.pub)
  fi
  [[ -z "$SERVER_ID" ]] || remote_args+=(--server-id "$SERVER_ID")
  if [[ "$CENTRAL_SPEAKS_GATE" != 1 ]]; then
    echo "ERROR: install needs --central-speaks-gate: until central runs the gate transport (M3b) it cannot read a bootstrapped host" >&2
    exit 2
  fi
  remote_args+=(--central-speaks-gate)
fi

target="$ADMIN@$HOST"
remote_dir="$(ssh "${SSH_OPTS[@]}" "$target" 'umask 077; mktemp -d /tmp/storage-viz-bootstrap.XXXXXX')"
[[ "$remote_dir" =~ ^/tmp/storage-viz-bootstrap\.[A-Za-z0-9]+$ ]] || { echo "ERROR: unexpected remote temp dir: $remote_dir" >&2; exit 1; }
tar -C "$payload" -cf - . | ssh "${SSH_OPTS[@]}" "$target" "tar -C '$remote_dir' -xf -"
set +e
ssh -t "${SSH_OPTS[@]}" "$target" "cd '$remote_dir' && sudo python3 bootstrap_host.py ${remote_args[*]} --payload '$remote_dir'"
rc=$?
set -e
ssh "${SSH_OPTS[@]}" "$target" "rm -rf '$remote_dir'" || true
exit "$rc"
