#!/usr/bin/env bash
# ==============================================================================
# Auto-Video-Factory Mobile One-Tap Service Launcher & Persistent Supervisor
# Architecture: Android Chrome / Native Termux Chromium -> Exact CDP -> AVF Web Service (127.0.0.1:8000/health)
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# 1. Resolve canonical production root (decouple from PR worktrees)
if [ -n "${AVF_PRODUCTION_ROOT:-}" ] && [ -d "$AVF_PRODUCTION_ROOT" ]; then
    CANONICAL_ROOT="$(cd "$AVF_PRODUCTION_ROOT" && pwd)"
elif [ -d "$SCRIPT_DIR/.git" ] && [ -f "$SCRIPT_DIR/pyproject.toml" ]; then
    CANONICAL_ROOT="$SCRIPT_DIR"
elif [ -d "/root/Auto-Video-Factory" ] && [ -f "/root/Auto-Video-Factory/pyproject.toml" ]; then
    CANONICAL_ROOT="/root/Auto-Video-Factory"
else
    CANONICAL_ROOT="$SCRIPT_DIR"
fi
export AVF_PRODUCTION_ROOT="$CANONICAL_ROOT"

# 2. State and PID directory setup (anchored to canonical root)
STATE_DIR="${AVF_STATE_DIR:-$CANONICAL_ROOT/output/web}"
mkdir -p "$STATE_DIR"
export AVF_STATE_DIR="$STATE_DIR"
SUPERVISOR_PID_FILE="$STATE_DIR/avf_supervisor.pid"
WORKER_PID_FILE="$STATE_DIR/avf_web.pid"
SUPERVISOR_LOG="$STATE_DIR/avf_supervisor.log"

# 3. Environment defaults for Phone-Only Flow Runtime
export AVF_PROVIDER="flow"
export AVF_LOCAL_PHONE="1"
export AVF_REQUIRE_AUTH="0"
export AVF_HOST="127.0.0.1"
export AVF_PORT="${AVF_PORT:-8000}"
export FLOW_PROJECT_ID="${FLOW_PROJECT_ID:-362c6899-f74f-4118-b7d8-613ade3cd3af}"
export FLOW_CDP_PORT="${FLOW_CDP_PORT:-9224}"
export FLOW_CDP_URL="${FLOW_CDP_URL:-http://127.0.0.1:${FLOW_CDP_PORT}}"
export FLOW_ANDROID_CDP="${FLOW_ANDROID_CDP:-1}"
export AVF_BROWSER_BACKEND="${AVF_BROWSER_BACKEND:-android_chrome}"
export AVF_FLOW_MODE="flow_balanced"
export AVF_FLOW_MODEL="omni_flash"

# 4. Locate Python binary (prefer canonical root venv)
PYTHON_BIN=""
for candidate in "$CANONICAL_ROOT/.venv/bin/python3" "$CANONICAL_ROOT/.venv/bin/python" "$SCRIPT_DIR/.venv/bin/python3" "$SCRIPT_DIR/.venv/bin/python" "/root/Auto-Video-Factory/.venv/bin/python3"; do
    if [ -x "$candidate" ]; then
        PYTHON_BIN="$candidate"
        break
    fi
done

if [ -z "$PYTHON_BIN" ]; then
    PYTHON_BIN="$(command -v python3)"
fi

# 4. Delegate to Python Persistent Supervisor
CMD="${1:-start}"

echo "========================================================"
echo "🚀 Auto-Video-Factory Persistent Mobile Service Launcher"
echo "🌐 Backend: ${AVF_BROWSER_BACKEND} | CDP: ${FLOW_CDP_URL}"
echo "========================================================"

case "$CMD" in
    start|--daemon|-d)
        "$PYTHON_BIN" -m auto_video_factory.supervisor start
        if [ -t 1 ] && command -v am >/dev/null 2>&1; then
            am start -a android.intent.action.VIEW -d "http://${AVF_HOST}:${AVF_PORT}" >/dev/null 2>&1 || true
        fi
        ;;
    stop)
        "$PYTHON_BIN" -m auto_video_factory.supervisor stop
        ;;
    restart)
        "$PYTHON_BIN" -m auto_video_factory.supervisor restart
        if [ -t 1 ] && command -v am >/dev/null 2>&1; then
            am start -a android.intent.action.VIEW -d "http://${AVF_HOST}:${AVF_PORT}" >/dev/null 2>&1 || true
        fi
        ;;
    status)
        "$PYTHON_BIN" -m auto_video_factory.supervisor status
        ;;
    foreground)
        "$PYTHON_BIN" -m auto_video_factory.supervisor foreground
        ;;
    *)
        echo "Usage: $0 {start|stop|restart|status|foreground}"
        exit 1
        ;;
esac

