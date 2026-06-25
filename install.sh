#!/usr/bin/env bash
# storage-viz installer — build the scanner and set up persistent serving + nightly scan via systemd.
# Run as root for real installs: sudo ./install.sh
# Dry-run/syntax-check without privileged writes: ./install.sh --dry-run
# Portable: clone path, data dir, targets, user, bind address, and port are configurable via env vars.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJ="${STORAGE_VIZ_ROOT:-${PROJ:-$SCRIPT_DIR}}"
PROJ="$(cd "$PROJ" && pwd)"

PORT="${STORAGE_VIZ_PORT:-${PORT:-8088}}"
BIND="${STORAGE_VIZ_BIND:-${BIND:-0.0.0.0}}"
# serve.py is manual-only by default; scheduled/manual scans stay in the root scan service.
SERVE_USER="${STORAGE_VIZ_SERVE_USER:-${SERVE_USER:-${SUDO_USER:-root}}}"
DATA_DIR="${STORAGE_VIZ_DATA_DIR:-${DATA_DIR:-$PROJ/data}}"
SCAN_TARGETS="${STORAGE_VIZ_SCAN_TARGETS:-${SCAN_TARGETS:-/ /data /data1 /data3}}"
SCAN_TIME="${STORAGE_VIZ_SCAN_TIME:-${SCAN_TIME:-02:00}}"
PYTHON="${PYTHON:-$(command -v python3)}"
DRY_RUN="${DRY_RUN:-0}"
ENABLE_SERVICES="${ENABLE_SERVICES:-1}"
RUN_INITIAL_SCAN="${RUN_INITIAL_SCAN:-1}"
UNIT_DIR="${UNIT_DIR:-/etc/systemd/system}"

usage() {
  cat <<USAGE
Usage: $0 [--dry-run] [--no-enable] [--no-initial-scan]

Environment overrides:
  STORAGE_VIZ_ROOT=/opt/storage-viz        clone/project root (default: this checkout)
  STORAGE_VIZ_DATA_DIR=/var/lib/storage-viz data JSON directory (default: \$ROOT/data)
  STORAGE_VIZ_SCAN_TARGETS='/ /data'        scanner targets (default: / /data /data1 /data3)
  STORAGE_VIZ_PORT=8088                    dashboard port
  STORAGE_VIZ_BIND=0.0.0.0                 dashboard bind address
  STORAGE_VIZ_SERVE_USER=$SERVE_USER              systemd HTTP service user; rescan stays manual-only by default
  STORAGE_VIZ_SCAN_TIME=02:00              nightly timer time (HH:MM)
  UNIT_DIR=/etc/systemd/system             systemd unit output directory

Dry-run writes units to a temporary UNIT_DIR (unless UNIT_DIR is set), runs syntax checks,
and does not call systemctl or start scans.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1 ;;
    --no-enable) ENABLE_SERVICES=0 ;;
    --no-initial-scan) RUN_INITIAL_SCAN=0 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "ERROR: unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

if [[ "$DRY_RUN" == "1" ]]; then
  ENABLE_SERVICES=0
  RUN_INITIAL_SCAN=0
  if [[ "${UNIT_DIR}" == "/etc/systemd/system" ]]; then
    UNIT_DIR="$(mktemp -d "${TMPDIR:-/tmp}/storage-viz-systemd.XXXXXX")"
  fi
elif [[ $EUID -ne 0 ]]; then
  echo "ERROR: run as root for a real install, or use --dry-run for local verification" >&2
  exit 1
fi

mkdir -p "$DATA_DIR"

echo "[*] project   : $PROJ"
echo "[*] data dir  : $DATA_DIR"
echo "[*] serve user: $SERVE_USER   port: $PORT   bind: $BIND"
echo "[*] scan       : $SCAN_TARGETS   nightly @ $SCAN_TIME"
echo "[*] unit dir   : $UNIT_DIR"
if [[ "$DRY_RUN" == "1" ]]; then echo "[*] mode       : dry-run (no privileged systemctl actions)"; fi

echo "[*] building scanner..."
make -C "$PROJ/scanner"

mkdir -p "$UNIT_DIR"
echo "[*] writing systemd units..."

cat > "$UNIT_DIR/storage-viz-http.service" <<EOF_UNIT
[Unit]
Description=storage-viz dashboard (static HTTP + rescan status)
After=network.target local-fs.target

[Service]
Type=simple
User=$SERVE_USER
WorkingDirectory=$PROJ
Environment="STORAGE_VIZ_ROOT=$PROJ"
Environment="STORAGE_VIZ_DATA_DIR=$DATA_DIR"
Environment="STORAGE_VIZ_SCAN_TARGETS=$SCAN_TARGETS"
Environment="STORAGE_VIZ_PORT=$PORT"
Environment="STORAGE_VIZ_BIND=$BIND"
Environment="STORAGE_VIZ_SCANNER=$PROJ/scanner/hstscan"
ExecStart="$PYTHON" "$PROJ/viewer/serve.py"
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF_UNIT

cat > "$UNIT_DIR/storage-viz-scan.service" <<EOF_UNIT
[Unit]
Description=storage-viz disk usage scan
After=local-fs.target

[Service]
Type=oneshot
WorkingDirectory=$PROJ
ExecStart="$PROJ/scanner/hstscan" --out "$DATA_DIR/%H.json" $SCAN_TARGETS
Nice=10
IOSchedulingClass=idle
EOF_UNIT

cat > "$UNIT_DIR/storage-viz-scan.timer" <<EOF_UNIT
[Unit]
Description=Run storage-viz scan nightly

[Timer]
OnCalendar=*-*-* $SCAN_TIME:00
Persistent=true

[Install]
WantedBy=timers.target
EOF_UNIT

if command -v systemd-analyze >/dev/null 2>&1; then
  echo "[*] verifying systemd unit syntax..."
  systemd-analyze verify \
    "$UNIT_DIR/storage-viz-http.service" \
    "$UNIT_DIR/storage-viz-scan.service" \
    "$UNIT_DIR/storage-viz-scan.timer"
else
  echo "[!] systemd-analyze not found; skipped unit syntax verification"
fi

if [[ "$DRY_RUN" == "1" ]]; then
  echo
  echo "[✓] dry-run complete. Units written for inspection:"
  echo "    $UNIT_DIR/storage-viz-http.service"
  echo "    $UNIT_DIR/storage-viz-scan.service"
  echo "    $UNIT_DIR/storage-viz-scan.timer"
  exit 0
fi

if [[ "$ENABLE_SERVICES" == "1" ]]; then
  echo "[*] enabling services..."
  systemctl daemon-reload
  systemctl enable --now storage-viz-http.service
  systemctl enable --now storage-viz-scan.timer
fi

if [[ "$RUN_INITIAL_SCAN" == "1" ]]; then
  echo "[*] running initial scan now (may take a few minutes)..."
  systemctl start storage-viz-scan.service
fi

IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
echo
echo "[✓] installed."
echo "    dashboard : http://${IP:-<host>}:$PORT/"
echo "    data dir  : $DATA_DIR"
echo "    next scan  : $(systemctl show -p NextElapseUSecRealtime --value storage-viz-scan.timer 2>/dev/null || echo "nightly @ $SCAN_TIME")"
echo "    rescan now : sudo systemctl start storage-viz-scan.service"
echo "    logs       : journalctl -u storage-viz-scan.service -u storage-viz-http.service"
