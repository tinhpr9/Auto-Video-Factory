#!/usr/bin/env bash
# ==============================================================================
# Auto-Video-Factory Mobile One-Tap Service Launcher
# Architecture: Native Termux Chromium -> Exact Localhost CDP -> AVF Web Service
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
export FLOW_CDP_PORT="${FLOW_CDP_PORT:-9222}"
export FLOW_CDP_URL="${FLOW_CDP_URL:-http://127.0.0.1:${FLOW_CDP_PORT}}"
export AVF_BROWSER_BACKEND="${AVF_BROWSER_BACKEND:-native}"
export AVF_FLOW_MODE="flow_balanced"
export AVF_FLOW_MODEL="omni_flash"

echo "========================================================"
echo "🚀 Khởi động Auto-Video-Factory One-Tap Mobile Service"
echo "🌐 Backend: ${AVF_BROWSER_BACKEND} | CDP: ${FLOW_CDP_URL}"
echo "========================================================"

# 2. Locate Python binary
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

# 3. Ensure CDP Backend Ownership (CDP_OWNER=exactly_one)
if [ "$AVF_BROWSER_BACKEND" = "native" ]; then
    export FLOW_ANDROID_CDP="0"
    echo "📱 Đảm bảo Native Termux Chromium trên cổng ${FLOW_CDP_PORT} (zero-ADB daily path)..."
    "$PYTHON_BIN" -c "
import sys
from auto_video_factory.flow_provider.native_chromium import NativeChromiumManager, NativeChromiumConfig
mgr = NativeChromiumManager(NativeChromiumConfig(port=int('${FLOW_CDP_PORT}'), host='127.0.0.1', headless=True))
if not mgr.ensure(timeout=8.0):
    print('⚠️ Native Chromium chưa thể khởi động tự động. Tiếp tục với CDP endpoint được cấu hình.')
" || true
elif [ "$AVF_BROWSER_BACKEND" = "adb" ]; then
    export FLOW_ANDROID_CDP="1"
    echo "📱 Khởi chạy Legacy Android ADB Fallback..."
    ADB_BIN="/data/data/com.termux/files/usr/bin/adb"
    if [ ! -x "$ADB_BIN" ] && command -v adb >/dev/null 2>&1; then
        ADB_BIN="$(command -v adb)"
    fi
    if [ -x "$ADB_BIN" ]; then
        "$ADB_BIN" start-server >/dev/null 2>&1 || true
        "$ADB_BIN" connect 127.0.0.1:5555 >/dev/null 2>&1 || true
        "$ADB_BIN" forward "tcp:${FLOW_CDP_PORT}" localabstract:chrome_devtools_remote >/dev/null 2>&1 || true
    fi
fi

echo "🌐 Web UI: http://127.0.0.1:${AVF_PORT}"
echo "✨ Model mặc định: Omni Flash (4s, 7 credits, tỉ lệ 9:16)"
echo "💡 Bạn có thể lưu trang web vào Màn hình chính (PWA) để mở 1 chạm mỗi ngày."
echo "========================================================"

# 4. Start local Web server in background
"$PYTHON_BIN" -m auto_video_factory.web &
SERVER_PID=$!

cleanup() {
    if [ -n "${SERVER_PID:-}" ] && kill -0 "$SERVER_PID" 2>/dev/null; then
        kill -TERM "$SERVER_PID" 2>/dev/null || true
    fi
}
trap cleanup EXIT INT TERM

# 5. Bounded readiness probe before launching browser UI
echo "⏳ Đang chờ Web server sẵn sàng..."
READY=0
PROBE_MAX_ATTEMPTS=40

for ((i=1; i<=PROBE_MAX_ATTEMPTS; i++)); do
    if ! kill -0 "$SERVER_PID" 2>/dev/null; then
        echo "❌ Server process exited unexpectedly." >&2
        wait "$SERVER_PID" 2>/dev/null || true
        exit 1
    fi

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
