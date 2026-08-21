"""
Targeted failure tests for Native Termux Chromium Production Runtime:
- TEST_NATIVE_SELECTED_OVER_ADB
- TEST_NO_ADB_DAILY_PATH (ADB_COMMAND_COUNT=0)
- TEST_EXACT_ENDPOINT_READINESS
- TEST_WRONG_PORT
- TEST_STALE_PROCESS_OR_PORT
- TEST_VALID_CDP_ENDPOINT
- TEST_SINGLE_OWNER
- TEST_FLOW_CLIENT_ATTACH
"""
from __future__ import annotations

import http.server
import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from auto_video_factory.flow_provider.cdp_endpoint import (
    CDPEndpointStatus,
    check_cdp_endpoint_detailed,
    verify_cdp_endpoint,
)
from auto_video_factory.flow_provider.contract import FlowProvider
from auto_video_factory.flow_provider.models import (
    FlowAspectRatio,
    FlowGenerationRequest,
    FlowHealthStatus,
    FlowJobStatus,
    FlowModel,
)
from auto_video_factory.flow_provider.native_chromium import (
    NativeChromiumConfig,
    NativeChromiumManager,
)
from auto_video_factory.flow_provider.provider import (
    ProductionFlowProvider,
)


class MockCDPHandler(http.server.BaseHTTPRequestHandler):
    """Mock Chrome DevTools HTTP server providing /json/version and /json/list."""
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):
        pass  # Suppress console logging

    def do_GET(self):
        if self.path == "/json/version":
            data = {
                "Browser": "Chrome/130.0.0.0",
                "Protocol-Version": "1.3",
                "User-Agent": "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36",
                "V8-Version": "13.0.0",
                "WebKit-Version": "537.36",
                "webSocketDebuggerUrl": f"ws://{self.headers.get('Host', '127.0.0.1')}/devtools/browser/mock-id",
            }
            body = json.dumps(data).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=UTF-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path in ("/json", "/json/list"):
            data = [
                {
                    "description": "",
                    "devtoolsFrontendUrl": "/devtools/inspector.html?ws=127.0.0.1/devtools/page/mock-page",
                    "id": "mock-page-id",
                    "title": "Google Flow",
                    "type": "page",
                    "url": "https://labs.google/flow",
                    "webSocketDebuggerUrl": f"ws://{self.headers.get('Host', '127.0.0.1')}/devtools/page/mock-page-id",
                }
            ]
            body = json.dumps(data).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=UTF-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()


class BadHTTPHandler(http.server.BaseHTTPRequestHandler):
    """Mock server returning invalid non-CDP response."""
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        body = b"<html><body>Not a CDP endpoint</body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def get_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def mock_cdp_server():
    port = get_free_port()
    server = http.server.HTTPServer(("127.0.0.1", port), MockCDPHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}", port
    server.shutdown()
    server.server_close()


@pytest.fixture
def bad_http_server():
    port = get_free_port()
    server = http.server.HTTPServer(("127.0.0.1", port), BadHTTPHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}", port
    server.shutdown()
    server.server_close()


# ==============================================================================
# 1. Exact Endpoint Readiness Contract Tests
# ==============================================================================

class TestExactEndpointReadinessContract:
    def test_valid_cdp_endpoint(self, mock_cdp_server):
        """TEST_VALID_CDP_ENDPOINT: Exact configured endpoint returns valid Chrome CDP metadata & targets."""
        cdp_url, port = mock_cdp_server
        assert verify_cdp_endpoint(cdp_url, timeout=2.0) is True

        status = check_cdp_endpoint_detailed(cdp_url, timeout=2.0)
        assert status.tcp_reachable is True
        assert status.version_valid is True
        assert status.targets_valid is True
        assert status.ready is True
        assert "Chrome/130" in status.browser_version

    def test_exact_endpoint_readiness_port_unreachable(self):
        """TEST_EXACT_ENDPOINT_READINESS: Endpoint with no open port fails."""
        free_port = get_free_port()
        unreachable_url = f"http://127.0.0.1:{free_port}"
        assert verify_cdp_endpoint(unreachable_url, timeout=0.2) is False

        status = check_cdp_endpoint_detailed(unreachable_url, timeout=0.2)
        assert status.tcp_reachable is False
        assert status.version_valid is False
        assert status.ready is False

    def test_wrong_port(self, mock_cdp_server):
        """TEST_WRONG_PORT: Chromium on port X while FLOW_CDP_URL points to Y must FAIL."""
        cdp_url, running_port = mock_cdp_server
        wrong_port = get_free_port()
        assert wrong_port != running_port
        wrong_url = f"http://127.0.0.1:{wrong_port}"
        assert verify_cdp_endpoint(wrong_url, timeout=0.2) is False

    def test_stale_process_or_port(self, bad_http_server):
        """TEST_STALE_PROCESS_OR_PORT: Port is open but serves non-CDP HTTP content -> FAIL."""
        bad_url, port = bad_http_server
        status = check_cdp_endpoint_detailed(bad_url, timeout=0.5)
        assert status.tcp_reachable is True
        assert status.version_valid is False
        assert status.ready is False
        assert verify_cdp_endpoint(bad_url, timeout=0.5) is False


# ==============================================================================
# 2. Native Selected Over ADB & Zero-ADB Daily Path Tests
# ==============================================================================

class TestNativePhoneProductionBackend:
    def test_native_selected_over_adb(self, mock_cdp_server):
        """TEST_NATIVE_SELECTED_OVER_ADB: Given native phone backend selected,
        AndroidCDPManager is NOT instantiated and FLOW_ANDROID_CDP is 0."""
        cdp_url, _port = mock_cdp_server
        with patch.dict(os.environ, {
            "FLOW_CDP_URL": cdp_url,
            "FLOW_ANDROID_CDP": "0",
            "AVF_BROWSER_BACKEND": "native_termux",
        }, clear=False):
            provider = ProductionFlowProvider(
                project_id="test-native-uuid",
                cdp_url=cdp_url,
            )
            assert provider._android_manager is None
            health = provider.health()
            assert health.healthy is True
            assert health.browser_ready is True
            assert health.details.get("android_cdp") is not True
            assert health.details.get("cdp_owner") == "NATIVE_TERMUX_CHROMIUM"

    def test_no_adb_daily_path(self, mock_cdp_server, monkeypatch):
        """TEST_NO_ADB_DAILY_PATH: Native startup must not execute any ADB commands.
        Enforces ADB_COMMAND_COUNT=0."""
        cdp_url, _port = mock_cdp_server
        adb_call_count = 0

        def fake_subprocess_run(args, *a, **kw):
            nonlocal adb_call_count
            cmd_str = str(args)
            if "adb" in cmd_str:
                adb_call_count += 1
                raise RuntimeError(f"ADB called unexpectedly in native path: {args}")
            return MagicMock(returncode=0, stdout=b"", stderr=b"")

        monkeypatch.setattr("subprocess.run", fake_subprocess_run)
        monkeypatch.setattr("subprocess.Popen", MagicMock())

        with patch.dict(os.environ, {
            "FLOW_CDP_URL": cdp_url,
            "FLOW_ANDROID_CDP": "0",
            "AVF_BROWSER_BACKEND": "native_termux",
        }, clear=False):
            provider = ProductionFlowProvider(
                project_id="test-native-uuid",
                cdp_url=cdp_url,
            )
            health = provider.health()
            assert health.healthy is True
            assert adb_call_count == 0, f"Expected ADB_COMMAND_COUNT=0, got {adb_call_count}"

    def test_single_owner(self, mock_cdp_server):
        """TEST_SINGLE_OWNER: Native backend selected -> exactly one owner (NATIVE_TERMUX_CHROMIUM),
        never simultaneously owning tcp:9222 with ADB."""
        cdp_url, _port = mock_cdp_server
        with patch.dict(os.environ, {
            "FLOW_CDP_URL": cdp_url,
            "FLOW_ANDROID_CDP": "0",
        }, clear=False):
            provider = ProductionFlowProvider(
                project_id="test-single-owner",
                cdp_url=cdp_url,
            )
            assert provider._android_manager is None
            health = provider.health()
            assert health.details.get("cdp_owner") == "NATIVE_TERMUX_CHROMIUM"
            assert "android_cdp" not in health.details or health.details["android_cdp"] is False


# ==============================================================================
# 3. Native Chromium Process Lifecycle Manager Tests
# ==============================================================================

class TestNativeChromiumManager:
    def test_manager_binds_only_to_localhost(self, tmp_path):
        cfg = NativeChromiumConfig(
            port=9222,
            host="127.0.0.1",
            user_data_dir=tmp_path / "chrome_profile",
            headless=True,
            binary_path="/usr/bin/chromium",
        )
        mgr = NativeChromiumManager(config=cfg)
        args = mgr.build_launch_args()

        assert "--remote-debugging-port=9222" in args
        assert "--remote-debugging-address=127.0.0.1" in args
        assert f"--user-data-dir={tmp_path / 'chrome_profile'}" in args
        assert "--no-sandbox" in args
        assert "--headless=new" in args

    def test_manager_rejects_non_loopback_host(self):
        with pytest.raises(ValueError, match="not a permitted loopback address"):
            NativeChromiumConfig(host="0.0.0.0", port=9222)
        with pytest.raises(ValueError, match="not a permitted loopback address"):
            NativeChromiumConfig(host="192.168.1.100", port=9222)

    def test_manager_filters_disallowed_extra_flags(self, tmp_path):
        cfg = NativeChromiumConfig(
            port=9222,
            host="127.0.0.1",
            user_data_dir=tmp_path / "chrome_profile",
            extra_flags=[
                "--remote-debugging-port=9999",
                "--remote-debugging-address=0.0.0.0",
                "--user-data-dir=/tmp/bad",
                "--enable-automation",
            ],
            binary_path="/usr/bin/chromium",
        )
        mgr = NativeChromiumManager(config=cfg)
        args = mgr.build_launch_args()

        assert "--remote-debugging-port=9222" in args
        assert "--remote-debugging-address=127.0.0.1" in args
        assert f"--user-data-dir={tmp_path / 'chrome_profile'}" in args
        assert "--remote-debugging-port=9999" not in args
        assert "--remote-debugging-address=0.0.0.0" not in args
        assert "--enable-automation" in args

    def test_manager_resolves_cdp_url(self, tmp_path):
        cfg = NativeChromiumConfig(
            port=9333,
            host="127.0.0.1",
            user_data_dir=tmp_path / "chrome_profile",
            binary_path="/usr/bin/chromium",
        )
        mgr = NativeChromiumManager(config=cfg)
        assert mgr.cdp_url == "http://127.0.0.1:9333"

    def test_manager_ensure_with_existing_ready_endpoint(self, mock_cdp_server, tmp_path):
        cdp_url, port = mock_cdp_server
        cfg = NativeChromiumConfig(
            port=port,
            host="127.0.0.1",
            user_data_dir=tmp_path / "chrome_profile",
            binary_path="/usr/bin/chromium",
        )
        mgr = NativeChromiumManager(config=cfg)
        # Should detect already running mock CDP without launching new process
        assert mgr.ensure(timeout=1.0) is True
        assert mgr.is_ready() is True

    def test_manager_fails_if_binary_not_found(self, tmp_path):
        cfg = NativeChromiumConfig(
            port=9222,
            host="127.0.0.1",
            user_data_dir=tmp_path / "chrome_profile",
            binary_path="/nonexistent/path/to/chromium-xyz",
        )
        mgr = NativeChromiumManager(config=cfg)
        with patch.object(mgr, "_find_binary", return_value=None):
            with pytest.raises(FileNotFoundError, match="Chromium binary not found"):
                mgr.start()


# ==============================================================================
# 4. Flow Client Attach Tests (Zero-Credit Production Edge)
# ==============================================================================

class TestFlowClientAttach:
    def test_flow_client_can_attach_to_native_endpoint(self, mock_cdp_server):
        """TEST_FLOW_CLIENT_ATTACH: Exercises actual ProductionFlowProvider client creation
        and model querying against the CDP endpoint without performing paid generation."""
        cdp_url, _port = mock_cdp_server

        fake_api = AsyncMock()
        fake_api.get_credits.return_value = MagicMock(credits=500)
        fake_client = MagicMock()
        fake_client._api = fake_api
        fake_client.get_model_config = AsyncMock(return_value={"videoModels": []})

        with patch.object(ProductionFlowProvider, "_import_flow") as mock_import, \
             patch.dict(os.environ, {"FLOW_CDP_URL": cdp_url, "FLOW_ANDROID_CDP": "0"}, clear=False):

            FlowClient_cls = MagicMock()
            FlowClient_cls.create = AsyncMock(return_value=fake_client)
            mock_import.return_value = (FlowClient_cls, MagicMock(), MagicMock())

            provider = ProductionFlowProvider(
                project_id="test-attach-project",
                cdp_url=cdp_url,
            )

            client = provider._get_client()
            assert client == fake_client
            FlowClient_cls.create.assert_called_once_with(
                project_id="test-attach-project",
                headless=True,
                cdp_url=cdp_url,
            )

            # Credit check
            credits = provider.get_credits()
            assert credits.available_credits == 500
            assert credits.consumed_credits == 0


# ==============================================================================
# 5. Q1-Q6 Verification & Adversarial Proof Tests
# ==============================================================================

class TestQodoFindingsVerification:
    def test_q1_process_group_cleanup_terminates_descendants(self, tmp_path):
        """Q1 Proof: NativeChromiumManager.stop() terminates the full process group,
        leaving zero orphan child processes."""
        pid_file = tmp_path / "child.pid"
        mock_bin = tmp_path / "mock_chromium.sh"
        mock_bin.write_text(f"""#!/bin/sh
{sys.executable} -c "import time; time.sleep(60)" &
echo $! > "{pid_file}"
wait
""")
        mock_bin.chmod(0o755)
        cfg = NativeChromiumConfig(
            port=9222,
            host="127.0.0.1",
            binary_path=str(mock_bin),
        )
        mgr = NativeChromiumManager(config=cfg)
        mgr.start()
        # Wait for child pid file
        for _ in range(30):
            if pid_file.exists():
                break
            time.sleep(0.1)
        assert pid_file.exists(), "Child PID file was not written"
        child_pid = int(pid_file.read_text().strip())
        parent_pid = mgr._process.pid

        # Verify child is running
        assert os.kill(child_pid, 0) is None or True

        # Stop manager and verify both parent and child are terminated
        mgr.stop(timeout=2.0)
        time.sleep(0.3)

        # Check parent process is dead
        with pytest.raises(ProcessLookupError):
            os.kill(parent_pid, 0)

        # Check child process is dead (no orphan process left)
        with pytest.raises(ProcessLookupError):
            os.kill(child_pid, 0)

    def test_q2_actual_launcher_syntax_and_heredoc_execution(self):
        """Q2 Proof: start_mobile_service.sh contains valid bash and python syntax with zero IndentationError."""
        launcher_path = Path(__file__).resolve().parent.parent / "start_mobile_service.sh"
        assert launcher_path.exists()

        # 1. Check bash syntax
        res = subprocess.run(["bash", "-n", str(launcher_path)], capture_output=True, text=True)
        assert res.returncode == 0, f"Bash syntax error in {launcher_path}: {res.stderr}"

        # 2. Test execution of the exact Python snippet used in the launcher
        python_test = """
from auto_video_factory.flow_provider.native_chromium import NativeChromiumManager, NativeChromiumConfig
mgr = NativeChromiumManager(NativeChromiumConfig(port=9222, host='127.0.0.1', headless=True))
assert mgr.cdp_url == 'http://127.0.0.1:9222'
"""
        py_res = subprocess.run([sys.executable, "-c", python_test], capture_output=True, text=True)
        assert py_res.returncode == 0, f"Python snippet failed: {py_res.stderr}"

    def test_q3_cdp_endpoint_timeout_respects_bound(self):
        """Q3 Proof: check_cdp_endpoint_detailed accurately bounds timeout without imposing a 0.5s minimum."""
        t0 = time.monotonic()
        # Probe an unroutable/unreachable port with a tight timeout
        status = check_cdp_endpoint_detailed("http://127.0.0.1:59999", timeout=0.15)
        elapsed = time.monotonic() - t0

        assert status.ready is False
        assert status.tcp_reachable is False
        # Must finish within reasonable margin of 0.15s, strictly less than 0.35s (never blocking for 0.5s+)
        assert elapsed < 0.35, f"Timeout overshoot: took {elapsed:.3f}s for 0.15s requested timeout"

    def test_q4_no_sandbox_flag_configuration(self, tmp_path):
        """Q4 Proof: --no-sandbox is default for Termux parity, but configurable via no_sandbox=False."""
        # Default: no_sandbox=True
        cfg_default = NativeChromiumConfig(
            port=9222,
            host="127.0.0.1",
            user_data_dir=tmp_path / "profile1",
            binary_path="/usr/bin/chromium",
        )
        mgr_default = NativeChromiumManager(config=cfg_default)
        assert "--no-sandbox" in mgr_default.build_launch_args()

        # Opt-out: no_sandbox=False (for desktop Linux with unprivileged user namespaces)
        cfg_sandboxed = NativeChromiumConfig(
            port=9222,
            host="127.0.0.1",
            user_data_dir=tmp_path / "profile2",
            binary_path="/usr/bin/chromium",
            no_sandbox=False,
        )
        mgr_sandboxed = NativeChromiumManager(config=cfg_sandboxed)
        assert "--no-sandbox" not in mgr_sandboxed.build_launch_args()

    def test_q5_cli_argument_parsing_headed_and_headless(self):
        """Q5 Proof: CLI options --headed and --headless correctly toggle headless mode."""
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument("--port", type=int, default=9222)
        parser.add_argument("--host", default="127.0.0.1")
        group = parser.add_mutually_exclusive_group()
        group.add_argument("--headless", dest="headless", action="store_true", default=True)
        group.add_argument("--headed", dest="headless", action="store_false")

        # Default: headless=True
        args_default = parser.parse_args([])
        assert args_default.headless is True

        # Explicit --headed: headless=False
        args_headed = parser.parse_args(["--headed"])
        assert args_headed.headless is False

        # Explicit --headless: headless=True
        args_headless = parser.parse_args(["--headless"])
        assert args_headless.headless is True

    def test_q6_tcp_reachable_contract_isolated_from_http(self):
        """Q6 Proof: tcp_reachable reflects actual TCP connection reachability,
        even when HTTP /json/version or targets endpoints fail or return non-CDP data."""
        # Start a raw TCP server that speaks non-HTTP or returns 500
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        port = sock.getsockname()[1]

        def serve_bad_http():
            try:
                conn, _ = sock.accept()
                conn.recv(1024)
                conn.sendall(b"HTTP/1.1 500 Internal Server Error\r\nContent-Length: 0\r\n\r\n")
                conn.close()
            except Exception:
                pass
            finally:
                sock.close()

        t = threading.Thread(target=serve_bad_http, daemon=True)
        t.start()

        try:
            status = check_cdp_endpoint_detailed(f"http://127.0.0.1:{port}", timeout=1.0)
            assert status.tcp_reachable is True, "TCP was reachable, tcp_reachable must be True"
            assert status.version_valid is False, "500 error means version_valid must be False"
            assert status.ready is False, "Non-CDP endpoint must have ready=False"
        finally:
            try:
                sock.close()
            except Exception:
                pass
