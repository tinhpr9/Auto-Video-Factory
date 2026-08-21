"""
Tests for Android ADB, Chrome DevTools Protocol manager via adbutils, and ProductionFlowProvider integration.
"""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import pytest

from auto_video_factory.flow_provider.android import (
    AndroidCDPManager,
    CDPTransport,
    CDPTransportMode,
    DirectSocketTransport,
    ForegroundPolicy,
    LocalAdbTcpTransport,
    SYSTEM_PACKAGES,
    WirelessAdbTransport,
)
from auto_video_factory.flow_provider.models import (
    FlowAspectRatio,
    FlowGenerationRequest,
    FlowJobStatus,
    FlowModel,
)
from auto_video_factory.flow_provider.provider import ProductionFlowProvider


class TestAndroidCDPManager:
    """Unit tests for AndroidCDPManager using adbutils primitives."""

    def test_is_adb_connected_single_device(self):
        manager = AndroidCDPManager()
        mock_device = MagicMock()
        mock_device.serial = "192.168.1.57:37453"
        mock_client = MagicMock()
        mock_client.device_list.return_value = [mock_device]

        with patch.object(manager, "_get_client", return_value=mock_client):
            assert manager.is_adb_connected() is True
            assert manager.get_device() == mock_device

    def test_is_adb_connected_no_device(self):
        manager = AndroidCDPManager()
        mock_client = MagicMock()
        mock_client.device_list.return_value = []

        with patch.object(manager, "_get_client", return_value=mock_client):
            assert manager.is_adb_connected() is False
            assert manager.get_device() is None

    def test_get_device_excludes_local_tcp_serials_from_filter(self):
        """Thread 4: get_device() should filter 127.0.0.1:* serials like WirelessAdbTransport."""
        manager = AndroidCDPManager()
        local_dev = MagicMock()
        local_dev.serial = "127.0.0.1:5555"
        wireless_dev = MagicMock()
        wireless_dev.serial = "192.168.1.50:37453"
        mock_client = MagicMock()
        # Both local and wireless present — without filtering this would be 2 devices → None
        # With filtering, local TCP is excluded, leaving only 1 wireless device
        mock_client.device_list.return_value = [local_dev, wireless_dev]

        with patch.object(manager, "_get_client", return_value=mock_client):
            device = manager.get_device()
            assert device == wireless_dev

    def test_get_device_multiple_devices_fails_closed_on_ambiguity(self):
        """Finding D: Multi-device ambiguity must fail closed (return None) to prevent misrouting."""
        manager = AndroidCDPManager()
        mock_dev1 = MagicMock(serial="192.168.1.57:37453")
        mock_dev2 = MagicMock(serial="emulator-5554")
        mock_client = MagicMock()
        mock_client.device_list.return_value = [mock_dev1, mock_dev2]

        with patch.object(manager, "_get_client", return_value=mock_client):
            device = manager.get_device()
            assert device is None

    def test_ensure_cdp_forward_local_tcp_auto_connect_when_no_transport_selected(self):
        """Thread 1/3: ensure_cdp_forward() must try local_tcp.ensure() when select_transport() returns None."""
        manager = AndroidCDPManager(cdp_port=9222)
        mock_local_tcp = MagicMock(spec=LocalAdbTcpTransport)
        mock_local_tcp.ensure.return_value = True

        with patch.object(manager, "select_transport", return_value=None), \
             patch.object(manager, "_get_local_tcp_transport", return_value=mock_local_tcp):
            result = manager.ensure_cdp_forward()
            assert result is True
            mock_local_tcp.ensure.assert_called_once()

    def test_socket_discovery_rejects_webview_socket(self):
        """Thread 2: discover_device_devtools_socket must NOT accept webview_devtools_remote_<pid>."""
        from auto_video_factory.flow_provider.android import discover_device_devtools_socket
        mock_device = MagicMock()
        # WebView socket listed BEFORE Chrome socket
        mock_device.shell.return_value = (
            "0000000000000000: 00000002 00000000 00010000 0001 01 11111 @webview_devtools_remote_9999\n"
            "0000000000000000: 00000002 00000000 00010000 0001 01 22222 @chrome_devtools_remote_1234\n"
        )
        result = discover_device_devtools_socket(mock_device)
        assert result == "chrome_devtools_remote_1234", f"Expected chrome socket, got: {result}"

    def test_socket_discovery_rejects_generic_devtools_remote(self):
        """Thread 2: discover_device_devtools_socket must reject sockets that don't start with chrome_devtools_remote."""
        from auto_video_factory.flow_provider.android import discover_device_devtools_socket
        mock_device = MagicMock()
        mock_device.shell.return_value = (
            "0000000000000000: 00000002 00000000 00010000 0001 01 33333 @some_other_devtools_remote\n"
        )
        result = discover_device_devtools_socket(mock_device)
        # Must fall back to default when no valid chrome socket found
        assert result == "chrome_devtools_remote"

    def test_get_device_explicit_serial(self):
        manager = AndroidCDPManager(serial="custom-serial-123")
        mock_device = MagicMock(serial="custom-serial-123")
        mock_client = MagicMock()
        mock_client.device.return_value = mock_device

        with patch.object(manager, "_get_client", return_value=mock_client):
            device = manager.get_device()
            mock_client.device.assert_called_once_with(serial="custom-serial-123")
            assert device == mock_device

    def test_discover_chrome_devtools_socket(self):
        manager = AndroidCDPManager()
        mock_device = MagicMock()
        mock_device.shell.return_value = (
            "0000000000000000: 00000002 00000000 00010000 0001 01 67495864 @chrome_devtools_remote\n"
            "0000000000000000: 00000002 00000000 00010000 0001 01 67495865 /dev/socket/adbd\n"
        )
        with patch.object(manager, "get_device", return_value=mock_device):
            socket_name = manager.discover_chrome_devtools_socket()
            assert socket_name == "chrome_devtools_remote"

    def test_ensure_cdp_forward_existing(self):
        manager = AndroidCDPManager(cdp_port=9222)
        mock_device = MagicMock()
        mock_forward = SimpleNamespace(
            local="tcp:9222",
            remote="localabstract:chrome_devtools_remote",
        )
        mock_device.forward_list.return_value = [mock_forward]

        with patch.object(manager, "select_transport", return_value=None), \
             patch.object(manager, "get_device", return_value=mock_device), \
             patch.object(manager, "discover_chrome_devtools_socket", return_value="chrome_devtools_remote"), \
             patch("auto_video_factory.flow_provider.android.verify_cdp_endpoint", return_value=True):
            assert manager.ensure_cdp_forward() is True
            mock_device.forward.assert_not_called()

    def test_ensure_cdp_forward_new(self):
        manager = AndroidCDPManager(cdp_port=9222)
        mock_device = MagicMock()
        mock_device.forward_list.return_value = []

        with patch.object(manager, "select_transport", return_value=None), \
             patch.object(manager, "get_device", return_value=mock_device), \
             patch.object(manager, "discover_chrome_devtools_socket", return_value="chrome_devtools_remote"), \
             patch("auto_video_factory.flow_provider.android.verify_cdp_endpoint", return_value=True):
            assert manager.ensure_cdp_forward() is True
            mock_device.forward.assert_called_once_with(
                "tcp:9222", "localabstract:chrome_devtools_remote"
            )

    def test_get_current_foreground_app_via_app_current(self):
        manager = AndroidCDPManager()
        mock_device = MagicMock()
        mock_device.app_current.return_value = SimpleNamespace(
            package="free.tube.premium.advanced.tuber",
            activity=".main.MainActivity",
        )
        with patch.object(manager, "get_device", return_value=mock_device):
            pkg, act = manager.get_current_foreground_app()
            assert pkg == "free.tube.premium.advanced.tuber"
            assert act == "free.tube.premium.advanced.tuber.main.MainActivity"

    def test_get_current_foreground_app_dumpsys_fallback(self):
        manager = AndroidCDPManager()
        mock_device = MagicMock()
        mock_device.app_current.side_effect = RuntimeError("app_current not supported")
        mock_dumpsys = "  mCurrentFocus=Window{d4f6be9 u0 com.openai.chatgpt/com.openai.chatgpt.MainActivity type=1}\n"
        mock_device.shell.return_value = mock_dumpsys

        with patch.object(manager, "get_device", return_value=mock_device):
            pkg, act = manager.get_current_foreground_app()
            assert pkg == "com.openai.chatgpt"
            assert act == "com.openai.chatgpt.MainActivity"

    def test_restore_foreground_app_skips_chrome_and_system(self):
        manager = AndroidCDPManager()
        mock_device = MagicMock()
        with patch.object(manager, "get_device", return_value=mock_device):
            # Skipping Chrome
            assert manager.restore_foreground_app("com.android.chrome", "Main") is True
            # Skipping system launcher
            assert manager.restore_foreground_app("com.android.launcher3", "Launcher") is True
            mock_device.shell.assert_not_called()

    def test_restore_foreground_app_calls_shell_am_start(self):
        manager = AndroidCDPManager()
        mock_device = MagicMock()
        mock_device.shell.return_value = "Starting: Intent { act=android.intent.action.MAIN ... }"

        with patch.object(manager, "get_device", return_value=mock_device):
            assert manager.restore_foreground_app("com.openai.chatgpt", "com.openai.chatgpt.MainActivity") is True
            mock_device.shell.assert_called_once()
            cmd = mock_device.shell.call_args[0][0]
            assert "com.openai.chatgpt/com.openai.chatgpt.MainActivity" in cmd

    def test_scoped_foreground_background_policy(self):
        manager = AndroidCDPManager(foreground_policy=ForegroundPolicy.BACKGROUND)
        with patch.object(manager, "bring_chrome_to_foreground") as mock_bring, \
             patch.object(manager, "restore_foreground_app") as mock_restore:
            with manager.scoped_foreground_for_submit():
                pass
            mock_bring.assert_not_called()
            mock_restore.assert_not_called()

    def test_scoped_foreground_micro_foreground_restores_on_success(self):
        manager = AndroidCDPManager(foreground_policy=ForegroundPolicy.MICRO_FOREGROUND)
        with patch.object(manager, "get_current_foreground_app", return_value=("com.openai.chatgpt", "MainActivity")), \
             patch.object(manager, "bring_chrome_to_foreground") as mock_bring, \
             patch.object(manager, "restore_foreground_app") as mock_restore:
            with manager.scoped_foreground_for_submit():
                mock_bring.assert_called_once()
            mock_restore.assert_called_once_with("com.openai.chatgpt", "MainActivity")

    def test_scoped_foreground_micro_foreground_restores_on_exception(self):
        manager = AndroidCDPManager(foreground_policy=ForegroundPolicy.MICRO_FOREGROUND)
        with patch.object(manager, "get_current_foreground_app", return_value=("com.openai.chatgpt", "MainActivity")), \
             patch.object(manager, "bring_chrome_to_foreground"), \
             patch.object(manager, "restore_foreground_app") as mock_restore:
            with pytest.raises(RuntimeError):
                with manager.scoped_foreground_for_submit():
                    raise RuntimeError("Submit error")
            mock_restore.assert_called_once_with("com.openai.chatgpt", "MainActivity")


class TestProductionFlowProviderAndroidCDP:
    """Unit tests for ProductionFlowProvider with Android CDP integration."""

    def test_health_reports_android_cdp(self):
        manager = AndroidCDPManager(foreground_policy=ForegroundPolicy.AUTO)
        with patch.object(manager, "ensure_cdp_forward", return_value=True):
            provider = ProductionFlowProvider(
                project_id="test-proj-uuid",
                cdp_url="http://127.0.0.1:9222",
                android_manager=manager,
            )
            health = provider.health()
            assert health.healthy is True
            assert health.details.get("android_cdp") is True
            assert health.details.get("foreground_policy") == "auto"

    def test_generate_video_uses_scoped_foreground(self):
        manager = AndroidCDPManager(foreground_policy=ForegroundPolicy.MICRO_FOREGROUND)
        mock_job = MagicMock()
        mock_job.media_name = "test_media_123"
        mock_job.workflow_id = "test_wf_123"
        mock_job.project_id = "test-proj-uuid"

        mock_flow_client = MagicMock()
        mock_flow_client._api.get_credits.return_value = MagicMock(credits=100)
        mock_flow_client.generate_video.return_value = [mock_job]

        with patch.object(manager, "scoped_foreground_for_submit") as mock_scoped, \
             patch.object(ProductionFlowProvider, "_import_flow") as mock_import, \
             patch.object(ProductionFlowProvider, "_get_client", return_value=mock_flow_client), \
             patch.object(ProductionFlowProvider, "_run") as mock_run:
            
            # Setup mock_import
            mock_exc = MagicMock()
            mock_import.return_value = (MagicMock(), mock_exc, MagicMock())
            mock_run.side_effect = [MagicMock(credits=100), [mock_job]]

            provider = ProductionFlowProvider(
                project_id="test-proj-uuid",
                cdp_url="http://127.0.0.1:9222",
                android_manager=manager,
            )

            req = FlowGenerationRequest(
                job_id="job-1",
                prompt="test prompt",
                model=FlowModel.VEO_3_1_FAST,
                aspect_ratio=FlowAspectRatio.PORTRAIT_9_16,
                count=1,
            )

            res = provider.generate_video(req)
            assert res.status == FlowJobStatus.SUBMITTED
            assert res.provider_job_id == "test_media_123"
            mock_scoped.assert_called_once()

    def test_health_fails_closed_when_cdp_forward_fails(self):
        manager = AndroidCDPManager(foreground_policy=ForegroundPolicy.AUTO)
        with patch.object(manager, "ensure_cdp_forward", return_value=False):
            provider = ProductionFlowProvider(
                project_id="test-proj-uuid",
                cdp_url="http://127.0.0.1:9222",
                android_manager=manager,
            )
            health = provider.health()
            assert health.healthy is False
            assert health.browser_ready is False
            assert health.authenticated is False
            assert "android_cdp_forward_failed" in health.details.get("reason", "")


class TestCDPTransports:
    """Unit tests for the modular CDPTransport implementations."""

    def test_direct_socket_transport_probe_fails_gracefully_on_error(self):
        transport = DirectSocketTransport(cdp_port=9222)
        with patch("socket.socket") as mock_sock_cls:
            mock_sock = MagicMock()
            mock_sock.__enter__.return_value = mock_sock
            mock_sock.connect.side_effect = PermissionError("SELinux denied")
            mock_sock_cls.return_value = mock_sock

            assert transport.probe() is False
            assert transport.health()["status"] == "unsupported"
            assert "SELinux" in transport.health().get("reason", "")

    def test_direct_socket_transport_probe_succeeds(self):
        transport = DirectSocketTransport(cdp_port=9222)
        with patch("socket.socket") as mock_sock_cls:
            mock_sock = MagicMock()
            mock_sock.__enter__.return_value = mock_sock
            mock_sock.connect.return_value = None
            mock_sock_cls.return_value = mock_sock

            assert transport.probe() is True
            assert transport.health()["status"] == "available"

    def test_local_adb_tcp_transport_probe_succeeds_when_connected(self):
        transport = LocalAdbTcpTransport(tcp_port=5555, cdp_port=9222)
        mock_device = MagicMock(serial="127.0.0.1:5555")
        with patch.object(transport, "_get_device", return_value=mock_device):
            assert transport.probe() is True
            assert transport.health()["status"] == "available"

    def test_local_adb_tcp_transport_probe_fails_when_no_device(self):
        """Finding C: probe() must be side-effect-free and NOT call _attempt_connect()."""
        transport = LocalAdbTcpTransport(tcp_port=5555, cdp_port=9222)
        with patch.object(transport, "_get_device", return_value=None), \
             patch.object(transport, "_attempt_connect") as mock_connect:
            assert transport.probe() is False
            assert transport.health()["status"] == "disconnected"
            mock_connect.assert_not_called()

    def test_local_adb_tcp_transport_ensure_attempts_connect_when_needed(self):
        """Finding C: ensure() is permitted to establish connection via _attempt_connect()."""
        transport = LocalAdbTcpTransport(tcp_port=5555, cdp_port=9222)
        mock_dev = MagicMock(serial="127.0.0.1:5555")
        mock_dev.forward_list.return_value = []
        with patch.object(transport, "_get_device", side_effect=[None, mock_dev]), \
             patch.object(transport, "_attempt_connect", return_value=True) as mock_connect, \
             patch("auto_video_factory.flow_provider.android.discover_device_devtools_socket", return_value="chrome_devtools_remote"), \
             patch("auto_video_factory.flow_provider.android.verify_cdp_endpoint", return_value=True):
            assert transport.ensure() is True
            mock_connect.assert_called_once()
            mock_dev.forward.assert_called_once_with("tcp:9222", "localabstract:chrome_devtools_remote")

    def test_pid_suffixed_socket_forward_in_transports(self):
        """Finding B: Transports must dynamically discover and forward PID-suffixed sockets (e.g. chrome_devtools_remote_1234)."""
        transport = WirelessAdbTransport(cdp_port=9222)
        mock_device = MagicMock(serial="192.168.1.57:37453")
        mock_device.forward_list.return_value = []
        with patch.object(transport, "_get_device", return_value=mock_device), \
             patch("auto_video_factory.flow_provider.android.discover_device_devtools_socket", return_value="chrome_devtools_remote_1234"), \
             patch("auto_video_factory.flow_provider.android.verify_cdp_endpoint", return_value=True):
            assert transport.ensure() is True
            mock_device.forward.assert_called_once_with(
                "tcp:9222", "localabstract:chrome_devtools_remote_1234"
            )

    def test_stale_forward_replacement(self):
        """Finding B: If existing forward points to a stale socket, ensure() must replace it with the discovered socket."""
        transport = LocalAdbTcpTransport(tcp_port=5555, cdp_port=9222)
        mock_dev = MagicMock(serial="127.0.0.1:5555")
        # Stale forward pointing to old socket
        stale_forward = SimpleNamespace(local="tcp:9222", remote="localabstract:chrome_devtools_remote_9999")
        mock_dev.forward_list.return_value = [stale_forward]

        with patch.object(transport, "_get_device", return_value=mock_dev), \
             patch("auto_video_factory.flow_provider.android.discover_device_devtools_socket", return_value="chrome_devtools_remote_1234"), \
             patch("auto_video_factory.flow_provider.android.verify_cdp_endpoint", return_value=True):
            assert transport.ensure() is True
            # Must replace with new socket
            mock_dev.forward.assert_called_once_with(
                "tcp:9222", "localabstract:chrome_devtools_remote_1234"
            )

    def test_wireless_multi_device_ambiguity_fails_closed(self):
        """Finding D: Wireless transport with multiple eligible devices and no serial must fail closed."""
        transport = WirelessAdbTransport(cdp_port=9222)
        dev1 = MagicMock(serial="192.168.1.50:5555")
        dev2 = MagicMock(serial="192.168.1.51:5555")
        mock_client = MagicMock()
        mock_client.device_list.return_value = [dev1, dev2]

        with patch.object(transport, "_get_client", return_value=mock_client):
            assert transport._get_device() is None
            assert transport.probe() is False

    def test_local_adb_tcp_transport_switch_to_tcp_mode(self):
        transport = LocalAdbTcpTransport(tcp_port=5555, cdp_port=9222)
        mock_source_device = MagicMock()
        mock_source_device.tcpip.return_value = "restarting in TCP mode port: 5555"

        result = transport.enable_tcp_mode_from_device(mock_source_device)
        assert result is True
        mock_source_device.tcpip.assert_called_once_with(5555)

    def test_wireless_adb_transport_probe_succeeds(self):
        transport = WirelessAdbTransport(cdp_port=9222)
        mock_device = MagicMock(serial="192.168.1.57:37453")
        mock_client = MagicMock()
        mock_client.device_list.return_value = [mock_device]

        with patch.object(transport, "_get_client", return_value=mock_client):
            assert transport.probe() is True
            assert transport.health()["status"] == "available"

    def test_wireless_adb_transport_probe_skips_localhost_tcp(self):
        # Wireless transport skips 127.0.0.1 (which belongs to LocalAdbTcpTransport)
        transport = WirelessAdbTransport(cdp_port=9222)
        mock_device = MagicMock(serial="127.0.0.1:5555")
        mock_client = MagicMock()
        mock_client.device_list.return_value = [mock_device]

        with patch.object(transport, "_get_client", return_value=mock_client):
            assert transport.probe() is False


class TestTransportRouter:
    """Unit tests for AndroidCDPManager transport routing and deterministic selection."""

    def test_manager_selects_direct_socket_first_if_available(self):
        manager = AndroidCDPManager()
        mock_direct = MagicMock(spec=DirectSocketTransport)
        mock_direct.probe.return_value = True
        mock_direct.mode = CDPTransportMode.DIRECT_LOCAL_SOCKET

        mock_local_adb = MagicMock(spec=LocalAdbTcpTransport)
        mock_local_adb.probe.return_value = True

        with patch.object(manager, "_get_direct_transport", return_value=mock_direct), \
             patch.object(manager, "_get_local_tcp_transport", return_value=mock_local_adb):
            selected = manager.select_transport()
            assert selected == mock_direct
            assert manager.get_active_transport_mode() == CDPTransportMode.DIRECT_LOCAL_SOCKET

    def test_manager_falls_back_to_local_adb_tcp(self):
        manager = AndroidCDPManager()
        mock_direct = MagicMock(spec=DirectSocketTransport)
        mock_direct.probe.return_value = False

        mock_local_adb = MagicMock(spec=LocalAdbTcpTransport)
        mock_local_adb.probe.return_value = True
        mock_local_adb.mode = CDPTransportMode.LOCAL_ADB_TCP

        with patch.object(manager, "_get_direct_transport", return_value=mock_direct), \
             patch.object(manager, "_get_local_tcp_transport", return_value=mock_local_adb):
            selected = manager.select_transport()
            assert selected == mock_local_adb
            assert manager.get_active_transport_mode() == CDPTransportMode.LOCAL_ADB_TCP

    def test_manager_falls_back_to_wireless_adb(self):
        manager = AndroidCDPManager()
        mock_direct = MagicMock(spec=DirectSocketTransport)
        mock_direct.probe.return_value = False

        mock_local_adb = MagicMock(spec=LocalAdbTcpTransport)
        mock_local_adb.probe.return_value = False

        mock_wireless = MagicMock(spec=WirelessAdbTransport)
        mock_wireless.probe.return_value = True
        mock_wireless.mode = CDPTransportMode.WIRELESS_ADB

        with patch.object(manager, "_get_direct_transport", return_value=mock_direct), \
             patch.object(manager, "_get_local_tcp_transport", return_value=mock_local_adb), \
             patch.object(manager, "_get_wireless_transport", return_value=mock_wireless):
            selected = manager.select_transport()
            assert selected == mock_wireless
            assert manager.get_active_transport_mode() == CDPTransportMode.WIRELESS_ADB

    def test_manager_returns_user_interaction_required_when_all_fail(self):
        manager = AndroidCDPManager()
        mock_direct = MagicMock(spec=DirectSocketTransport)
        mock_direct.probe.return_value = False

        mock_local_adb = MagicMock(spec=LocalAdbTcpTransport)
        mock_local_adb.probe.return_value = False

        mock_wireless = MagicMock(spec=WirelessAdbTransport)
        mock_wireless.probe.return_value = False

        with patch.object(manager, "_get_direct_transport", return_value=mock_direct), \
             patch.object(manager, "_get_local_tcp_transport", return_value=mock_local_adb), \
             patch.object(manager, "_get_wireless_transport", return_value=mock_wireless):
            selected = manager.select_transport()
            assert selected is None
            assert manager.get_active_transport_mode() == CDPTransportMode.USER_INTERACTION_REQUIRED


class TestAndroidChromeProductionBackend:
    """Targeted tests for Android Chrome CDP production backend requirements."""

    def test_reuse_existing_pairing(self):
        """TEST_REUSE_EXISTING_PAIRING: No pairing request when paired device is still valid."""
        transport = WirelessAdbTransport(cdp_port=9222)
        mock_client = MagicMock()
        mock_device = MagicMock(serial="192.168.1.137:38555")
        mock_client.device_list.return_value = [mock_device]

        with patch.object(transport, "_get_client", return_value=mock_client):
            dev = transport._get_device()
            assert dev == mock_device
            mock_client.connect.assert_not_called()

    def test_connect_port_changed_auto_rediscovered(self):
        """TEST_CONNECT_PORT_CHANGED: Old connect endpoint stale. mDNS returns new endpoint -> auto-connected."""
        transport = WirelessAdbTransport(cdp_port=9222)
        mock_client = MagicMock()
        mock_client.device_list.return_value = []
        mock_new_device = MagicMock(serial="192.168.1.137:45678")
        mock_client.connect.return_value = "connected to 192.168.1.137:45678"
        mock_client.device.return_value = mock_new_device

        with patch.object(transport, "_get_client", return_value=mock_client), \
             patch("auto_video_factory.flow_provider.android.discover_mdns_wireless_endpoints", return_value=["192.168.1.137:45678"]):
            dev = transport._get_device()
            assert dev == mock_new_device
            mock_client.connect.assert_called_once_with("192.168.1.137:45678")

    def test_pairing_port_not_used_for_connect(self):
        """TEST_PAIRING_PORT_NOT_USED_FOR_CONNECT: Ensure pairing port is never treated as connect port."""
        from auto_video_factory.flow_provider.android import discover_mdns_wireless_endpoints
        # Pairing port broadcasts _adb-tls-pairing._tcp, connect broadcasts _adb-tls-connect._tcp
        mock_output = (
            "adb-10AC8A299S000GL-9I791Q\t_adb-tls-pairing._tcp\t192.168.1.137:39353\n"
            "adb-10AC8A299S000GL-9I791Q\t_adb-tls-connect._tcp\t192.168.1.137:38555\n"
        )
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=mock_output)
            discovered = discover_mdns_wireless_endpoints()
            assert "192.168.1.137:38555" in discovered
            assert "192.168.1.137:39353" not in discovered

    def test_dynamic_phone_ip(self):
        """TEST_DYNAMIC_PHONE_IP: Phone LAN IP changes -> rediscovery works dynamically."""
        transport = WirelessAdbTransport(cdp_port=9222)
        mock_client = MagicMock()
        mock_client.device_list.return_value = []
        mock_new_device = MagicMock(serial="10.0.0.42:37777")
        mock_client.connect.return_value = "connected to 10.0.0.42:37777"
        mock_client.device.return_value = mock_new_device

        with patch.object(transport, "_get_client", return_value=mock_client), \
             patch("auto_video_factory.flow_provider.android.discover_mdns_wireless_endpoints", return_value=["10.0.0.42:37777"]):
            dev = transport._get_device()
            assert dev == mock_new_device

    def test_pid_suffixed_socket(self):
        """TEST_PID_SUFFIXED_SOCKET: chrome_devtools_remote_1234 is forwarded correctly."""
        from auto_video_factory.flow_provider.android import discover_device_devtools_socket
        mock_dev = MagicMock()
        mock_dev.shell.return_value = (
            "0000000000000000: 00000002 00000000 00010000 0001 01 99999 @chrome_devtools_remote_5678\n"
        )
        socket_name = discover_device_devtools_socket(mock_dev)
        assert socket_name == "chrome_devtools_remote_5678"

    def test_stale_forward(self):
        """TEST_STALE_FORWARD: Existing local port pointing to wrong socket/device is repaired safely."""
        transport = WirelessAdbTransport(cdp_port=9222)
        mock_dev = MagicMock(serial="192.168.1.137:38555")
        mock_dev.shell.return_value = "00000000: 00000002 00000000 00010000 0001 01 12345 @chrome_devtools_remote_999\n"
        
        # Simulate an old forward to a wrong socket
        stale_item = SimpleNamespace(local="tcp:9222", remote="localabstract:chrome_devtools_remote_old")
        mock_dev.forward_list.return_value = [stale_item]

        with patch.object(transport, "_get_device", return_value=mock_dev), \
             patch("auto_video_factory.flow_provider.android.verify_cdp_endpoint", return_value=True):
            ok = transport.ensure()
            assert ok is True
            mock_dev.forward_remove.assert_called_once_with("tcp:9222")
            mock_dev.forward.assert_called_once_with("tcp:9222", "localabstract:chrome_devtools_remote_999")

    def test_android_chrome_auth_session(self):
        """TEST_ANDROID_CHROME_AUTH_SESSION: ProductionFlowProvider detects authenticated Android Chrome CDP."""
        mock_mgr = MagicMock(spec=AndroidCDPManager)
        mock_mgr.ensure_cdp_forward.return_value = True
        mock_mgr.is_adb_connected.return_value = True
        mock_mgr.cdp_port = 9222

        provider = ProductionFlowProvider(
            project_id="test-proj-uuid",
            cdp_url="http://127.0.0.1:9222",
            android_manager=mock_mgr,
            backend="android_chrome",
        )

        with patch("auto_video_factory.flow_provider.provider.check_cdp_endpoint_detailed") as mock_check:
            mock_check.return_value = SimpleNamespace(ready=True, error=None)
            status = provider.health()
            assert status.healthy is True
            assert status.authenticated is True
            assert status.details.get("backend") == "android_chrome"
            assert status.details.get("cdp_owner") == "ANDROID_CHROME_CDP"

    def test_reattach(self):
        """TEST_REATTACH: Detach and reattach preserves session and manager."""
        mock_mgr = MagicMock(spec=AndroidCDPManager)
        mock_mgr.ensure_cdp_forward.return_value = True
        mock_mgr.is_adb_connected.return_value = True
        mock_mgr.cdp_port = 9222

        provider = ProductionFlowProvider(
            project_id="test-proj-uuid",
            cdp_url="http://127.0.0.1:9222",
            android_manager=mock_mgr,
            backend="android_chrome",
        )

        with patch("auto_video_factory.flow_provider.provider.check_cdp_endpoint_detailed") as mock_check:
            mock_check.return_value = SimpleNamespace(ready=True, error=None)
            s1 = provider.health()
            assert s1.healthy is True
            provider.close()
            s2 = provider.health()
            assert s2.healthy is True

    def test_pairing_lost(self):
        """TEST_PAIRING_LOST: Lost pairing fails closed with USER_INTERACTION_REQUIRED=ANDROID_WIRELESS_DEBUG_PAIRING."""
        mock_mgr = MagicMock(spec=AndroidCDPManager)
        mock_mgr.ensure_cdp_forward.return_value = False
        mock_mgr.is_adb_connected.return_value = False
        mock_mgr.cdp_port = 9222

        provider = ProductionFlowProvider(
            project_id="test-proj-uuid",
            cdp_url="http://127.0.0.1:9222",
            android_manager=mock_mgr,
            backend="android_chrome",
        )

        status = provider.health()
        assert status.healthy is False
        assert status.authenticated is False
        assert status.details.get("user_interaction_required") == "ANDROID_WIRELESS_DEBUG_PAIRING"

    def test_native_fallback(self):
        """TEST_NATIVE_FALLBACK: Android Chrome backend unavailable -> native fallback routes deterministically."""
        provider_native = ProductionFlowProvider(
            project_id="test-proj-uuid",
            cdp_url="http://127.0.0.1:9222",
            backend="native",
        )
        assert provider_native._backend == "native"
        assert provider_native._android_manager is None

        with patch("auto_video_factory.flow_provider.provider.check_cdp_endpoint_detailed") as mock_check:
            mock_check.return_value = SimpleNamespace(ready=True, error=None)
            status = provider_native.health()
            assert status.details.get("cdp_owner") == "NATIVE_TERMUX_CHROMIUM"

    def test_no_manual_daily_values(self):
        """TEST_NO_MANUAL_DAILY_VALUES: Daily path operates automatically with 0 manual IP/port/code inputs."""
        transport = WirelessAdbTransport(cdp_port=9222)
        mock_client = MagicMock()
        mock_client.device_list.return_value = []
        mock_device = MagicMock(serial="192.168.1.137:38555")
        mock_client.connect.return_value = "connected to 192.168.1.137:38555"
        mock_client.device.return_value = mock_device

        with patch.object(transport, "_get_client", return_value=mock_client), \
             patch("auto_video_factory.flow_provider.android.discover_mdns_wireless_endpoints", return_value=["192.168.1.137:38555"]):
            dev = transport._get_device()
            assert dev == mock_device
            assert transport.serial is None  # Never required hard-coded serial



