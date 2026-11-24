#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$ROOT_DIR/scripts/run_development.sh"
WORKTREE_KEY=$(printf '%s' "$ROOT_DIR" | sha256sum)
WORKTREE_KEY=${WORKTREE_KEY:0:12}
BACKEND_SESSION="gpu_dev_${WORKTREE_KEY}_api"
FRONTEND_SESSION="gpu_dev_${WORKTREE_KEY}_ui"
BACKEND_PORT=${GPU_MONITOR_DEV_BACKEND_PORT:-8101}
FRONTEND_PORT=${GPU_MONITOR_DEV_PORT:-5175}
COLLECT=${GPU_MONITOR_DEV_COLLECT:-false}

fail() { printf '%s\n' "$*" >&2; exit 1; }
session_exists() { tmux has-session -t "=$1" 2>/dev/null; }
stop_session() {
  if session_exists "$1"; then tmux kill-session -t "=$1"; fi
}

validate_port() {
  [[ "$1" =~ ^[1-9][0-9]{0,4}$ ]] && (( $1 <= 65535 )) || fail "Invalid development port: $1"
}

start() {
  validate_port "$BACKEND_PORT"
  validate_port "$FRONTEND_PORT"
  [[ "$BACKEND_PORT" != "$FRONTEND_PORT" ]] || fail "Use different frontend and backend ports."
  [[ "$COLLECT" == true || "$COLLECT" == false ]] || fail "GPU_MONITOR_DEV_COLLECT must be true or false."
  [[ -f "$ROOT_DIR/.env" ]] || fail "Missing $ROOT_DIR/.env. Create it from .env.example."
  [[ -x "$ROOT_DIR/.venv/bin/uvicorn" ]] || fail "Install the backend dependencies into $ROOT_DIR/.venv."
  [[ -x "$ROOT_DIR/frontend/node_modules/.bin/vite" ]] || fail "Run npm ci in $ROOT_DIR/frontend."
  command -v npm >/dev/null || fail "Install Node.js and put npm on PATH."

  if session_exists "$BACKEND_SESSION" || session_exists "$FRONTEND_SESSION"; then
    fail "A development session already exists for this worktree. Use status or restart."
  fi
  for port in "$BACKEND_PORT" "$FRONTEND_PORT"; do
    listener=$(ss -H -ltn "sport = :$port") || fail "Could not check port $port."
    [[ -z "$listener" ]] || fail "Development port $port is already in use."
  done

  tmux new-session -d -s "$BACKEND_SESSION" bash "$SCRIPT" _backend "$BACKEND_PORT" "$COLLECT"
  if ! tmux new-session -d -s "$FRONTEND_SESSION" -e "PATH=$PATH" bash "$SCRIPT" _frontend "$FRONTEND_PORT" "$BACKEND_PORT"; then
    stop_session "$BACKEND_SESSION"
    fail "Could not create the development frontend session."
  fi
  printf 'Development frontend: http://127.0.0.1:%s/\nAPI: http://127.0.0.1:%s/health\n' "$FRONTEND_PORT" "$BACKEND_PORT"
}

case "${1:-}" in
  start) start ;;
  _backend)
    cd "$ROOT_DIR"
    export DATABASE_URL="sqlite+aiosqlite:///$ROOT_DIR/data/dev_gpu_monitor.db"
    export MONITORING_DISABLE_SLACK=true
    export MONITORING_EXPECTED_SERVER_COUNT=0 MONITORING_DATABASE_BACKUP_DIR=
    export MONITORING_DISABLE_COLLECTORS=true
    if [[ "$3" == true ]]; then export MONITORING_DISABLE_COLLECTORS=false; fi
    exec ./.venv/bin/uvicorn backend.main:app --host 127.0.0.1 --port "$2"
    ;;
  _frontend)
    cd "$ROOT_DIR/frontend"
    export MONITORING_API_TARGET="http://127.0.0.1:$3"
    export MONITORING_WS_TARGET="ws://127.0.0.1:$3"
    exec npm run dev -- --host 127.0.0.1 --port "$2" --strictPort
    ;;
  stop) stop_session "$FRONTEND_SESSION"; stop_session "$BACKEND_SESSION" ;;
  restart) "$SCRIPT" stop; "$SCRIPT" start ;;
  status)
    for session in "$BACKEND_SESSION" "$FRONTEND_SESSION"; do
      if session_exists "$session"; then printf '[up] %s\n' "$session"; else printf '[down] %s\n' "$session"; fi
    done
    ;;
  logs)
    for session in "$BACKEND_SESSION" "$FRONTEND_SESSION"; do
      if session_exists "$session"; then tmux capture-pane -pt "=$session" -S -60; fi
    done
    ;;
  *) fail "Usage: $0 {start|stop|restart|status|logs}" ;;
esac
