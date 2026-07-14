#!/usr/bin/env bash
set -euo pipefail

# Isolated development stack: its source tree, SQLite DB, tmux sessions, and ports
# differ from the live monitoring_v2 stack. It never launches the Slack bridge.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_SESSION="monitoring_v2_dev_backend"
FRONTEND_SESSION="monitoring_v2_dev_frontend"
BACKEND_PORT="8101"
FRONTEND_PORT="5175"
BACKEND_CMD="cd \"$ROOT_DIR\" && ./.venv/bin/uvicorn backend.main:app --host 127.0.0.1 --port $BACKEND_PORT"
FRONTEND_CMD="cd \"$ROOT_DIR/frontend\" && MONITORING_API_TARGET=http://127.0.0.1:$BACKEND_PORT npm run dev -- --host 0.0.0.0 --port $FRONTEND_PORT --strictPort"

session_exists() { tmux has-session -t "$1" 2>/dev/null; }
start_session() {
  if session_exists "$1"; then echo "[skip] $1 already running"; else tmux new-session -d -s "$1" "$2" && echo "[ok] started $1"; fi
}
stop_session() {
  if session_exists "$1"; then tmux kill-session -t "$1" && echo "[ok] stopped $1"; else echo "[skip] $1 not running"; fi
}
require_dependencies() {
  [[ -x "$ROOT_DIR/.venv/bin/uvicorn" ]] || { echo "Missing $ROOT_DIR/.venv. Run: python3 -m venv .venv && ./.venv/bin/pip install -r backend/requirements.txt" >&2; exit 1; }
  [[ -d "$ROOT_DIR/frontend/node_modules" ]] || { echo "Missing frontend/node_modules. Run: (cd frontend && npm ci)" >&2; exit 1; }
}
case "${1:-}" in
  start)
    require_dependencies
    start_session "$BACKEND_SESSION" "$BACKEND_CMD"
    start_session "$FRONTEND_SESSION" "$FRONTEND_CMD"
    printf '\nDevelopment monitor: http://127.0.0.1:%s/debug\nAPI: http://127.0.0.1:%s/health\nSafety: collectors and Slack Socket Mode are disabled; no Slack bridge is started.\n' "$FRONTEND_PORT" "$BACKEND_PORT"
    ;;
  stop) stop_session "$FRONTEND_SESSION"; stop_session "$BACKEND_SESSION" ;;
  restart) "$0" stop; "$0" start ;;
  status)
    for s in "$BACKEND_SESSION" "$FRONTEND_SESSION"; do if session_exists "$s"; then echo "[up]   $s"; else echo "[down] $s"; fi; done
    ;;
  logs)
    for s in "$BACKEND_SESSION" "$FRONTEND_SESSION"; do echo "===== $s ====="; session_exists "$s" && tmux capture-pane -pt "$s:0.0" -S -60 || true; done
    ;;
  *) echo "Usage: $0 {start|stop|restart|status|logs}" >&2; exit 1 ;;
esac
