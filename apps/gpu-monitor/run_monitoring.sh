#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_SESSION="monitoring_v2_backend"
FRONTEND_SESSION="monitoring_v2_frontend"
BRIDGE_SESSION="monitoring_v2_slack_bridge"

BACKEND_CMD="cd \"$ROOT_DIR\" && ./.venv/bin/uvicorn backend.main:app --host 127.0.0.1 --port 8001"
FRONTEND_CMD="cd \"$ROOT_DIR/frontend\" && npm run dev -- --host 0.0.0.0"
BRIDGE_CMD="cd \"$ROOT_DIR\" && ./.venv/bin/uvicorn backend.slack_bridge:app --host 0.0.0.0 --port 8000"

usage() {
  cat <<'EOF'
Usage: ./run_monitoring.sh <command>

Commands:
  start      Start backend, frontend, and slack bridge in tmux
  stop       Stop all tmux sessions for the monitoring stack
  restart    Restart the full monitoring stack
  status     Show whether each tmux session is running
  logs       Show recent logs from all three tmux sessions
EOF
}

require_tmux() {
  if ! command -v tmux >/dev/null 2>&1; then
    echo "tmux is required but not installed." >&2
    exit 1
  fi
}

require_backend_venv() {
  if [[ ! -x "$ROOT_DIR/.venv/bin/uvicorn" ]]; then
    echo "Missing backend venv at $ROOT_DIR/.venv/bin/uvicorn" >&2
    echo "Create it first, e.g.:" >&2
    echo "  cd $ROOT_DIR && python3 -m venv .venv && ./.venv/bin/pip install -r backend/requirements.txt" >&2
    exit 1
  fi
}

require_frontend_deps() {
  if [[ ! -d "$ROOT_DIR/frontend/node_modules" ]]; then
    echo "Missing frontend dependencies at $ROOT_DIR/frontend/node_modules" >&2
    echo "Install them first, e.g.:" >&2
    echo "  cd $ROOT_DIR/frontend && npm ci" >&2
    exit 1
  fi
}

session_exists() {
  local session_name="$1"
  tmux has-session -t "$session_name" 2>/dev/null
}

start_session() {
  local session_name="$1"
  local command="$2"

  if session_exists "$session_name"; then
    echo "[skip] $session_name already running"
    return
  fi

  tmux new-session -d -s "$session_name" "$command"
  echo "[ok] started $session_name"
}

stop_session() {
  local session_name="$1"

  if ! session_exists "$session_name"; then
    echo "[skip] $session_name not running"
    return
  fi

  tmux kill-session -t "$session_name"
  echo "[ok] stopped $session_name"
}

show_status() {
  local session_name="$1"
  if session_exists "$session_name"; then
    echo "[up]   $session_name"
  else
    echo "[down] $session_name"
  fi
}

show_logs() {
  local session_name="$1"

  if ! session_exists "$session_name"; then
    echo "===== $session_name (not running) ====="
    return
  fi

  echo "===== $session_name ====="
  tmux capture-pane -pt "$session_name:0.0" -S -40
}

start_stack() {
  require_tmux
  require_backend_venv
  require_frontend_deps

  start_session "$BACKEND_SESSION" "$BACKEND_CMD"
  start_session "$BRIDGE_SESSION" "$BRIDGE_CMD"
  start_session "$FRONTEND_SESSION" "$FRONTEND_CMD"

  cat <<EOF

Monitoring stack requested.
  frontend: http://127.0.0.1:5173
  backend:  http://127.0.0.1:8001
  bridge:   http://127.0.0.1:8000

Use:
  ./run_monitoring.sh status
  ./run_monitoring.sh logs
EOF
}

stop_stack() {
  require_tmux
  stop_session "$FRONTEND_SESSION"
  stop_session "$BRIDGE_SESSION"
  stop_session "$BACKEND_SESSION"
}

restart_stack() {
  stop_stack
  start_stack
}

logs_stack() {
  require_tmux
  show_logs "$BACKEND_SESSION"
  echo
  show_logs "$BRIDGE_SESSION"
  echo
  show_logs "$FRONTEND_SESSION"
}

status_stack() {
  require_tmux
  show_status "$BACKEND_SESSION"
  show_status "$BRIDGE_SESSION"
  show_status "$FRONTEND_SESSION"
}

main() {
  local command="${1:-}"

  case "$command" in
    start)
      start_stack
      ;;
    stop)
      stop_stack
      ;;
    restart)
      restart_stack
      ;;
    status)
      status_stack
      ;;
    logs)
      logs_stack
      ;;
    *)
      usage
      exit 1
      ;;
  esac
}

main "$@"
