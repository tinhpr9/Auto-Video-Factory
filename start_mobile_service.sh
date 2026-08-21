#!/usr/bin/env bash
# ==============================================================================
# Auto-Video-Factory Mobile One-Tap Service Launcher
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# 1. Environment defaults for Phone-Only Flow Runtime
export AVF_PROVIDER="flow"
export AVF_LOCAL_PHONE="1"
export AVF_REQUIRE_AUTH="0"
export AVF_HOST="127.0.0.1"
export AVF_PORT="8000"
export FLOW_PROJECT_ID="${FLOW_PROJECT_ID:-362c6899-f74f-4118-b7d8-613ade3cd3af}"
export FLOW_CDP_URL="${FLOW_CDP_URL:-http://127.0.0.1:9222}"
export FLOW_ANDROID_CDP="1"
export AVF_FLOW_MODE="flow_balanced"
export AVF_FLOW_MODEL="omni_flash"

echo "========================================================"
echo "🚀 Khởi động Auto-Video-Factory One-Tap Mobile Service"
echo "========================================================"

# 2. Check ADB Server
ADB_BIN="/data/data/com.termux/files/usr/bin/adb"
if [ ! -x "$ADB_BIN" ] && command -v adb >/dev/null 2>&1; then
    ADB_BIN="$(command -v adb)"
fi

if [ -x "$ADB_BIN" ]; then
    echo "📱 Kiểm tra ADB & Chrome DevTools..."
    "$ADB_BIN" start-server >/dev/null 2>&1 || true
    # Attempt forwarding
    "$ADB_BIN" forward tcp:9222 localabstract:chrome_devtools_remote >/dev/null 2>&1 || true
fi

# 3. Locate Python virtualenv
PYTHON_BIN=""
for candidate in "$SCRIPT_DIR/.venv/bin/python3" "$SCRIPT_DIR/../Auto-Video-Factory/.venv/bin/python3" "/root/Auto-Video-Factory/.venv/bin/python3"; do
    if [ -x "$candidate" ]; then
        PYTHON_BIN="$candidate"
        break
    fi
done

if [ -z "$PYTHON_BIN" ]; then
    PYTHON_BIN="$(command -v python3)"
fi

echo "🌐 Web UI: http://127.0.0.1:${AVF_PORT}"
echo "✨ Model mặc định: Omni Flash (4s, 7 credits, tỉ lệ 9:16)"
echo "💡 Bạn có thể lưu trang web vào Màn hình chính (PWA) để mở 1 chạm mỗi ngày."
echo "========================================================"

# 4. Start local Web server first in background
"$PYTHON_BIN" -m auto_video_factory.web &
SERVER_PID=$!

cleanup() {
    if [ -n "${SERVER_PID:-}" ] && kill -0 "$SERVER_PID" 2>/dev/null; then
        kill -TERM "$SERVER_PID" 2>/dev/null || true
    fi
}
trap cleanup EXIT INT TERM

# 5. Bounded readiness probe before launching browser
echo "⏳ Đang chờ Web server sẵn sàng..."
READY=0
PROBE_MAX_ATTEMPTS=40

for ((i=1; i<=PROBE_MAX_ATTEMPTS; i++)); do
    if ! kill -0 "$SERVER_PID" 2>/dev/null; then
        echo "❌ Server process exited unexpectedly." >&2
        wait "$SERVER_PID" 2>/dev/null || true
        exit 1
    fi

    # Probe /health endpoint via python urllib
    if "$PYTHON_BIN" -c "
import urllib.request, sys
try:
    with urllib.request.urlopen('http://127.0.0.1:${AVF_PORT}/health', timeout=1) as resp:
        if resp.status == 200:
            sys.exit(0)
except Exception:
    sys.exit(1)
" >/dev/null 2>&1; then
        READY=1
        break
    fi
    sleep 0.25
done

if [ "$READY" -eq 1 ]; then
    echo "✅ Server đã sẵn sàng. Mở Web UI..."
    if command -v am >/dev/null 2>&1; then
        am start -a android.intent.action.VIEW -d "http://127.0.0.1:${AVF_PORT}" >/dev/null 2>&1 || true
    fi
else
    echo "❌ Server readiness probe timed out after $((PROBE_MAX_ATTEMPTS / 4)) seconds." >&2
    exit 1
fi

# Wait for server to run in foreground
wait "$SERVER_PID"
