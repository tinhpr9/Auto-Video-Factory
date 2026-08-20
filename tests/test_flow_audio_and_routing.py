"""
Targeted TDD RED Test Suite for:
1. Audio Fail-Closed Contract (Tests 1-5)
2. Flow Mode Routing and Model Override Precedence (Tests 6-11)
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest

from auto_video_factory.models import Scene
from auto_video_factory.pipeline import VideoFactory
from auto_video_factory.media import CardImageProvider, EspeakTTS, FFmpegRenderer
from auto_video_factory.flow_provider.models import (
    FlowAspectRatio,
    FlowFailureClass,
    FlowGenerationRequest,
    FlowJobResult,
    FlowJobStatus,
    FlowModel,
)
from auto_video_factory.flow_provider.provider import MockFlowProvider
from auto_video_factory.flow_provider.controller import FlowController
from auto_video_factory.flow_provider.visual_provider import (
    FlowVisualProvider,
    FlowProviderError,
    FlowGenerationError,
)
from auto_video_factory.cli import build_parser, build_factory_from_args


# ---------------------------------------------------------------------------
# Helpers to generate real minimal audio/silent video files for tests
# ---------------------------------------------------------------------------

def _create_silent_video(path: Path) -> Path:
    """Create a real minimal silent MP4 video using ffmpeg."""
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=black:s=720x1280:d=0.5:r=24",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)
        ],
        capture_output=True,
        check=True,
    )
    return path


def _create_audio_bearing_video(path: Path) -> Path:
    """Create a real minimal MP4 video with an AAC audio track using ffmpeg."""
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "color=c=black:s=720x1280:d=0.5:r=24",
            "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
            "-t", "0.5",
            "-c:v", "libx264", "-c:a", "aac",
            "-pix_fmt", "yuv420p", str(path)
        ],
        capture_output=True,
        check=True,
    )
    return path


# ---------------------------------------------------------------------------
# Batch A: Audio Fail-Closed Contract (Tests 1-5)
# ---------------------------------------------------------------------------

def test_1_audio_verification_fails_closed_when_ffprobe_unavailable(tmp_path: Path):
    """If video requires audio verification but ffprobe is missing, fail closed."""
    storage_path = tmp_path / "flow_jobs.json"
    scenes_dir = tmp_path / "scenes"
    mock_provider = MockFlowProvider(initial_credits=200)
    controller = FlowController(provider=mock_provider, storage_path=storage_path, output_dir=scenes_dir)
    visual_provider = FlowVisualProvider(controller=controller)

    vid = _create_silent_video(tmp_path / "test_vid.mp4")

    with patch("shutil.which", side_effect=lambda binary: None if binary == "ffprobe" else "/usr/bin/ffmpeg"):
        with pytest.raises(FlowGenerationError, match="ffprobe is required for audio verification"):
            visual_provider._strip_audio_if_present(vid)


def test_2_audio_stripping_fails_closed_when_ffmpeg_unavailable(tmp_path: Path):
    """If ffprobe detects audio stream but ffmpeg is missing for stripping, fail closed."""
    storage_path = tmp_path / "flow_jobs.json"
    scenes_dir = tmp_path / "scenes"
    mock_provider = MockFlowProvider(initial_credits=200)
    controller = FlowController(provider=mock_provider, storage_path=storage_path, output_dir=scenes_dir)
    visual_provider = FlowVisualProvider(controller=controller)

    vid = _create_audio_bearing_video(tmp_path / "test_with_audio.mp4")

    real_ffprobe = shutil.which("ffprobe")
    # ffprobe exists, but ffmpeg is missing
    with patch("shutil.which", side_effect=lambda binary: real_ffprobe if binary == "ffprobe" else None):
        with pytest.raises(FlowGenerationError, match="ffmpeg is required for audio stripping"):
            visual_provider._strip_audio_if_present(vid)


def test_3_audio_stream_exists_stripping_produces_valid_non_empty_output(tmp_path: Path):
    """If audio stream exists, successful stripping produces valid non-empty output with 0 audio streams."""
    storage_path = tmp_path / "flow_jobs.json"
    scenes_dir = tmp_path / "scenes"
    mock_provider = MockFlowProvider(initial_credits=200)
    controller = FlowController(provider=mock_provider, storage_path=storage_path, output_dir=scenes_dir)
    visual_provider = FlowVisualProvider(controller=controller)

    vid = _create_audio_bearing_video(tmp_path / "test_strip.mp4")
    assert vid.exists() and vid.stat().st_size > 0

    visual_provider._strip_audio_if_present(vid)

    assert vid.exists() and vid.stat().st_size > 0
    # Probe to confirm zero audio streams remain
    probe_cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "a",
        "-show_entries", "stream=codec_type",
        "-of", "json",
        str(vid),
    ]
    res = subprocess.run(probe_cmd, capture_output=True, text=True, check=True)
    import json
    data = json.loads(res.stdout)
    assert len(data.get("streams", [])) == 0


def test_4_stripping_command_fails_raises_flow_generation_error(tmp_path: Path):
    """If ffmpeg strip command fails or produces empty file, raise FlowGenerationError."""
    storage_path = tmp_path / "flow_jobs.json"
    scenes_dir = tmp_path / "scenes"
    mock_provider = MockFlowProvider(initial_credits=200)
    controller = FlowController(provider=mock_provider, storage_path=storage_path, output_dir=scenes_dir)
    visual_provider = FlowVisualProvider(controller=controller)

    vid = _create_audio_bearing_video(tmp_path / "test_fail.mp4")

    # Mock subprocess.run for the strip command (ffmpeg) to fail with returncode != 0
    original_run = subprocess.run
    def fake_run(cmd, *args, **kwargs):
        if "ffmpeg" in str(cmd[0]):
            return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="Simulated ffmpeg error")
        return original_run(cmd, *args, **kwargs)

    with patch("subprocess.run", side_effect=fake_run):
        with pytest.raises(FlowGenerationError, match="Audio stripping failed"):
            visual_provider._strip_audio_if_present(vid)


def test_5_confirmed_video_with_no_audio_allowed_through_unchanged(tmp_path: Path):
    """Confirmed video with no audio streams passes through verification unchanged."""
    storage_path = tmp_path / "flow_jobs.json"
    scenes_dir = tmp_path / "scenes"
    mock_provider = MockFlowProvider(initial_credits=200)
    controller = FlowController(provider=mock_provider, storage_path=storage_path, output_dir=scenes_dir)
    visual_provider = FlowVisualProvider(controller=controller)

    vid = _create_silent_video(tmp_path / "test_silent.mp4")
    original_size = vid.stat().st_size

    visual_provider._strip_audio_if_present(vid)

    assert vid.exists()
    assert vid.stat().st_size == original_size


# ---------------------------------------------------------------------------
# Batch B: Flow Mode Routing and Model Override Precedence (Tests 6-11)
# ---------------------------------------------------------------------------

def test_6_flow_quality_selects_quality_model(tmp_path: Path):
    """flow_quality mode routes to FlowModel.VEO_3_1_QUALITY."""
    storage_path = tmp_path / "flow_jobs.json"
    scenes_dir = tmp_path / "scenes"
    mock_provider = MockFlowProvider(initial_credits=200)
    controller = FlowController(provider=mock_provider, storage_path=storage_path, output_dir=scenes_dir)

    provider = FlowVisualProvider(controller=controller, flow_mode="flow_quality")
    assert provider.model == FlowModel.VEO_3_1_QUALITY
    assert provider.flow_mode == "flow_quality"


def test_7_flow_economy_selects_economy_model(tmp_path: Path):
    """flow_economy mode routes to FlowModel.VEO_2_1_FAST."""
    storage_path = tmp_path / "flow_jobs.json"
    scenes_dir = tmp_path / "scenes"
    mock_provider = MockFlowProvider(initial_credits=200)
    controller = FlowController(provider=mock_provider, storage_path=storage_path, output_dir=scenes_dir)

    provider = FlowVisualProvider(controller=controller, flow_mode="flow_economy")
    assert provider.model == FlowModel.VEO_2_1_FAST
    assert provider.flow_mode == "flow_economy"


def test_8_flow_balanced_selects_balanced_model(tmp_path: Path):
    """flow_balanced mode routes to FlowModel.VEO_3_1_FAST."""
    storage_path = tmp_path / "flow_jobs.json"
    scenes_dir = tmp_path / "scenes"
    mock_provider = MockFlowProvider(initial_credits=200)
    controller = FlowController(provider=mock_provider, storage_path=storage_path, output_dir=scenes_dir)

    provider = FlowVisualProvider(controller=controller, flow_mode="flow_balanced")
    assert provider.model == FlowModel.VEO_3_1_FAST
    assert provider.flow_mode == "flow_balanced"


def test_9_explicit_flow_model_override_takes_precedence_over_flow_mode(tmp_path: Path):
    """Explicit model override takes precedence over flow_mode routing."""
    storage_path = tmp_path / "flow_jobs.json"
    scenes_dir = tmp_path / "scenes"
    mock_provider = MockFlowProvider(initial_credits=200)
    controller = FlowController(provider=mock_provider, storage_path=storage_path, output_dir=scenes_dir)

    # flow_mode is flow_economy (which defaults to VEO_2_1_FAST), but model is explicitly VEO_3_1_QUALITY
    provider = FlowVisualProvider(
        controller=controller,
        model=FlowModel.VEO_3_1_QUALITY,
        flow_mode="flow_economy",
    )
    assert provider.model == FlowModel.VEO_3_1_QUALITY


def test_10_request_sent_to_flow_controller_contains_selected_model(tmp_path: Path):
    """Generation request submitted by FlowVisualProvider contains the effective routed model."""
    storage_path = tmp_path / "flow_jobs.json"
    scenes_dir = tmp_path / "scenes"
    mock_provider = MockFlowProvider(initial_credits=500)
    controller = FlowController(provider=mock_provider, storage_path=storage_path, output_dir=scenes_dir, poll_interval_s=0.01)

    # 1. Test flow_quality mode
    quality_provider = FlowVisualProvider(controller=controller, flow_mode="flow_quality")
    scene_q = Scene(index=1, narration="Quality scene", visual_prompt="A majestic dragon in golden clouds")
    out_q = scenes_dir / "scene_q.mp4"
    quality_provider.create(scene_q, out_q)

    # Inspect submitted job in store
    all_jobs = controller.store.list_all_jobs()
    job_q = next((j for j in all_jobs if j.prompt == scene_q.visual_prompt), None)
    assert job_q is not None
    assert job_q.model == FlowModel.VEO_3_1_QUALITY.value

    # 2. Test flow_economy mode
    economy_provider = FlowVisualProvider(controller=controller, flow_mode="flow_economy")
    scene_e = Scene(index=2, narration="Economy scene", visual_prompt="A solitary wanderer in bamboo forest")
    out_e = scenes_dir / "scene_e.mp4"
    economy_provider.create(scene_e, out_e)

    all_jobs = controller.store.list_all_jobs()
    job_e = next((j for j in all_jobs if j.prompt == scene_e.visual_prompt), None)
    assert job_e is not None
    assert job_e.model == FlowModel.VEO_2_1_FAST.value


def test_11_existing_offline_and_openai_behavior_remains_unchanged(tmp_path: Path):
    """Offline and OpenAI providers remain unaffected by Flow routing changes."""
    parser = build_parser()

    # Offline
    args_offline = parser.parse_args(["--topic", "Offline topic", "--provider", "offline"])
    factory_offline = build_factory_from_args(args_offline)
    assert isinstance(factory_offline.image_provider, CardImageProvider)

    # CLI flow mode with explicit override
    args_flow = parser.parse_args([
        "--topic", "Flow topic",
        "--provider", "flow",
        "--flow-mode", "flow_quality",
        "--flow-model", "veo-2.1-fast",
        "--flow-mock",
    ])
    factory_flow = build_factory_from_args(args_flow)
    assert isinstance(factory_flow.image_provider, FlowVisualProvider)
    assert factory_flow.image_provider.model == FlowModel.VEO_2_1_FAST


def test_12_unknown_flow_mode_raises_value_error(tmp_path: Path):
    """Unknown flow_mode fails closed with ValueError."""
    storage_path = tmp_path / "flow_jobs.json"
    scenes_dir = tmp_path / "scenes"
    mock_provider = MockFlowProvider(initial_credits=200)
    controller = FlowController(provider=mock_provider, storage_path=storage_path, output_dir=scenes_dir)

    with pytest.raises(ValueError, match="Unknown flow_mode 'flow_unknown'"):
        FlowVisualProvider(controller=controller, flow_mode="flow_unknown")


# ===========================================================================
# Promptfoo Flow Routing Evaluation Tests
# ===========================================================================

class TestFlowRoutingEvalScript:
    """Validate Promptfoo eval script for Flow routing and override precedence."""

    def test_eval_flow_quality_routing(self):
        import sys, json
        eval_path = Path(__file__).resolve().parents[1] / "evals" / "flow_routing_eval.py"
        context = json.dumps({"vars": {"flow_mode": "flow_quality"}})
        res = subprocess.run([sys.executable, str(eval_path), context], capture_output=True, text=True, check=True)
        data = json.loads(res.stdout)
        assert data["flow_mode"] == "flow_quality"
        assert data["explicit_model"] is None
        assert data["resolved_model"] == FlowModel.VEO_3_1_QUALITY.value

    def test_eval_flow_economy_routing(self):
        import sys, json
        eval_path = Path(__file__).resolve().parents[1] / "evals" / "flow_routing_eval.py"
        context = json.dumps({"vars": {"flow_mode": "flow_economy"}})
        res = subprocess.run([sys.executable, str(eval_path), context], capture_output=True, text=True, check=True)
        data = json.loads(res.stdout)
        assert data["flow_mode"] == "flow_economy"
        assert data["explicit_model"] is None
        assert data["resolved_model"] == FlowModel.VEO_2_1_FAST.value

    def test_eval_flow_balanced_routing(self):
        import sys, json
        eval_path = Path(__file__).resolve().parents[1] / "evals" / "flow_routing_eval.py"
        context = json.dumps({"vars": {"flow_mode": "flow_balanced"}})
        res = subprocess.run([sys.executable, str(eval_path), context], capture_output=True, text=True, check=True)
        data = json.loads(res.stdout)
        assert data["flow_mode"] == "flow_balanced"
        assert data["explicit_model"] is None
        assert data["resolved_model"] == FlowModel.VEO_3_1_FAST.value

    def test_eval_explicit_override_precedence(self):
        import sys, json
        eval_path = Path(__file__).resolve().parents[1] / "evals" / "flow_routing_eval.py"
        context = json.dumps({"vars": {"flow_mode": "flow_quality", "flow_model": "veo-2.1-fast"}})
        res = subprocess.run([sys.executable, str(eval_path), context], capture_output=True, text=True, check=True)
        data = json.loads(res.stdout)
        assert data["flow_mode"] == "flow_quality"
        assert data["explicit_model"] == FlowModel.VEO_2_1_FAST.value
        assert data["resolved_model"] == FlowModel.VEO_2_1_FAST.value

