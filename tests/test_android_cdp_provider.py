"""
Tests for Android ADB, Chrome DevTools Protocol manager via adbutils, and ProductionFlowProvider integration.
"""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import pytest

from auto_video_factory.flow_provider.android import (
    AndroidCDPManager,
    ForegroundPolicy,
    SYSTEM_PACKAGES,
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

    def test_get_device_multiple_devices_selects_first_with_warning(self):
        manager = AndroidCDPManager()
        mock_dev1 = MagicMock(serial="192.168.1.57:37453")
        mock_dev2 = MagicMock(serial="emulator-5554")
        mock_client = MagicMock()
        mock_client.device_list.return_value = [mock_dev1, mock_dev2]

        with patch.object(manager, "_get_client", return_value=mock_client):
            device = manager.get_device()
            assert device == mock_dev1

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

        with patch.object(manager, "get_device", return_value=mock_device), \
             patch.object(manager, "discover_chrome_devtools_socket", return_value="chrome_devtools_remote"):
            assert manager.ensure_cdp_forward() is True
            mock_device.forward.assert_not_called()

    def test_ensure_cdp_forward_new(self):
        manager = AndroidCDPManager(cdp_port=9222)
        mock_device = MagicMock()
        mock_device.forward_list.return_value = []

        with patch.object(manager, "get_device", return_value=mock_device), \
             patch.object(manager, "discover_chrome_devtools_socket", return_value="chrome_devtools_remote"):
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

