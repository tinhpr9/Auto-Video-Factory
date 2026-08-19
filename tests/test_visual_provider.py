"""
TDD unit tests for Visual Provider architecture (PR B: feat/v4.2-gemini-video-provider).
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch
import urllib.error

import pytest

from auto_video_factory import visual_provider
from auto_video_factory.visual_provider import (
    CHARACTER_BIBLE,
    DEFAULT_MAX_GENERATION_SECONDS,
    GEMINI_VIDEO_FALLBACK_MODEL,
    GEMINI_VIDEO_MODEL,
    BillingBlockedError,
    CostCeilingExceededError,
    GeminiVideoProvider,
    PexelsVisualProvider,
    ScenePlan,
    get_visual_provider,
)


# ===========================================================================
# 1. Single Source of Truth for Models
# ===========================================================================

class TestModelDefinitions:
    def test_gemini_video_model_name(self):
        assert GEMINI_VIDEO_MODEL == "gemini-omni-flash-preview"

    def test_fallback_model_name(self):
        assert "veo" in GEMINI_VIDEO_FALLBACK_MODEL.lower()


# ===========================================================================
# 2. Factory Registry & Routing
# ===========================================================================

class TestProviderFactory:
    def test_get_pexels_provider(self):
        p = get_visual_provider("pexels")
        assert isinstance(p, PexelsVisualProvider)
        assert p.name == "pexels"

    def test_get_gemini_video_provider(self):
        p = get_visual_provider("gemini_video")
        assert isinstance(p, GeminiVideoProvider)
        assert p.name == "gemini_video"
        assert p.model_name == GEMINI_VIDEO_MODEL

    def test_unknown_provider_raises(self):
        with pytest.raises(ValueError, match="Unknown visual provider"):
            get_visual_provider("unknown_provider_xyz")


# ===========================================================================
# 3. Pexels Visual Provider Preflight
# ===========================================================================

class TestPexelsVisualProvider:
    def test_preflight_missing_key(self):
        p = PexelsVisualProvider()
        res = p.preflight_check("")
        assert res["status"] == "error"
        assert res["reason"] == "missing_api_key"

    def test_preflight_valid_key(self):
        p = PexelsVisualProvider()
        res = p.preflight_check("valid_pexels_key_123")
        assert res["status"] == "ok"


# ===========================================================================
# 4. Gemini Video Provider Preflight & Error Classification
# ===========================================================================

class TestGeminiVideoProviderPreflight:
    def test_preflight_missing_key(self):
        p = GeminiVideoProvider()
        res = p.preflight_check("")
        assert res["status"] == "error"
        assert res["reason"] == "missing_api_key"

    @patch("urllib.request.urlopen")
    def test_preflight_success(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "models": [{"name": "models/gemini-omni-flash-preview"}]
        }).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        p = GeminiVideoProvider()
        res = p.preflight_check("valid_gemini_key")
        assert res["status"] == "ok"
        assert res["model"] == GEMINI_VIDEO_MODEL
        assert res["available_models_count"] == 1

    @patch("urllib.request.urlopen")
    def test_preflight_billing_required(self, mock_urlopen):
        error = urllib.error.HTTPError(
            url="http://test",
            code=403,
            msg="Forbidden",
            hdrs={},
            fp=MagicMock(read=lambda: b'{"error": {"message": "Billing not enabled"}}'),
        )
        mock_urlopen.side_effect = error

        p = GeminiVideoProvider()
        res = p.preflight_check("no_billing_key")
        assert res["status"] == "error"
        assert res["reason"] == "unauthorized_or_billing_required"
        assert res["http_code"] == 403

    @patch("urllib.request.urlopen")
    def test_preflight_rate_limit(self, mock_urlopen):
        error = urllib.error.HTTPError(
            url="http://test",
            code=429,
            msg="Too Many Requests",
            hdrs={},
            fp=MagicMock(read=lambda: b'{"error": {"message": "Quota exceeded"}}'),
        )
        mock_urlopen.side_effect = error

        p = GeminiVideoProvider()
        res = p.preflight_check("rate_limited_key")
        assert res["status"] == "error"
        assert res["reason"] == "rate_limit_exceeded"


# ===========================================================================
# 5. Scene Planner & Character Bible Consistency
# ===========================================================================

class TestScenePlanner:
    def test_plan_scenes_45s_count(self):
        p = GeminiVideoProvider()
        scenes = p.plan_scenes(
            topic="Một đệ tử bị tông môn ruồng bỏ",
            duration_seconds=45,
        )
        assert 3 <= len(scenes) <= 7
        assert scenes[0].scene_id == 1
        assert scenes[-1].scene_id == len(scenes)

    def test_scene_prompts_contain_character_bible(self):
        p = GeminiVideoProvider()
        scenes = p.plan_scenes(topic="Topic", duration_seconds=45)
        protag = CHARACTER_BIBLE["protagonist"]
        for s in scenes:
            assert "9:16" in s.prompt
            assert protag["features"] in s.prompt
            assert protag["attire"] in s.prompt
            assert len(s.negative_constraints) > 0
            assert "astronaut" in s.negative_constraints

    def test_plan_scenes_smoke_mode(self):
        p = GeminiVideoProvider()
        scenes = p.plan_scenes(
            topic="Topic",
            duration_seconds=45,
            quality_mode="smoke",
            max_seconds=10,
        )
        assert len(scenes) == 1
        assert scenes[0].duration_target == 4
        assert scenes[0].scene_id == 1

    def test_cost_ceiling_exceeded_raises(self):
        p = GeminiVideoProvider()
        with pytest.raises(CostCeilingExceededError):
            p.plan_scenes(
                topic="Topic",
                duration_seconds=90,
                max_seconds=10,  # low ceiling
            )


# ===========================================================================
# 6. Video Validation & Audio Stripping
# ===========================================================================

class TestVideoValidation:
    def test_validate_nonexistent_clip(self, tmp_path):
        p = GeminiVideoProvider()
        assert not p.validate_scene_clip(tmp_path / "nonexistent.mp4")

    def test_validate_empty_clip(self, tmp_path):
        empty = tmp_path / "empty.mp4"
        empty.write_bytes(b"")
        p = GeminiVideoProvider()
        assert not p.validate_scene_clip(empty)

    @patch("subprocess.run")
    def test_validate_valid_clip(self, mock_run, tmp_path):
        valid = tmp_path / "valid.mp4"
        valid.write_bytes(b"fake_mp4_bytes")
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps({
                "streams": [{"codec_type": "video", "width": 720, "height": 1280}],
                "format": {"duration": "4.5"},
            }),
        )
        p = GeminiVideoProvider()
        assert p.validate_scene_clip(valid)

    @patch("subprocess.run")
    def test_validate_audio_only_clip_returns_false(self, mock_run, tmp_path):
        audio_clip = tmp_path / "audio.mp4"
        audio_clip.write_bytes(b"fake_audio_bytes")
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps({
                "streams": [{"codec_type": "audio"}],
                "format": {"duration": "4.5"},
            }),
        )
        p = GeminiVideoProvider()
        assert not p.validate_scene_clip(audio_clip)


class TestAudioStripping:
    @patch("subprocess.run")
    def test_strip_audio_success(self, mock_run, tmp_path):
        inp = tmp_path / "input.mp4"
        inp.write_bytes(b"sample_data")
        out = tmp_path / "output.mp4"

        # First call is ffmpeg (creates output file), second call is ffprobe (checks audio streams)
        def fake_run(cmd, *args, **kwargs):
            if cmd[0] == "ffmpeg":
                out.write_bytes(b"stripped_video_data")
                return MagicMock(returncode=0, stderr="")
            elif cmd[0] == "ffprobe":
                return MagicMock(returncode=0, stdout=json.dumps({"streams": []}))
            return MagicMock(returncode=0)

        mock_run.side_effect = fake_run
        p = GeminiVideoProvider()
        res = p.strip_audio(inp, out)
        assert res == out
        assert out.exists()

    @patch("subprocess.run")
    def test_strip_audio_ffmpeg_failure_raises_and_cleans(self, mock_run, tmp_path):
        inp = tmp_path / "input.mp4"
        inp.write_bytes(b"sample_data")
        out = tmp_path / "output.mp4"

        mock_run.return_value = MagicMock(returncode=1, stderr="FFmpeg syntax error")
        p = GeminiVideoProvider()
        with pytest.raises(RuntimeError, match="Fail-closed: Audio stripping failed"):
            p.strip_audio(inp, out)
        assert not out.exists()

    @patch("subprocess.run")
    def test_strip_audio_lingering_audio_raises_and_cleans(self, mock_run, tmp_path):
        inp = tmp_path / "input.mp4"
        inp.write_bytes(b"sample_data")
        out = tmp_path / "output.mp4"

        def fake_run(cmd, *args, **kwargs):
            if cmd[0] == "ffmpeg":
                out.write_bytes(b"video_with_audio")
                return MagicMock(returncode=0, stderr="")
            elif cmd[0] == "ffprobe":
                return MagicMock(returncode=0, stdout=json.dumps({"streams": [{"codec_type": "audio"}]}))
            return MagicMock(returncode=0)

        mock_run.side_effect = fake_run
        p = GeminiVideoProvider()
        with pytest.raises(RuntimeError, match="Fail-closed.*still contains.*audio stream"):
            p.strip_audio(inp, out)
        assert not out.exists()


class TestGenerateSceneClip:
    def test_generate_missing_key_raises_billing_error(self, tmp_path):
        p = GeminiVideoProvider()
        scene = p.plan_scenes(topic="Topic", duration_seconds=45, quality_mode="smoke", max_seconds=10)[0]
        with pytest.raises(BillingBlockedError, match="Missing GEMINI_API_KEY"):
            p.generate_scene_clip(scene, tmp_path / "out.mp4", "")

    @patch("urllib.request.urlopen")
    def test_generate_billing_blocked_raises(self, mock_urlopen, tmp_path):
        error = urllib.error.HTTPError(
            url="http://test",
            code=403,
            msg="Forbidden",
            hdrs={},
            fp=MagicMock(read=lambda: b'{"error": {"message": "Billing not enabled on project"}}'),
        )
        mock_urlopen.side_effect = error
        p = GeminiVideoProvider()
        scene = p.plan_scenes(topic="Topic", duration_seconds=45, quality_mode="smoke", max_seconds=10)[0]
        with pytest.raises(BillingBlockedError, match="Gemini Video API blocked"):
            p.generate_scene_clip(scene, tmp_path / "out.mp4", "valid_key")

    @patch("urllib.request.urlopen")
    def test_generate_missing_video_bytes_raises(self, mock_urlopen, tmp_path):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"video": {}}).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        p = GeminiVideoProvider()
        scene = p.plan_scenes(topic="Topic", duration_seconds=45, quality_mode="smoke", max_seconds=10)[0]
        with pytest.raises(RuntimeError, match="returned no base64 video data"):
            p.generate_scene_clip(scene, tmp_path / "out.mp4", "valid_key")

    @patch("urllib.request.urlopen")
    def test_generate_scene_clip_success_decodes_and_validates(self, mock_urlopen, tmp_path):
        import base64
        fake_b64 = base64.b64encode(b"dummy_video_payload").decode("ascii")
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "video": {"bytesBase64Encoded": fake_b64}
        }).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        p = GeminiVideoProvider()
        p.strip_audio = MagicMock(side_effect=lambda src, dst: dst.write_bytes(b"stripped_bytes") or dst)
        p.validate_scene_clip = MagicMock(return_value=True)

        scene = p.plan_scenes(topic="Topic", duration_seconds=45, quality_mode="smoke", max_seconds=10)[0]
        out_file = tmp_path / "scene_01.mp4"
        res = p.generate_scene_clip(scene, out_file, "valid_key")
        assert res == out_file
        assert p.strip_audio.called
        assert p.validate_scene_clip.called


# ===========================================================================
# 7. Promptfoo Scene Planner Evaluation Script
# ===========================================================================

class TestScenePlannerEvalScript:
    def test_eval_script_execution(self):
        import subprocess, sys
        eval_path = Path(__file__).resolve().parents[1] / "evals" / "scene_planner_eval.py"
        context = json.dumps({"vars": {"topic": "Một đệ tử", "duration": 45}})
        result = subprocess.run([sys.executable, str(eval_path), context], capture_output=True, text=True)
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["model"] == GEMINI_VIDEO_MODEL
        assert data["scene_count"] >= 3
        assert len(data["scenes"]) == data["scene_count"]

