"""
TDD tests for MoneyPrinterTurbo adapter layer.
RED phase — all tests expected to FAIL until mpt_adapter.py is implemented.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))
from auto_video_factory import mpt_adapter  # noqa: E402


# ===========================================================================
# 1. Pinned upstream ref
# ===========================================================================

class TestUpstreamPin:
    def test_mpt_sha_defined(self):
        assert hasattr(mpt_adapter, "MONEYPRINTERTURBO_REF")

    def test_mpt_sha_is_40_hex(self):
        sha = mpt_adapter.MONEYPRINTERTURBO_REF
        assert re.fullmatch(r"[0-9a-f]{40}", sha), f"Expected 40-char hex, got: {sha!r}"

    def test_mpt_sha_matches_known(self):
        expected = "b42e945b497176c823579f9b1895d9323446de23"
        assert mpt_adapter.MONEYPRINTERTURBO_REF == expected

    def test_mpt_repo_url_defined(self):
        assert hasattr(mpt_adapter, "MONEYPRINTERTURBO_REPO")
        assert "harry0703/MoneyPrinterTurbo" in mpt_adapter.MONEYPRINTERTURBO_REPO


# ===========================================================================
# 2. duration -> paragraph_number mapping
# ===========================================================================

class TestDurationMapping:
    @pytest.mark.parametrize("duration,expected", [
        ("45", 3),
        ("60", 4),
        ("90", 6),
    ])
    def test_duration_to_paragraphs(self, duration, expected):
        assert mpt_adapter.duration_to_paragraphs(duration) == expected

    @pytest.mark.parametrize("bad", ["0", "30", "120", "", "abc", "45.5"])
    def test_invalid_duration_raises(self, bad):
        with pytest.raises(ValueError, match=r"duration"):
            mpt_adapter.duration_to_paragraphs(bad)


# ===========================================================================
# 3. voice name mapping
# ===========================================================================

class TestVoiceMapping:
    def test_marin_maps_to_edge_tts(self):
        voice = mpt_adapter.map_voice("marin")
        assert "zh-CN" in voice or "Xiaoxiao" in voice or "Neural" in voice

    def test_onyx_maps_to_male_edge_tts(self):
        voice = mpt_adapter.map_voice("onyx")
        assert "zh-CN" in voice or "Neural" in voice

    def test_unknown_voice_raises(self):
        with pytest.raises(ValueError, match=r"voice"):
            mpt_adapter.map_voice("unknown-voice-xyz")

    def test_voice_no_api_key(self):
        for v in ["marin", "onyx"]:
            result = mpt_adapter.map_voice(v)
            assert "sk-" not in result
            assert "ghp_" not in result


# ===========================================================================
# 4. build_cli_args
# ===========================================================================

class TestBuildCliArgs:
    def _args(self, **kwargs):
        defaults = dict(
            topic="Một đệ tử bị tông môn ruồng bỏ",
            duration="60",
            voice="marin",
            video_source="pexels",
        )
        defaults.update(kwargs)
        return mpt_adapter.build_cli_args(**defaults)

    def test_returns_list(self):
        assert isinstance(self._args(), list)

    def test_video_subject_included(self):
        args = self._args(topic="Hello world topic")
        idx = args.index("--video-subject")
        assert args[idx + 1] == "Hello world topic"

    def test_video_aspect_portrait(self):
        args = self._args()
        assert "--video-aspect" in args
        idx = args.index("--video-aspect")
        assert args[idx + 1] == "9:16"

    def test_paragraph_number_from_duration(self):
        a45 = self._args(duration="45")
        a90 = self._args(duration="90")
        p45 = int(a45[a45.index("--paragraph-number") + 1])
        p90 = int(a90[a90.index("--paragraph-number") + 1])
        assert p45 < p90

    def test_voice_name_included(self):
        args = self._args(voice="marin")
        assert "--voice-name" in args

    def test_subtitle_enabled(self):
        args = self._args()
        assert "--subtitle-enabled" in args

    def test_shell_injection_topic_passed_verbatim(self):
        dangerous = "$(rm -rf /); echo pwned"
        args = self._args(topic=dangerous)
        assert dangerous in args  # safe because subprocess uses list, not shell=True

    def test_backtick_topic_verbatim(self):
        topic = "`evil`"
        args = self._args(topic=topic)
        assert topic in args

    def test_no_secret_in_cli_args(self):
        args = self._args()
        joined = " ".join(args)
        assert "sk-" not in joined
        assert "ghp_" not in joined

    def test_invalid_duration_propagates(self):
        with pytest.raises(ValueError):
            self._args(duration="999")

    def test_invalid_voice_propagates(self):
        with pytest.raises(ValueError):
            self._args(voice="bad-voice")


# ===========================================================================
# 5. build_config_toml
# ===========================================================================

class TestBuildConfigToml:
    def test_returns_string(self):
        result = mpt_adapter.build_config_toml(
            pexels_api_key="PX", llm_provider="openai", llm_api_key="OAI"
        )
        assert isinstance(result, str)

    def test_pexels_key_in_config(self):
        result = mpt_adapter.build_config_toml(
            pexels_api_key="PEXELS_TEST_KEY", llm_provider="openai", llm_api_key="K"
        )
        assert "PEXELS_TEST_KEY" in result

    def test_llm_api_key_in_config(self):
        result = mpt_adapter.build_config_toml(
            pexels_api_key="P", llm_provider="openai", llm_api_key="OAI_KEY"
        )
        assert "OAI_KEY" in result

    def test_empty_pexels_gives_empty_list(self):
        result = mpt_adapter.build_config_toml(
            pexels_api_key="", llm_provider="openai", llm_api_key="k"
        )
        assert "pexels_api_keys = []" in result

    def test_subtitle_provider_edge(self):
        result = mpt_adapter.build_config_toml(
            pexels_api_key="K", llm_provider="openai", llm_api_key="K"
        )
        assert 'subtitle_provider = "edge"' in result

    def test_tls_verify_true(self):
        result = mpt_adapter.build_config_toml(
            pexels_api_key="K", llm_provider="openai", llm_api_key="K"
        )
        assert "tls_verify = true" in result


# ===========================================================================
# 6. locate_output_video
# ===========================================================================

class TestLocateOutputVideo:
    def test_finds_video(self, tmp_path):
        task_dir = tmp_path / "storage" / "tasks" / "abc-123"
        task_dir.mkdir(parents=True)
        video = task_dir / "combined-1.mp4"
        video.write_bytes(b"\x00" * 100)
        result = mpt_adapter.locate_output_video(str(tmp_path), "abc-123")
        assert result == str(video)

    def test_raises_when_no_video(self, tmp_path):
        task_dir = tmp_path / "storage" / "tasks" / "no-video"
        task_dir.mkdir(parents=True)
        with pytest.raises(FileNotFoundError, match="mp4"):
            mpt_adapter.locate_output_video(str(tmp_path), "no-video")

    def test_raises_when_video_empty(self, tmp_path):
        task_dir = tmp_path / "storage" / "tasks" / "empty"
        task_dir.mkdir(parents=True)
        (task_dir / "combined-1.mp4").write_bytes(b"")
        with pytest.raises(ValueError, match="empty"):
            mpt_adapter.locate_output_video(str(tmp_path), "empty")


# ===========================================================================
# 7. sanitize_result_metadata
# ===========================================================================

class TestSanitizeResultMetadata:
    def test_removes_api_keys(self):
        raw = {
            "task_id": "123",
            "pexels_api_key": "secret-key",
            "openai_api_key": "sk-secret",
            "result": {"state": "success"},
        }
        clean = mpt_adapter.sanitize_result_metadata(raw)
        dumped = str(clean)
        assert "secret-key" not in dumped
        assert "sk-secret" not in dumped

    def test_preserves_safe_fields(self):
        raw = {"task_id": "abc", "state": "success", "duration": 45}
        clean = mpt_adapter.sanitize_result_metadata(raw)
        assert clean["task_id"] == "abc"
        assert clean["state"] == "success"

    def test_returns_dict(self):
        assert isinstance(mpt_adapter.sanitize_result_metadata({}), dict)


# ===========================================================================
# 8. Workflow file integrity
# ===========================================================================

WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "render-video.yml"

class TestWorkflowFile:
    def _content(self) -> str:
        return WORKFLOW_PATH.read_text(encoding="utf-8")

    def test_workflow_file_exists(self):
        assert WORKFLOW_PATH.exists()

    def test_workflow_dispatch_present(self):
        assert "workflow_dispatch" in self._content()

    def test_mpt_sha_pinned_in_workflow(self):
        assert "b42e945b497176c823579f9b1895d9323446de23" in self._content()

    def test_timeout_minutes_set(self):
        assert "timeout-minutes" in self._content()

    def test_artifact_name_correct(self):
        assert "auto-video-output" in self._content()

    def test_retention_days_short(self):
        content = self._content()
        m = re.search(r"retention-days:\s*(\d+)", content)
        assert m is not None
        assert int(m.group(1)) <= 3

    def test_no_github_token_hardcoded(self):
        assert "ghp_" not in self._content()

    def test_no_openai_key_hardcoded(self):
        assert "sk-" not in self._content()

    def test_permissions_least_privilege(self):
        assert "contents: read" in self._content()

    def test_topic_input_present(self):
        assert "topic" in self._content()

    def test_mpt_clone_uses_pinned_sha(self):
        assert "b42e945b497176c823579f9b1895d9323446de23" in self._content()

    def test_verify_output_step_present(self):
        content = self._content()
        assert "test -s" in content or "verify" in content.lower()
