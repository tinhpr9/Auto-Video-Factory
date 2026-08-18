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
        assert pack_60.total_scenes == 6
        # 60s: 5 scenes * 30 (Omni Flash 10s) + 100 (Veo Quality) = 250
        assert pack_60.estimated_total_credits == 250

        pack_90 = plan_flow_scenes("Topic", duration_seconds=90)
        assert pack_90.scenes[0].target_seconds == 10
        assert pack_90.total_scenes == 9
        # 90s: 8 scenes * 30 (Omni Flash 10s) + 100 (Veo Quality) = 340
        assert pack_90.estimated_total_credits == 340

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
# 5. Clip Validation & Audio Stripping (Fail-Closed & Exact Scenes)
# ===========================================================================

# NEEDS_EVIDENCE→VALID: Generic topic divergence
# Evidence: two generic topics (no kiếm/đan/thể keywords) produce identical roles
# and share 4/6 identical actions. Only scenes 2 and 4 differ because the literal
# topic string is injected. This is NOT material narrative divergence.
class TestGenericTopicDiversity:
    """Generic topics (no archetype keyword) must produce materially different
    narrative arcs beyond literal topic-string injection."""

    # RED: roles must differ between two unrelated generic topics
    def test_generic_topics_produce_different_narrative_roles(self):
        """Two unrelated generic topics must have at least some differing narrative roles."""
        topic_mirror = "Một đệ tử khám phá cổ kính đảo ngược thời gian tại mộ địa cổ đại"
        topic_beast  = "Một cô nhi bảo vệ trứng linh thú trong cuộc chiến tranh tông môn"

        pack_a = plan_flow_scenes(topic_mirror)
        pack_b = plan_flow_scenes(topic_beast)

        roles_a = [s.narrative_role for s in pack_a.scenes]
        roles_b = [s.narrative_role for s in pack_b.scenes]

        # At least scenes 2, 4, 5, or 6 should differ by role
        differing_roles = sum(1 for a, b in zip(roles_a, roles_b) if a != b)
        assert differing_roles >= 2, (
            f"Generic topics produce {differing_roles} differing roles (expected >= 2). "
            f"Roles A: {roles_a}, Roles B: {roles_b}. "
            "Generic topics must map to materially different narrative arcs."
        )

    # RED: actions must differ on more than just injected topic string
    def test_generic_topics_produce_different_actions_beyond_string_injection(self):
        """Scenes 5 and 6 must differ between generic topics — they do NOT inject topic string."""
        topic_mirror = "Một đệ tử khám phá cổ kính đảo ngược thời gian tại mộ địa cổ đại"
        topic_beast  = "Một cô nhi bảo vệ trứng linh thú trong cuộc chiến tranh tông môn"

        pack_a = plan_flow_scenes(topic_mirror)
        pack_b = plan_flow_scenes(topic_beast)

        # Scenes 5 and 6 in the generic path do NOT inject topic string
        # If they are identical, the archetype is fully generic and non-diverging
        scene5_a = pack_a.scenes[4].action
        scene5_b = pack_b.scenes[4].action
        scene6_a = pack_a.scenes[5].action
        scene6_b = pack_b.scenes[5].action

        assert scene5_a != scene5_b or scene6_a != scene6_b, (
            "Generic topics must produce at least one materially different action "
            "in scenes 5 or 6 (which do not inject topic string). "
            f"Scene 5 A: {scene5_a[:60]!r} == B: {scene5_b[:60]!r}, "
            f"Scene 6 A: {scene6_a[:60]!r} == B: {scene6_b[:60]!r}. "
            "The generic archetype must do more than inject the topic string."
        )


class TestFlowClipStaging:
    def test_validate_nonexistent_clip(self, tmp_path):
        assert not validate_clip(tmp_path / "nonexistent.mp4")

    def test_validate_empty_clip(self, tmp_path):
        p = tmp_path / "empty.mp4"
        p.write_bytes(b"")
        assert not validate_clip(p)

    def test_stage_empty_dir_raises_error(self, tmp_path):
        with pytest.raises(ValueError, match="Missing required Flow scenes"):
            validate_and_stage_flow_clips(tmp_path / "missing", tmp_path / "staged", expected_scene_count=6)

    def test_stage_missing_scenes_raises_error(self, tmp_path):
        input_dir = tmp_path / "inputs"
        input_dir.mkdir()
        staged_dir = tmp_path / "staged"

        # Create only 4 of 6 scenes
        for i in (1, 2, 3, 4):
            (input_dir / f"scene{i:02d}.mp4").write_bytes(b"dummy video content")

        with patch("auto_video_factory.flow_planner.validate_clip", return_value=True), \
             patch("auto_video_factory.flow_planner.strip_clip_audio", side_effect=lambda inp, out: out.write_bytes(b"clean")):
            with pytest.raises(ValueError, match=r"Missing required Flow scenes: \['scene05.mp4', 'scene06.mp4'\]"):
                validate_and_stage_flow_clips(input_dir, staged_dir, expected_scene_count=6)

    def test_stage_corrupt_clip_raises_error(self, tmp_path):
        input_dir = tmp_path / "inputs"
        input_dir.mkdir()
        staged_dir = tmp_path / "staged"

        for i in range(1, 7):
            (input_dir / f"scene{i:02d}.mp4").write_bytes(b"dummy")

        # Mock validate_clip to fail on scene 3
        def _mock_validate(p: Path) -> bool:
            return "scene03" not in p.name

        with patch("auto_video_factory.flow_planner.validate_clip", side_effect=_mock_validate), \
             patch("auto_video_factory.flow_planner.strip_clip_audio", side_effect=lambda inp, out: out.write_bytes(b"clean")):
            with pytest.raises(ValueError, match="Corrupt or invalid video clip: .*scene03.mp4"):
                validate_and_stage_flow_clips(input_dir, staged_dir, expected_scene_count=6)

    def test_strip_clip_audio_fails_closed_on_ffmpeg_error(self, tmp_path):
        input_clip = tmp_path / "input.mp4"
        input_clip.write_bytes(b"input video with audio")
        output_clip = tmp_path / "output.mp4"

        # Mock ffmpeg failure
        mock_res = MagicMock(returncode=1, stderr="ffmpeg codec error")
        with patch("subprocess.run", return_value=mock_res):
            with pytest.raises(RuntimeError, match="Fail-closed: Audio stripping failed"):
                strip_clip_audio(input_clip, output_clip)

        # Output must NOT exist (never copy original audio-bearing file)
        assert not output_clip.exists()

    def test_topic_materially_changes_scene_prompts(self):
        topic_a = "Một kiếm tu bị trục xuất khỏi Thanh Vân Môn, thức tỉnh kiếm hồn viễn cổ"
        topic_b = "Một nữ đan sư bị hãm hại, luyện thành Cửu Chuyển Kim Đan nghịch thiên cứu thế"

        pack_a = plan_flow_scenes(topic_a)
        pack_b = plan_flow_scenes(topic_b)

        # Topic must drive materially different action and narrative_role fields
        actions_a = [s.action for s in pack_a.scenes]
        actions_b = [s.action for s in pack_b.scenes]
        roles_a = [s.narrative_role for s in pack_a.scenes]
        roles_b = [s.narrative_role for s in pack_b.scenes]

        assert actions_a != actions_b, "Topics must produce different scene actions"
        assert roles_a != roles_b, "Topics must produce different narrative roles"
        # Sword topic: hero scene must be sword-type awakening
        assert any("sword" in a.lower() or "sword soul" in a.lower() or "kiếm" in a.lower()
                   for a in actions_a), "Sword topic must produce sword-related actions"
        # Pill topic: hero scene must be alchemy-type awakening
        assert any("dan" in a.lower() or "pill" in a.lower() or "alchemy" in a.lower() or "furnace" in a.lower()
                   for a in actions_b), "Pill topic must produce alchemy-related actions"

    # -----------------------------------------------------------------------
    # Bug 3 (RED): topic must materially change scene ACTIONS — not just appended
    # -----------------------------------------------------------------------

    def test_topic_changes_scene_actions_not_just_appended(self):
        """Scene narrative_role and action fields must reflect topic, not fixed templates."""
        topic_sword = "Một kiếm tu thức tỉnh kiếm hồn thần cấp"
        topic_pill = "Một đan sư luyện Cửu Chuyển Kim Đan phá thiên kiếp"

        pack_sword = plan_flow_scenes(topic_sword)
        pack_pill = plan_flow_scenes(topic_pill)

        # Actions from pack must NOT be identical across topics
        actions_sword = [s.action for s in pack_sword.scenes]
        actions_pill = [s.action for s in pack_pill.scenes]
        assert actions_sword != actions_pill, (
            "topic must materially change scene actions, not just be appended as string"
        )

    # -----------------------------------------------------------------------
    # Bug 1 (RED): exact filename set — aliases/extras/duplicates must fail
    # -----------------------------------------------------------------------

    def test_exact_filename_set_rejects_alias_names(self, tmp_path):
        """scene_06.mp4 (underscore) must be REJECTED — only exact scene06.mp4 is valid."""
        input_dir = tmp_path / "inputs"
        input_dir.mkdir()
        staged_dir = tmp_path / "staged"
        for i in range(1, 6):
            (input_dir / f"scene{i:02d}.mp4").write_bytes(b"dummy")
        # scene_06.mp4 with underscore — must NOT be accepted as scene06.mp4
        (input_dir / "scene_06.mp4").write_bytes(b"dummy")

        with patch("auto_video_factory.flow_planner.validate_clip", return_value=True), \
             patch("auto_video_factory.flow_planner.strip_clip_audio", side_effect=lambda inp, out: out.write_bytes(b"clean")):
            with pytest.raises(ValueError, match=r"Unexpected mp4 files"):
                validate_and_stage_flow_clips(input_dir, staged_dir, expected_scene_count=6)

    def test_exact_filename_set_rejects_extra_mp4(self, tmp_path):
        """Extra mp4 files beyond the expected set must cause rejection."""
        input_dir = tmp_path / "inputs"
        input_dir.mkdir()
        staged_dir = tmp_path / "staged"
        # Correct 6 files + 1 extra
        for i in range(1, 7):
            (input_dir / f"scene{i:02d}.mp4").write_bytes(b"dummy")
        (input_dir / "bonus_clip.mp4").write_bytes(b"extra")

        with patch("auto_video_factory.flow_planner.validate_clip", return_value=True), \
             patch("auto_video_factory.flow_planner.strip_clip_audio", side_effect=lambda inp, out: out.write_bytes(b"clean")):
            with pytest.raises(ValueError, match="Unexpected mp4 files"):
                validate_and_stage_flow_clips(input_dir, staged_dir, expected_scene_count=6)

    def test_exact_filename_set_rejects_foo_prefix(self, tmp_path):
        """foo_scene06.mp4 must be REJECTED — only exact scene06.mp4 is valid."""
        input_dir = tmp_path / "inputs"
        input_dir.mkdir()
        staged_dir = tmp_path / "staged"
        for i in range(1, 6):
            (input_dir / f"scene{i:02d}.mp4").write_bytes(b"dummy")
        (input_dir / "foo_scene06.mp4").write_bytes(b"dummy")

        with patch("auto_video_factory.flow_planner.validate_clip", return_value=True), \
             patch("auto_video_factory.flow_planner.strip_clip_audio", side_effect=lambda inp, out: out.write_bytes(b"clean")):
            with pytest.raises(ValueError, match=r"Unexpected mp4 files"):
                validate_and_stage_flow_clips(input_dir, staged_dir, expected_scene_count=6)

    def test_exact_filename_set_passes_exact_six(self, tmp_path):
        """Exactly scene01.mp4..scene06.mp4 must succeed."""
        input_dir = tmp_path / "inputs"
        input_dir.mkdir()
        staged_dir = tmp_path / "staged"
        for i in range(1, 7):
            (input_dir / f"scene{i:02d}.mp4").write_bytes(b"dummy")

        with patch("auto_video_factory.flow_planner.validate_clip", return_value=True), \
             patch("auto_video_factory.flow_planner.strip_clip_audio",
                   side_effect=lambda inp, out: (out.write_bytes(b"clean"), out)[1]):
            result = validate_and_stage_flow_clips(input_dir, staged_dir, expected_scene_count=6)
        assert len(result) == 6

    # -----------------------------------------------------------------------
    # Bug 2 (RED): ffprobe failure must fail closed
    # -----------------------------------------------------------------------

    def test_strip_clip_audio_fails_closed_on_ffprobe_unavailable(self, tmp_path):
        """If ffprobe is unavailable (FileNotFoundError), strip must fail closed."""
        input_clip = tmp_path / "input.mp4"
        input_clip.write_bytes(b"video")
        output_clip = tmp_path / "output.mp4"

        def _side_effect(cmd, **kwargs):
            if cmd[0] == "ffmpeg":
                output_clip.write_bytes(b"stripped video")
                return MagicMock(returncode=0, stderr="")
            # ffprobe not available
            raise FileNotFoundError("ffprobe: command not found")

        with patch("subprocess.run", side_effect=_side_effect):
            with pytest.raises(RuntimeError, match="Fail-closed.*ffprobe"):
                strip_clip_audio(input_clip, output_clip)
        assert not output_clip.exists()

    def test_strip_clip_audio_fails_closed_on_malformed_ffprobe_json(self, tmp_path):
        """Malformed ffprobe JSON must cause fail-closed RuntimeError."""
        input_clip = tmp_path / "input.mp4"
        input_clip.write_bytes(b"video")
        output_clip = tmp_path / "output.mp4"

        def _side_effect(cmd, **kwargs):
            if cmd[0] == "ffmpeg":
                output_clip.write_bytes(b"stripped video")
                return MagicMock(returncode=0, stderr="")
            return MagicMock(returncode=0, stdout="NOT VALID JSON {{{{", stderr="")

        with patch("subprocess.run", side_effect=_side_effect):
            with pytest.raises(RuntimeError, match="Fail-closed.*malformed"):
                strip_clip_audio(input_clip, output_clip)
        assert not output_clip.exists()

    def test_strip_clip_audio_fails_closed_on_ffprobe_timeout(self, tmp_path):
        """ffprobe timeout must cause fail-closed RuntimeError."""
        import subprocess as _subprocess
        input_clip = tmp_path / "input.mp4"
        input_clip.write_bytes(b"video")
        output_clip = tmp_path / "output.mp4"

        def _side_effect(cmd, **kwargs):
            if cmd[0] == "ffmpeg":
                output_clip.write_bytes(b"stripped video")
                return MagicMock(returncode=0, stderr="")
            raise _subprocess.TimeoutExpired(cmd, 10)

        with patch("subprocess.run", side_effect=_side_effect):
            with pytest.raises(RuntimeError, match="Fail-closed.*ffprobe"):
                strip_clip_audio(input_clip, output_clip)
        assert not output_clip.exists()

    def test_duration_credit_consistency_all_modes_and_durations(self):
        for dur, expected_scenes in [(45, 6), (60, 6), (90, 9)]:
            for mode in ("flow_balanced", "flow_economy", "flow_quality"):
                pack = plan_flow_scenes("Topic test", duration_seconds=dur, flow_mode=mode)
                assert pack.total_scenes == expected_scenes
                assert len(pack.scenes) == expected_scenes
                # Sum of scene credits must strictly equal pack estimated total credits
                calc_credits = sum(s.estimated_credits for s in pack.scenes)
                assert pack.estimated_total_credits == calc_credits


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

