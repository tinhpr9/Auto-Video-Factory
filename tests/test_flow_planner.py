"""
TDD unit tests for Google Flow Quality Pipeline & Scene Planner (PR: feat/v4.3-flow-quality-pipeline).
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import MagicMock, patch

import pytest

from auto_video_factory.flow_planner import (
    CHARACTER_BIBLE,
    DEFAULT_FLOW_MODE,
    FLOW_MODES,
    FlowScene,
    FlowScenePack,
    export_flow_pack,
    plan_flow_scenes,
    strip_clip_audio,
    validate_and_stage_flow_clips,
    validate_clip,
)
from auto_video_factory.mpt_adapter import build_cli_args


# ===========================================================================
# 1. Character Bible & Constraints
# ===========================================================================

class TestCharacterBible:
    def test_character_bible_contains_xianxia_keys(self):
        assert "protagonist" in CHARACTER_BIBLE
        assert "world_style" in CHARACTER_BIBLE
        assert "negative_constraints" in CHARACTER_BIBLE

        protag = CHARACTER_BIBLE["protagonist"]
        assert "Vietnamese" in protag["identity"]
        assert "ponytail" in protag["features"]
        assert "indigo" in protag["attire"]

    def test_negative_constraints_block_irrelevant_footage(self):
        negatives = CHARACTER_BIBLE["negative_constraints"]
        assert "astronaut" in negatives
        assert "desert sand dunes" in negatives
        assert "modern cars" in negatives
        assert "city skyscrapers" in negatives


# ===========================================================================
# 2. Scene Planner & Flow Credit Modes
# ===========================================================================

class TestFlowScenePlanner:
    def test_plan_flow_scenes_balanced_default(self):
        pack = plan_flow_scenes("Một đệ tử bị tông môn ruồng bỏ, thức tỉnh sức mạnh cổ đại")
        assert pack.flow_mode == "flow_balanced"
        assert pack.total_scenes == 6
        assert len(pack.scenes) == 6

        # In balanced mode: scene 4 is hero scene (Veo Quality = 100), others are Omni Flash (25)
        # Total credits = 5 * 25 + 100 = 225
        assert pack.estimated_total_credits == 225
        assert pack.scenes[3].recommended_model == "veo-3.1-quality"
        assert pack.scenes[3].estimated_credits == 100
        assert pack.scenes[0].recommended_model == "gemini-omni-flash"
        assert pack.scenes[0].estimated_credits == 25

    def test_plan_flow_scenes_economy(self):
        pack = plan_flow_scenes("Một đệ tử", flow_mode="flow_economy")
        assert pack.flow_mode == "flow_economy"
        assert pack.total_scenes == 6
        # All scenes are Veo Lite (10 credits each) = 60
        assert pack.estimated_total_credits == 60
        for s in pack.scenes:
            assert s.recommended_model == "veo-3.1-lite"
            assert s.estimated_credits == 10

    def test_plan_flow_scenes_quality(self):
        pack = plan_flow_scenes("Một đệ tử", flow_mode="flow_quality")
        assert pack.flow_mode == "flow_quality"
        assert pack.total_scenes == 6
        # Key scenes (1, 4, 6) are Veo Quality (100 each), others Omni Flash (25 each)
        # Total = 3*100 + 3*25 = 375
        assert pack.estimated_total_credits == 375
        assert pack.scenes[0].recommended_model == "veo-3.1-quality"
        assert pack.scenes[3].recommended_model == "veo-3.1-quality"
        assert pack.scenes[5].recommended_model == "veo-3.1-quality"

    def test_plan_flow_scenes_duration_scaling(self):
        pack_60 = plan_flow_scenes("Topic", duration_seconds=60)
        assert pack_60.scenes[0].target_seconds == 10
        # 60s: 5 scenes * 30 (Omni Flash 10s) + 100 (Veo Quality) = 250
        assert pack_60.estimated_total_credits == 250

        pack_90 = plan_flow_scenes("Topic", duration_seconds=90)
        assert pack_90.scenes[0].target_seconds == 15
        assert pack_90.estimated_total_credits == 250

    def test_invalid_flow_mode_raises(self):
        with pytest.raises(ValueError, match="Unknown flow_mode"):
            plan_flow_scenes("Topic", flow_mode="flow_super_unsupported")

    def test_all_scene_prompts_contain_character_bible_and_9_16(self):
        pack = plan_flow_scenes("Một đệ tử")
        protag = CHARACTER_BIBLE["protagonist"]
        for s in pack.scenes:
            assert "9:16" in s.prompt
            assert protag["features"] in s.prompt
            assert protag["attire"] in s.prompt
            assert "scene0" in s.expected_filename
            assert s.target_seconds == 8
            assert len(s.negative_constraints) > 5


# ===========================================================================
# 3. Export Pack (JSON, Prompts TXT, Checklist TXT)
# ===========================================================================

class TestExportFlowPack:
    def test_export_flow_pack_creates_all_files(self, tmp_path):
        pack = plan_flow_scenes("Một đệ tử bị ruồng bỏ", flow_mode="flow_balanced")
        out_paths = export_flow_pack(pack, tmp_path)

        assert out_paths["json"].exists()
        assert out_paths["prompts"].exists()
        assert out_paths["checklist"].exists()

        # Check JSON
        data = json.loads(out_paths["json"].read_text(encoding="utf-8"))
        assert data["total_scenes"] == 6
        assert data["estimated_total_credits"] == 225
        assert len(data["scenes"]) == 6

        # Check Prompts TXT
        prompts_text = out_paths["prompts"].read_text(encoding="utf-8")
        assert "=== GOOGLE FLOW PROMPT PACK" in prompts_text
        assert "SCENE 01" in prompts_text
        assert "SCENE 06" in prompts_text
        assert "veo-3.1-quality" in prompts_text

        # Check Checklist TXT
        checklist_text = out_paths["checklist"].read_text(encoding="utf-8")
        assert "GOOGLE FLOW QUALITY CHECKLIST" in checklist_text
        assert "Scene 01" in checklist_text
        assert "Scene 06" in checklist_text


# ===========================================================================
# 4. CLI Execution
# ===========================================================================

class TestFlowCLI:
    def test_cli_generates_pack(self, tmp_path):
        cmd = [
            sys.executable,
            "-m", "auto_video_factory.flow_cli",
            "--topic", "Một kiếm tu",
            "--duration", "45",
            "--mode", "flow_balanced",
            "--out", str(tmp_path),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        assert res.returncode == 0
        assert (tmp_path / "flow_scene_pack.json").exists()
        assert (tmp_path / "flow_prompts.txt").exists()
        assert (tmp_path / "flow_checklist.txt").exists()


# ===========================================================================
# 5. Clip Validation & Audio Stripping (Deterministic Ordering)
# ===========================================================================

class TestFlowClipStaging:
    def test_validate_nonexistent_clip(self, tmp_path):
        assert not validate_clip(tmp_path / "nonexistent.mp4")

    def test_validate_empty_clip(self, tmp_path):
        p = tmp_path / "empty.mp4"
        p.write_bytes(b"")
        assert not validate_clip(p)

    def test_stage_empty_dir_returns_empty_list(self, tmp_path):
        res = validate_and_stage_flow_clips(tmp_path / "missing", tmp_path / "staged")
        assert res == []


# ===========================================================================
# 6. Zero Pexels Leakage on Local Flow Source
# ===========================================================================

class TestZeroPexelsLeakage:
    def test_local_flow_source_includes_video_materials(self):
        materials = "/path/to/scene01.mp4,/path/to/scene02.mp4"
        args = build_cli_args(
            topic="Topic",
            duration="45",
            voice="marin",
            video_source="local",
            video_materials=materials,
        )
        assert "--video-source" in args
        idx = args.index("--video-source")
        assert args[idx + 1] == "local"
        assert "--video-materials" in args
        idx_mat = args.index("--video-materials")
        assert args[idx_mat + 1] == materials


# ===========================================================================
# 7. Promptfoo Flow Prompt Evaluation Script
# ===========================================================================

class TestFlowPromptEvalScript:
    def test_eval_script_execution(self):
        eval_path = Path(__file__).resolve().parents[1] / "evals" / "flow_prompt_eval.py"
        context = json.dumps({"vars": {"topic": "Một đệ tử", "duration": 45, "flow_mode": "flow_balanced"}})
        res = subprocess.run([sys.executable, str(eval_path), context], capture_output=True, text=True)
        assert res.returncode == 0
        data = json.loads(res.stdout)
        assert data["flow_mode"] == "flow_balanced"
        assert data["total_scenes"] == 6
        assert data["estimated_total_credits"] == 225
        assert len(data["scenes"]) == 6

