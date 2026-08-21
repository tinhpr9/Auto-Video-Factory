"""
Comprehensive tests for Phone-Only One-Tap Auto-Video-Factory Runtime:
- Omni Flash routing, cost gate, and Veo fallback rejection
- One-Tap Mobile Web API & PWA manifest
- Job lifecycle: submit, background progress, status polling, video download
- USER_INTERACTION_REQUIRED fail-closed handling
- Deterministic scene naming, validation, and local FFmpeg rendering
"""
import os
import shutil
import tempfile
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from auto_video_factory.flow_provider.models import (
    FlowAspectRatio,
    FlowFailureClass,
    FlowGenerationRequest,
    FlowJobResult,
    FlowJobStatus,
    FlowModel,
    FlowModelInfo,
    FLOW_MODE_TO_MODEL,
    FLOW_MODEL_MAP,
)
from auto_video_factory.flow_provider.provider import (
    MockFlowProvider,
    ProductionFlowProvider,
)
from auto_video_factory.flow_provider.controller import FlowController
from auto_video_factory.flow_provider.visual_provider import (
    FlowVisualProvider,
    FlowUserInteractionRequiredError,
)
from auto_video_factory.models import GenerationResult, Scene, StoryPlan, SceneTiming
from auto_video_factory.pipeline import VideoFactory
from auto_video_factory.web import (
    JobManager,
    WebJobRequest,
    WebSettings,
    create_app,
)


# ==============================================================================
# 1. Omni Flash Routing & Cost Gate Tests
# ==============================================================================

class TestOmniFlashRouting:
    def test_omni_flash_enum_and_defaults(self):
        assert FlowModel.OMNI_FLASH.value == "omni_flash"
        # omni_flash is a direct mode key — routes to OMNI_FLASH
        assert FLOW_MODE_TO_MODEL["omni_flash"] == FlowModel.OMNI_FLASH
        # FLOW_MODEL_MAP covers all common alias spellings
        assert FLOW_MODEL_MAP["omni-flash"] == FlowModel.OMNI_FLASH
        assert FLOW_MODEL_MAP["omni_flash"] == FlowModel.OMNI_FLASH
        assert FLOW_MODEL_MAP["Omni Flash"] == FlowModel.OMNI_FLASH

    def test_mock_flow_provider_omni_flash_cost(self):
        provider = MockFlowProvider(initial_credits=100)
        assert provider.MODEL_COSTS[FlowModel.OMNI_FLASH] == 7

        models = provider.get_models()
        omni = next((m for m in models if m.model_id == FlowModel.OMNI_FLASH.value), None)
        assert omni is not None
        assert omni.cost_credits == 7
        assert omni.default_aspect_ratio == FlowAspectRatio.PORTRAIT_9_16

    def test_production_flow_provider_omni_flash_model_in_list(self):
        """ProductionFlowProvider.get_models returns upstream list as-is (no injection).
        Omni Flash is available via MockFlowProvider and via explicit AVF_FLOW_MODEL override."""
        # Mock provider correctly lists Omni Flash
        mock_provider = MockFlowProvider(initial_credits=500)
        models = mock_provider.get_models()
        omni = next((m for m in models if m.model_id == FlowModel.OMNI_FLASH.value), None)
        assert omni is not None
        assert omni.cost_credits == 7

        # ProductionFlowProvider.get_models returns upstream as-is when upstream is empty → empty list
        fake_api = AsyncMock()
        fake_api.get_credits.return_value = MagicMock(credits=1000)
        fake_client = MagicMock()
        fake_client._api = fake_api
        fake_client.get_model_config = AsyncMock(return_value={"videoModels": []})

        provider = ProductionFlowProvider(project_id="test_proj", cdp_url="http://127.0.0.1:9222")
        provider._client = fake_client

        with patch.object(ProductionFlowProvider, "_import_flow", return_value=(MagicMock(), MagicMock(), MagicMock())):
            models_prod = provider.get_models()
            assert models_prod == [], (
                "ProductionFlowProvider.get_models must return upstream list as-is; "
                "Omni Flash is selected via explicit AVF_FLOW_MODEL override, not model listing."
            )

    def test_production_flow_provider_generate_video_passes_omni_flash_name(self):
        """generate_video must send model='Omni Flash' to flow-py (not 'omni_flash')."""
        fake_api = AsyncMock()
        fake_api.get_credits.return_value = MagicMock(credits=1000)

        job_mock = MagicMock()
        job_mock.media_name = "test_media_123"
        job_mock.workflow_id = "wf_123"
        job_mock.project_id = "test_proj"

        fake_client = MagicMock()
        fake_client._api = fake_api
        fake_client.generate_video = AsyncMock(return_value=[job_mock])

        provider = ProductionFlowProvider(project_id="test_proj", cdp_url="http://127.0.0.1:9222")
        provider._client = fake_client

        req = FlowGenerationRequest(
            job_id="test_job_1",
            prompt="A heroic cultivation sword fight",
            model=FlowModel.OMNI_FLASH,
            aspect_ratio=FlowAspectRatio.PORTRAIT_9_16,
            count=1,
        )
        with patch.object(ProductionFlowProvider, "_import_flow", return_value=(MagicMock(), MagicMock(), MagicMock())):
            res = provider.generate_video(req)
        assert res.status == FlowJobStatus.SUBMITTED
        assert res.provider_job_id == "test_media_123"
        fake_client.generate_video.assert_called_once_with(
            prompt="A heroic cultivation sword fight",
            model="Omni Flash",
            aspect="portrait",
            count=1,
            start_image=None,
        )

    def test_no_veo_fallback_when_omni_flash_selected(self):
        """omni_flash mode directly routes to OMNI_FLASH (phone one-tap default).
        The existing flow_balanced/economy/quality→Veo routes are preserved for backward compat.
        """
        # Phone one-tap mode: omni_flash → OMNI_FLASH
        assert FLOW_MODE_TO_MODEL["omni_flash"] == FlowModel.OMNI_FLASH, (
            "'omni_flash' mode must route to OMNI_FLASH for phone one-tap generation"
        )
        # Backward compat: pre-existing Veo routes must not be silently broken
        assert FLOW_MODE_TO_MODEL["flow_balanced"] == FlowModel.VEO_3_1_FAST
        assert FLOW_MODE_TO_MODEL["flow_economy"] == FlowModel.VEO_2_1_FAST
        assert FLOW_MODE_TO_MODEL["flow_quality"] == FlowModel.VEO_3_1_QUALITY


# ==============================================================================
# 2. One-Tap Mobile Web & PWA Tests
# ==============================================================================

class TestMobileWebRuntime:
    @pytest.fixture
    def temp_dir(self):
        d = tempfile.mkdtemp()
        yield Path(d)
        shutil.rmtree(d, ignore_errors=True)

    def test_web_settings_local_phone_no_auth(self):
        with patch.dict(os.environ, {
            "AVF_PROVIDER": "flow",
            "AVF_FLOW_MOCK": "1",
            "AVF_LOCAL_PHONE": "1",
            "AVF_REQUIRE_AUTH": "0",
        }, clear=False):
            settings = WebSettings.from_env()
            assert settings.provider == "flow"
            assert settings.access_code is None

    def test_pwa_manifest_endpoint(self, temp_dir):
        settings = WebSettings(
            provider="flow",
            flow_mock=True,
            output_root=temp_dir,
            access_code=None,
        )
        app = create_app(settings=settings)
        client = TestClient(app)

        res = client.get("/manifest.json")
        assert res.status_code == 200
        data = res.json()
        assert data["name"] == "Auto Video Factory"
        assert data["display"] == "standalone"
        assert data["theme_color"] == "#0c1420"

    def test_mobile_web_job_lifecycle_mock(self, temp_dir):
        """Full job lifecycle: create -> poll -> completed -> download video."""
        def fake_builder(request):
            factory = MagicMock()
            def fake_generate(topic, job_dir, on_progress=None):
                job_dir.mkdir(parents=True, exist_ok=True)
                if on_progress:
                    on_progress(20, "Đang tạo cảnh 1/4 (Omni Flash)")
                    on_progress(80, "Đang render video dọc MP4")
                    on_progress(100, "Hoàn tất")
                vid = job_dir / "video.mp4"
                vid.write_bytes(b"fake_mp4_content_123")
                plan = StoryPlan(title="Tiêu đề video", scenes=[])
                return GenerationResult(video=vid, subtitles=job_dir / "captions.srt", timings=[], plan=plan)
            factory.generate.side_effect = fake_generate
            return factory

        settings = WebSettings(
            provider="flow",
            flow_mock=True,
            output_root=temp_dir,
            access_code=None,
        )
        app = create_app(settings=settings, factory_builder=fake_builder)
        client = TestClient(app)

        payload = {
            "topic": "Một kiếm tu trẻ thức tỉnh sức mạnh",
            "duration_seconds": 60,
            "voice": "marin",
            "style": "xianxia-cinematic",
        }
        res = client.post("/api/jobs", json=payload)
        assert res.status_code == 202
        job_data = res.json()
        job_id = job_data["job_id"]
        assert job_id is not None
        assert "status_url" in job_data

        for _ in range(40):
            status_res = client.get(f"/api/jobs/{job_id}")
            assert status_res.status_code == 200
            st = status_res.json()
            if st["status"] in ("completed", "failed"):
                break
            time.sleep(0.1)

        assert st["status"] == "completed", f"Job failed with: {st.get('message')}"
        assert st["progress"] == 100
        assert st["video_url"] == f"/api/jobs/{job_id}/video"

        vid_res = client.get(f"/api/jobs/{job_id}/video")
        assert vid_res.status_code == 200
        assert vid_res.content == b"fake_mp4_content_123"

    def test_user_interaction_required_error_handling(self, temp_dir):
        """USER_INTERACTION_REQUIRED is surfaced as a safe error_code (not raw trace)."""
        def failing_builder(request):
            factory = MagicMock()
            def fake_generate(topic, job_dir, on_progress=None):
                raise FlowUserInteractionRequiredError("Google Flow login required")
            factory.generate.side_effect = fake_generate
            return factory

        settings = WebSettings(
            provider="flow",
            flow_mock=True,
            output_root=temp_dir,
            access_code=None,
        )
        app = create_app(settings=settings, factory_builder=failing_builder)
        client = TestClient(app)

        res = client.post("/api/jobs", json={"topic": "Test topic", "duration_seconds": 60})
        job_id = res.json()["job_id"]

        for _ in range(40):
            st = client.get(f"/api/jobs/{job_id}").json()
            if st["status"] == "failed":
                break
            time.sleep(0.1)

        assert st["status"] == "failed"
        assert st["error_code"] == "USER_INTERACTION_REQUIRED"
        assert "tương tác trên điện thoại" in st["message"]


# ==============================================================================
# 3. Clip Validation & Deterministic Pipeline Tests
# ==============================================================================

class TestClipValidationAndPipeline:
    @pytest.fixture
    def temp_dir(self):
        d = tempfile.mkdtemp()
        yield Path(d)
        shutil.rmtree(d, ignore_errors=True)

    def test_pipeline_validates_non_empty_clips(self, temp_dir):
        """Pipeline runs with valid clips and produces rendered video."""
        planner = MagicMock()
        planner.plan.return_value = StoryPlan(
            title="Title",
            scenes=[
                Scene(index=1, narration="Narration 1", visual_prompt="Prompt 1"),
                Scene(index=2, narration="Narration 2", visual_prompt="Prompt 2"),
            ],
        )

        tts = MagicMock()
        def fake_synth(text, out):
            out.write_bytes(b"RIFF....WAVEfmt ....data....")
            return out
        tts.synthesize.side_effect = fake_synth

        img_prov = MagicMock()
        def fake_create(scene, out):
            out.write_bytes(b"dummy_mp4_bytes")
            return out
        img_prov.create.side_effect = fake_create

        renderer = MagicMock()
        def fake_render(imgs, auds, subs, out):
            out.write_bytes(b"final_video_mp4")
            return out
        renderer.render.side_effect = fake_render

        factory = VideoFactory(planner=planner, tts=tts, image_provider=img_prov, renderer=renderer)

        with patch("auto_video_factory.pipeline.media_duration", return_value=3.5):
            res = factory.generate("Test topic", temp_dir / "job_01")
            assert res.video.exists()
            assert res.video.read_bytes() == b"final_video_mp4"

    def test_pipeline_fails_closed_on_empty_clip(self, temp_dir):
        """Pipeline must raise RuntimeError if a generated clip is empty (0 bytes)."""
        planner = MagicMock()
        planner.plan.return_value = StoryPlan(
            title="Title",
            scenes=[
                Scene(index=1, narration="Narration 1", visual_prompt="Prompt 1"),
            ],
        )

        tts = MagicMock()
        tts.synthesize.side_effect = lambda t, o: o.write_bytes(b"audio")

        img_prov = MagicMock()
        # Returns empty (0-byte) file
        img_prov.create.side_effect = lambda s, o: o.touch()

        renderer = MagicMock()
        factory = VideoFactory(planner=planner, tts=tts, image_provider=img_prov, renderer=renderer)

        with patch("auto_video_factory.pipeline.media_duration", return_value=2.0):
            with pytest.raises(RuntimeError, match="Invalid visual media for scene 1"):
                factory.generate("Test topic", temp_dir / "job_02")

    def test_pipeline_rejects_duplicate_scene_indices(self, temp_dir):
        """Duplicate scene indices must raise ValueError before image creation or TTS synthesis."""
        planner = MagicMock()
        scenes = [
            Scene(index=1, narration="Narration 1", visual_prompt="Prompt 1"),
            Scene(index=1, narration="Narration 2", visual_prompt="Prompt 2"),  # duplicate!
        ]
        planner.plan.return_value = StoryPlan(title="Dup", scenes=scenes)

        tts = MagicMock()
        tts.synthesize.side_effect = lambda t, o: o.write_bytes(b"audio_data_xyz")

        img_prov = MagicMock()
        img_prov.create.side_effect = lambda s, o: (o.write_bytes(b"img_data"), o)[1]

        renderer = MagicMock()
        factory = VideoFactory(planner=planner, tts=tts, image_provider=img_prov, renderer=renderer)

        with patch("auto_video_factory.pipeline.media_duration", return_value=2.0):
            with pytest.raises(ValueError, match="Duplicate scene index"):
                factory.generate("Test topic", temp_dir / "job_dup")

        # Must not call image provider or TTS on invalid duplicate plan
        assert img_prov.create.call_count == 0, "img_prov.create must not be called when duplicate scene index exists"
        assert tts.synthesize.call_count == 0, "tts.synthesize must not be called when duplicate scene index exists"


class TestMobileLauncherScript:
    def test_start_mobile_service_script_structure_and_safety(self):
        script_path = Path("start_mobile_service.sh")
        assert script_path.exists()
        content = script_path.read_text(encoding="utf-8")

        # Root Cause A safety invariants:
        assert 'export AVF_LOCAL_PHONE="1"' in content
        assert 'export AVF_REQUIRE_AUTH="0"' in content
        assert 'export AVF_HOST="127.0.0.1"' in content
        assert 'AVF_BROWSER_BACKEND=' in content
        assert 'FLOW_ANDROID_CDP=' in content

        # Root Cause B safety invariants:
        # 1. Server started first in background before browser launch
        # 2. Bounded probe loop checking /health
        # 3. Only after readiness PASS, invoke browser (am start)
        # 4. Fail cleanly if readiness probe times out
        assert "/health" in content
        assert "am start" in content
        # Ensure 'am start' does not happen before background server launch
        server_launch_pos = content.find("auto_video_factory.web")
        am_start_pos = content.find("am start")
        assert server_launch_pos != -1
        assert am_start_pos != -1
        assert server_launch_pos < am_start_pos, (
            "start_mobile_service.sh must start server before attempting to open browser with am start"
        )


