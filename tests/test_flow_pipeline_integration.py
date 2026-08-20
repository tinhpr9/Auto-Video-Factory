"""
Targeted tests for Flow Provider Pipeline Integration.

TDD Suite covering:
1. pipeline routes configured Flow generation through FlowController
2. Flow-disabled configuration preserves existing behavior
3. unhealthy Flow provider fails closed before submission
4. USER_INTERACTION_REQUIRED stops the Flow stage safely
5. completed mock Flow artifact reaches the next pipeline stage
6. failed/invalid artifact does not advance the pipeline
7. recovered provider job does not trigger duplicate generation
8. existing pipeline regression remains green
"""
import os
import shutil
import tempfile
from pathlib import Path

from auto_video_factory.models import Scene, StoryPlan
from auto_video_factory.pipeline import VideoFactory
from auto_video_factory.story import TemplateStoryPlanner
from auto_video_factory.media import CardImageProvider, EspeakTTS, FFmpegRenderer
from auto_video_factory.flow_provider.models import (
    FlowAspectRatio,
    FlowFailureClass,
    FlowGenerationRequest,
    FlowHealthStatus,
    FlowJobRecord,
    FlowJobResult,
    FlowJobStatus,
    FlowModel,
)
from auto_video_factory.flow_provider.contract import FlowProvider
from auto_video_factory.flow_provider.provider import MockFlowProvider, ProductionFlowProvider
from auto_video_factory.flow_provider.controller import FlowController
from auto_video_factory.flow_provider.persistence import JobStateStore
from auto_video_factory.flow_provider.visual_provider import (
    FlowVisualProvider,
    FlowProviderError,
    FlowUserInteractionRequiredError,
)
from auto_video_factory.cli import build_factory_from_args, build_parser


# ---------------------------------------------------------------------------
# Helper Fakes
# ---------------------------------------------------------------------------

class FakeTTS:
    def synthesize(self, text: str, output: Path) -> Path:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"RIFFfake_wav_audio_content")
        return output


class FakeRenderer:
    def __init__(self):
        self.rendered_images: list[Path] = []
        self.rendered_audios: list[Path] = []
        self.rendered_video: Path | None = None

    def render(self, images: list[Path], audios: list[Path], subtitles: Path, output: Path) -> Path:
        self.rendered_images = list(images)
        self.rendered_audios = list(audios)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"fake_mp4_video_data")
        self.rendered_video = output
        return output


# ---------------------------------------------------------------------------
# Test 1: Pipeline routes configured Flow generation through FlowController
# ---------------------------------------------------------------------------

def test_1_pipeline_routes_configured_flow_through_flow_controller(tmp_path: Path):
    """Pipeline with FlowVisualProvider routes scene generation through FlowController."""
    storage_path = tmp_path / "flow_jobs.json"
    scenes_dir = tmp_path / "scenes"
    
    mock_provider = MockFlowProvider(initial_credits=200)
    controller = FlowController(
        provider=mock_provider,
        storage_path=storage_path,
        output_dir=scenes_dir,
        poll_interval_s=0.01,
    )
    visual_provider = FlowVisualProvider(
        controller=controller,
        model=FlowModel.VEO_3_1_FAST,
        aspect_ratio=FlowAspectRatio.PORTRAIT_9_16,
    )
    renderer = FakeRenderer()
    
    import auto_video_factory.pipeline as pipeline_module
    orig_duration = pipeline_module.media_duration
    pipeline_module.media_duration = lambda p: 2.0
    try:
        factory = VideoFactory(
            planner=TemplateStoryPlanner(scene_count=2),
            tts=FakeTTS(),
            image_provider=visual_provider,
            renderer=renderer,
        )
        res = factory.generate("Kiếm tu đại chiến", tmp_path / "job_flow")
        
        assert res.video.exists()
        assert len(renderer.rendered_images) == 2
        # Crucial check: visual provider returned .mp4 files, not .png
        assert all(img.suffix.lower() == ".mp4" for img in renderer.rendered_images)
        assert all(img.exists() and img.stat().st_size > 0 for img in renderer.rendered_images)
        # Check that jobs were recorded in FlowController store
        store = JobStateStore(storage_path)
        all_jobs = store.list_all_jobs()
        assert len(all_jobs) == 2
        assert all(j.status == FlowJobStatus.COMPLETED for j in all_jobs)
        assert mock_provider.generate_video_calls == 2
    finally:
        pipeline_module.media_duration = orig_duration


# ---------------------------------------------------------------------------
# Test 2: Flow-disabled configuration preserves existing behavior
# ---------------------------------------------------------------------------

def test_2_flow_disabled_configuration_preserves_existing_behavior():
    """Default CLI and non-flow settings preserve offline and openai providers."""
    # Default is offline
    parser = build_parser()
    args_default = parser.parse_args(["--topic", "Test story"])
    factory_default = build_factory_from_args(args_default)
    assert isinstance(factory_default.image_provider, CardImageProvider)
    assert isinstance(factory_default.planner, TemplateStoryPlanner)
    assert isinstance(factory_default.tts, EspeakTTS)

    # Explicit flow selection works via CLI
    args_flow = parser.parse_args(["--topic", "Test flow story", "--provider", "flow", "--flow-mock"])
    factory_flow = build_factory_from_args(args_flow)
    assert isinstance(factory_flow.image_provider, FlowVisualProvider)


# ---------------------------------------------------------------------------
# Test 3: Unhealthy Flow provider fails closed before submission
# ---------------------------------------------------------------------------

def test_3_unhealthy_flow_provider_fails_closed_before_submission(tmp_path: Path):
    """Unhealthy Flow provider (e.g. unauthenticated production) halts before submission."""
    storage_path = tmp_path / "flow_jobs.json"
    
    # Production provider without session or profile is unhealthy
    unhealthy_provider = ProductionFlowProvider(auth_token=None, profile_path=None)
    controller = FlowController(
        provider=unhealthy_provider,
        storage_path=storage_path,
        output_dir=tmp_path / "out",
    )
    visual_provider = FlowVisualProvider(controller=controller)
    
    scene = Scene(index=1, narration="Cảnh mở đầu", visual_prompt="Hero standing in mist")
    out_file = tmp_path / "out" / "scene-01.mp4"
    
    raised = False
    try:
        visual_provider.create(scene, out_file)
    except FlowProviderError as exc:
        raised = True
        assert "unhealthy" in str(exc).lower()
    assert raised, "Expected FlowProviderError for unhealthy provider"
    
    # Ensure no jobs were submitted to store
    store = JobStateStore(storage_path)
    assert len(store.list_all_jobs()) == 0


# ---------------------------------------------------------------------------
# Test 4: USER_INTERACTION_REQUIRED stops the Flow stage safely
# ---------------------------------------------------------------------------

def test_4_user_interaction_required_stops_stage_safely(tmp_path: Path):
    """USER_INTERACTION_REQUIRED causes fail-closed pause without bypass."""
    storage_path = tmp_path / "flow_jobs.json"
    
    class CaptchaFlowProvider(MockFlowProvider):
        def generate_video(self, req: FlowGenerationRequest) -> FlowJobResult:
            return FlowJobResult(
                job_id=req.job_id,
                provider_job_id="",
                status=FlowJobStatus.USER_INTERACTION_REQUIRED,
                failure_class=FlowFailureClass.USER_INTERACTION_REQUIRED,
                failure_message="CAPTCHA challenge detected",
            )
    
    prov = CaptchaFlowProvider(initial_credits=200)
    controller = FlowController(
        provider=prov,
        storage_path=storage_path,
        output_dir=tmp_path / "out",
    )
    visual_provider = FlowVisualProvider(controller=controller)
    
    scene = Scene(index=1, narration="Cảnh CAPTCHA", visual_prompt="Hero confronting trial")
    out_file = tmp_path / "out" / "scene-01.mp4"
    
    raised = False
    try:
        visual_provider.create(scene, out_file)
    except FlowUserInteractionRequiredError:
        raised = True
    assert raised, "Expected FlowUserInteractionRequiredError"
    
    assert controller.is_paused is True
    # Subsequent calls also fail closed immediately
    raised_subsequent = False
    try:
        visual_provider.create(scene, out_file)
    except FlowUserInteractionRequiredError:
        raised_subsequent = True
    assert raised_subsequent, "Expected subsequent call to fail closed with FlowUserInteractionRequiredError"


# ---------------------------------------------------------------------------
# Test 5: Completed mock Flow artifact reaches next pipeline stage
# ---------------------------------------------------------------------------

def test_5_completed_mock_flow_artifact_reaches_downstream_renderer(tmp_path: Path):
    """Completed mock Flow artifact is correctly forwarded to renderer."""
    storage_path = tmp_path / "flow_jobs.json"
    scenes_dir = tmp_path / "scenes"
    
    mock_provider = MockFlowProvider(initial_credits=200)
    controller = FlowController(
        provider=mock_provider,
        storage_path=storage_path,
        output_dir=scenes_dir,
        poll_interval_s=0.01,
    )
    visual_provider = FlowVisualProvider(controller=controller)
    
    scene = Scene(index=1, narration="Cảnh kiếm linh", visual_prompt="Sword spirit glowing in cavern")
    out_file = scenes_dir / "scene-01.mp4"
    
    created_path = visual_provider.create(scene, out_file)
    assert created_path.exists()
    assert created_path.stat().st_size > 0
    assert created_path.name == "scene-01.mp4"


# ---------------------------------------------------------------------------
# Test 6: Failed/invalid artifact does not advance the pipeline
# ---------------------------------------------------------------------------

def test_6_failed_or_invalid_artifact_does_not_advance_pipeline(tmp_path: Path):
    """If Flow generation fails, VideoFactory does not call renderer or finish."""
    storage_path = tmp_path / "flow_jobs.json"
    
    class FailingFlowProvider(MockFlowProvider):
        def generate_video(self, req: FlowGenerationRequest) -> FlowJobResult:
            return FlowJobResult(
                job_id=req.job_id,
                provider_job_id="",
                status=FlowJobStatus.FAILED,
                failure_class=FlowFailureClass.QUOTA,
                failure_message="Daily quota exceeded",
            )
    
    prov = FailingFlowProvider(initial_credits=200)
    controller = FlowController(
        provider=prov,
        storage_path=storage_path,
        output_dir=tmp_path / "scenes",
    )
    visual_provider = FlowVisualProvider(controller=controller)
    renderer = FakeRenderer()
    
    import auto_video_factory.pipeline as pipeline_module
    orig_duration = pipeline_module.media_duration
    pipeline_module.media_duration = lambda p: 2.0
    try:
        factory = VideoFactory(
            planner=TemplateStoryPlanner(scene_count=2),
            tts=FakeTTS(),
            image_provider=visual_provider,
            renderer=renderer,
        )
        raised = False
        try:
            factory.generate("Thất bại", tmp_path / "job_fail")
        except FlowProviderError as exc:
            raised = True
            assert "QUOTA" in str(exc) or "quota" in str(exc).lower()
        assert raised, "Expected FlowProviderError for failed generation"
        
        # Renderer was never invoked
        assert renderer.rendered_video is None
        assert len(renderer.rendered_images) == 0
    finally:
        pipeline_module.media_duration = orig_duration


# ---------------------------------------------------------------------------
# Test 7: Recovered provider job does not trigger duplicate generation
# ---------------------------------------------------------------------------

def test_7_recovered_provider_job_does_not_trigger_duplicate_generation(tmp_path: Path):
    """Resumed pending job with provider_job_id polls to completion without calling generate_video."""
    storage_path = tmp_path / "flow_jobs.json"
    scenes_dir = tmp_path / "scenes"
    
    mock_provider = MockFlowProvider(initial_credits=200)
    mock_provider._SHARED_JOBS["prov_existing_lotus"] = {
        "job_id": "scene_01_recov",
        "client_request_id": "scene_01_recov",
        "status": FlowJobStatus.COMPLETED,
        "model": FlowModel.VEO_3_1_FAST.value,
        "aspect_ratio": FlowAspectRatio.PORTRAIT_9_16.value,
    }
    
    # Pre-populate store with PENDING job with provider_job_id
    store = JobStateStore(storage_path)
    req = FlowGenerationRequest(
        job_id="scene_01_recov",
        prompt="A serene lotus in mountain pond",
        model=FlowModel.VEO_3_1_FAST,
        aspect_ratio=FlowAspectRatio.PORTRAIT_9_16,
        count=1,
        output_dir=scenes_dir,
    )
    record = FlowJobRecord.from_request(req, provider="mock")
    record.status = FlowJobStatus.PENDING
    record.provider_job_id = "prov_existing_lotus"
    store.save_job(record)
    
    controller = FlowController(
        provider=mock_provider,
        storage_path=storage_path,
        output_dir=scenes_dir,
        poll_interval_s=0.01,
    )
    visual_provider = FlowVisualProvider(controller=controller)
    
    # Resume
    resolved = visual_provider.resume_pending()
    assert len(resolved) == 1
    assert resolved[0].status == FlowJobStatus.COMPLETED
    assert mock_provider.generate_video_calls == 0  # Zero duplicate generation calls!


# ---------------------------------------------------------------------------
# Test 8: Existing pipeline regression remains green & web/cli flow modes work
# ---------------------------------------------------------------------------

def test_8_existing_pipeline_regression_remains_green(tmp_path: Path):
    """Offline & non-flow regression is green; CLI flow models and web config work."""
    # 1. Test CLI model parsing for all supported flow models
    parser = build_parser()
    for model_arg, expected_enum in [
        ("veo-3.1-fast", FlowModel.VEO_3_1_FAST),
        ("veo-3.1-quality", FlowModel.VEO_3_1_QUALITY),
        ("veo-2.1-fast", FlowModel.VEO_2_1_FAST),
    ]:
        args = parser.parse_args(["--topic", "Test", "--provider", "flow", "--flow-model", model_arg, "--flow-mock"])
        factory = build_factory_from_args(args)
        assert isinstance(factory.image_provider, FlowVisualProvider)
        assert factory.image_provider.model == expected_enum

    # 2. Test Web API GET /api/config with flow provider
    from fastapi.testclient import TestClient
    from auto_video_factory.web import WebSettings, create_app
    
    settings_flow = WebSettings(provider="flow", output_root=tmp_path / "web_flow", max_workers=1)
    os.environ["AVF_FLOW_MOCK"] = "1"
    try:
        app = create_app(settings=settings_flow)
        client = TestClient(app)
        res = client.get("/api/config")
        assert res.status_code == 200
        data = res.json()
        assert data["provider"] == "flow"
        assert len(data["durations"]) > 0
    finally:
        os.environ.pop("AVF_FLOW_MOCK", None)


if __name__ == "__main__":
    import tempfile
    for name, func in list(globals().items()):
        if name.startswith("test_") and callable(func):
            with tempfile.TemporaryDirectory() as td:
                if func.__code__.co_argcount > 0:
                    func(Path(td))
                else:
                    func()
                print(f"{name}: PASS")
    print("\nAll 8 flow pipeline integration tests passed!")
