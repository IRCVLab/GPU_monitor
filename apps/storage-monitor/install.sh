#!/usr/bin/env bash
# storage-viz installer — build the scanner and set up persistent serving + nightly scan via systemd.
# Run as root:  sudo ./install.sh
# Portable: paths/user/port are auto-derived; override with env vars.
set -euo pipefail

PROJ="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT="${PORT:-8088}"
BIND="${BIND:-0.0.0.0}"
SERVE_USER="${SERVE_USER:-$(stat -c %U "$PROJ")}"          # run the web server as the project owner (non-root)
SCAN_TARGETS="${SCAN_TARGETS:-/ /data /data1 /data3}"       # mounts to scan (root sees everything)
SCAN_TIME="${SCAN_TIME:-02:00}"                            # nightly scan time (HH:MM)
PYTHON="$(command -v python3)"

if [[ $EUID -ne 0 ]]; then
  echo "ERROR: run as root:  sudo ./install.sh" >&2; exit 1
fi

echo "[*] project   : $PROJ"
echo "[*] serve user: $SERVE_USER   port: $PORT   bind: $BIND"
echo "[*] scan       : $SCAN_TARGETS   nightly @ $SCAN_TIME"

echo "[*] building scanner..."
make -C "$PROJ/scanner"

echo "[*] writing systemd units..."

cat > /etc/systemd/system/storage-viz-http.service <<EOF
[Unit]
Description=storage-viz dashboard (offline static HTTP)
After=network.target

[Service]
Type=simple
User=$SERVE_USER
ExecStart=$PYTHON -m http.server $PORT --directory $PROJ/viewer --bind $BIND
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/storage-viz-scan.service <<EOF
[Unit]
Description=storage-viz disk usage scan
After=local-fs.target

[Service]
Type=oneshot
ExecStart=$PROJ/scanner/hstscan --out $PROJ/data/%H.json $SCAN_TARGETS
Nice=10
IOSchedulingClass=idle
EOF

cat > /etc/systemd/system/storage-viz-scan.timer <<EOF
[Unit]
Description=Run storage-viz scan nightly

[Timer]
OnCalendar=*-*-* $SCAN_TIME:00
Persistent=true

[Install]
WantedBy=timers.target
EOF

echo "[*] enabling services..."
systemctl daemon-reload
systemctl enable --now storage-viz-http.service
systemctl enable --now storage-viz-scan.timer

echo "[*] running initial scan now (may take a few minutes)..."
systemctl start storage-viz-scan.service

IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
echo
echo "[✓] installed."
echo "    dashboard : http://${IP:-<host>}:$PORT/"
echo "    next scan  : $(systemctl show -p NextElapseUSecRealtime --value storage-viz-scan.timer 2>/dev/null || echo "nightly @ $SCAN_TIME")"
echo "    rescan now : sudo systemctl start storage-viz-scan.service"
echo "    logs       : journalctl -u storage-viz-scan.service -u storage-viz-http.service"
