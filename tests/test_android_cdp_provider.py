"""
Tests for Android ADB, Chrome DevTools Protocol manager, and ProductionFlowProvider integration.
"""
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

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
    """Unit tests for AndroidCDPManager."""

    def test_find_adb_binary(self, monkeypatch):
        monkeypatch.setenv("ADB_PATH", "/custom/bin/adb")
        with patch("os.path.isfile", return_value=True), patch("os.access", return_value=True):
            manager = AndroidCDPManager()
            assert manager.adb_path == "/custom/bin/adb"

    def test_is_adb_connected_true(self):
        manager = AndroidCDPManager(adb_path="adb")
        mock_output = "List of devices attached\n192.168.1.57:37453\tdevice product:V2206T model:V2206\n"
        with patch.object(manager, "run_adb", return_value=(0, mock_output, "")):
            assert manager.is_adb_connected() is True

    def test_is_adb_connected_false(self):
        manager = AndroidCDPManager(adb_path="adb")
        mock_output = "List of devices attached\n\n"
        with patch.object(manager, "run_adb", return_value=(0, mock_output, "")):
            assert manager.is_adb_connected() is False

    def test_discover_chrome_devtools_socket(self):
        manager = AndroidCDPManager(adb_path="adb")
        mock_unix = (
            "0000000000000000: 00000002 00000000 00010000 0001 01 67495864 @chrome_devtools_remote\n"
            "0000000000000000: 00000002 00000000 00010000 0001 01 67495865 /dev/socket/adbd\n"
        )
        with patch.object(manager, "run_adb", return_value=(0, mock_unix, "")):
            socket_name = manager.discover_chrome_devtools_socket()
            assert socket_name == "chrome_devtools_remote"

    def test_ensure_cdp_forward_existing(self):
        manager = AndroidCDPManager(adb_path="adb", cdp_port=9222)
        mock_list = "192.168.1.57:37453 tcp:9222 localabstract:chrome_devtools_remote\n"
        with patch.object(manager, "is_adb_connected", return_value=True), \
             patch.object(manager, "run_adb", return_value=(0, mock_list, "")):
            assert manager.ensure_cdp_forward() is True

    def test_ensure_cdp_forward_new(self):
        manager = AndroidCDPManager(adb_path="adb", cdp_port=9222)
        with patch.object(manager, "is_adb_connected", return_value=True), \
             patch.object(manager, "discover_chrome_devtools_socket", return_value="chrome_devtools_remote"), \
             patch.object(manager, "run_adb") as mock_run:
            # First call for forward --list returns empty, second call for forward tcp:9222 returns success
            mock_run.side_effect = [
                (0, "", ""),
                (0, "9222", ""),
            ]
            assert manager.ensure_cdp_forward() is True
            assert mock_run.call_count == 2

    def test_get_current_foreground_app_window(self):
        manager = AndroidCDPManager(adb_path="adb")
        mock_dumpsys = "  mCurrentFocus=Window{d4f6be9 u0 com.openai.chatgpt/com.openai.chatgpt.MainActivity type=1}\n"
        with patch.object(manager, "run_adb", return_value=(0, mock_dumpsys, "")):
            pkg, act = manager.get_current_foreground_app()
            assert pkg == "com.openai.chatgpt"
            assert act == "com.openai.chatgpt.MainActivity"

    def test_get_current_foreground_app_activity_fallback(self):
        manager = AndroidCDPManager(adb_path="adb")
        mock_dumpsys_act = "  mFocusedApp=ActivityRecord{db52695 u0 com.netflix.mediaclient/.ui.launch.UIWebViewActivity t19 d0}\n"
        with patch.object(manager, "run_adb") as mock_run:
            mock_run.side_effect = [
                (0, "", ""),  # dumpsys window empty
                (0, mock_dumpsys_act, ""),  # dumpsys activity activities
            ]
            pkg, act = manager.get_current_foreground_app()
            assert pkg == "com.netflix.mediaclient"
            assert act == "com.netflix.mediaclient.ui.launch.UIWebViewActivity"

    def test_restore_foreground_app_skips_chrome_and_system(self):
        manager = AndroidCDPManager(adb_path="adb")
        with patch.object(manager, "run_adb") as mock_run:
            # Skipping Chrome
            assert manager.restore_foreground_app("com.android.chrome", "Main") is True
            # Skipping system launcher
            assert manager.restore_foreground_app("com.android.launcher3", "Launcher") is True
            assert mock_run.call_count == 0

    def test_restore_foreground_app_calls_am_start(self):
        manager = AndroidCDPManager(adb_path="adb")
        with patch.object(manager, "run_adb", return_value=(0, "Starting...", "")) as mock_run:
            assert manager.restore_foreground_app("com.openai.chatgpt", "com.openai.chatgpt.MainActivity") is True
            mock_run.assert_called_once()
            args = mock_run.call_args[0]
            assert "com.openai.chatgpt/com.openai.chatgpt.MainActivity" in args

    def test_scoped_foreground_background_policy(self):
        manager = AndroidCDPManager(adb_path="adb", foreground_policy=ForegroundPolicy.BACKGROUND)
        with patch.object(manager, "bring_chrome_to_foreground") as mock_bring, \
             patch.object(manager, "restore_foreground_app") as mock_restore:
            with manager.scoped_foreground_for_submit():
                pass
            mock_bring.assert_not_called()
            mock_restore.assert_not_called()

    def test_scoped_foreground_micro_foreground_restores_on_success(self):
        manager = AndroidCDPManager(adb_path="adb", foreground_policy=ForegroundPolicy.MICRO_FOREGROUND)
        with patch.object(manager, "get_current_foreground_app", return_value=("com.openai.chatgpt", "MainActivity")), \
             patch.object(manager, "bring_chrome_to_foreground") as mock_bring, \
             patch.object(manager, "restore_foreground_app") as mock_restore:
            with manager.scoped_foreground_for_submit():
                mock_bring.assert_called_once()
            mock_restore.assert_called_once_with("com.openai.chatgpt", "MainActivity")

    def test_scoped_foreground_micro_foreground_restores_on_exception(self):
        manager = AndroidCDPManager(adb_path="adb", foreground_policy=ForegroundPolicy.MICRO_FOREGROUND)
        with patch.object(manager, "get_current_foreground_app", return_value=("com.openai.chatgpt", "MainActivity")), \
             patch.object(manager, "bring_chrome_to_foreground"), \
             patch.object(manager, "restore_foreground_app") as mock_restore:
            with pytest.raises(RuntimeError):
                with manager.scoped_foreground_for_submit():
                    raise RuntimeError("Submit error")
            mock_restore.assert_called_once_with("com.openai.chatgpt", "MainActivity")


class TestProductionFlowProviderAndroidCDP:
    """Unit tests for ProductionFlowProvider with Android CDP integration."""

    def test_health_reports_android_cdp(self, monkeypatch):
        manager = AndroidCDPManager(adb_path="adb", foreground_policy=ForegroundPolicy.AUTO)
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

    def test_generate_video_uses_scoped_foreground(self, monkeypatch):
        manager = AndroidCDPManager(adb_path="adb", foreground_policy=ForegroundPolicy.MICRO_FOREGROUND)
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
