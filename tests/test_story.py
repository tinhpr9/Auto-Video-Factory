from pathlib import Path
import shutil
import tempfile
import pytest

from auto_video_factory.media import EspeakTTS, media_duration
from auto_video_factory.presets import DURATION_TO_SCENES
from auto_video_factory.story import TemplateStoryPlanner


def test_template_story_planner_creates_eight_numbered_scenes():
    plan = TemplateStoryPlanner(scene_count=8).plan(
        "Một kiếm tu bị tông môn ruồng bỏ nhưng thức tỉnh kiếm ý"
    )
    assert len(plan.scenes) == 8
    assert [scene.index for scene in plan.scenes] == list(range(1, 9))
    assert all(scene.narration.strip() for scene in plan.scenes)
    assert all(scene.visual_prompt.strip() for scene in plan.scenes)
    assert plan.title


def test_story_plan_preserves_topic_in_title():
    topic = "Kiếm tu trở lại sau một trăm năm"
    plan = TemplateStoryPlanner(scene_count=8).plan(topic)
    assert topic in plan.title


@pytest.mark.skipif(shutil.which("espeak") is None or shutil.which("ffprobe") is None, reason="espeak and ffprobe required for TTS duration validation")
@pytest.mark.parametrize(
    ("target_duration", "scene_count", "min_sec", "max_sec"),
    [
        (45, 6, 40.0, 50.0),
        (60, 8, 54.0, 66.0),
        (90, 12, 81.0, 99.0),
    ],
)
def test_template_story_planner_preset_duration_within_tolerance(
    target_duration: int, scene_count: int, min_sec: float, max_sec: float
):
    assert DURATION_TO_SCENES[target_duration] == scene_count
    planner = TemplateStoryPlanner(scene_count=scene_count)
    topic = "Một kiếm tu bị tông môn ruồng bỏ nhưng thức tỉnh kiếm ý"
    plan = planner.plan(topic)
    assert len(plan.scenes) == scene_count

    tts = EspeakTTS(language="vi", speed=165)
    with tempfile.TemporaryDirectory() as tmp_dir:
        total_duration = 0.0
        for scene in plan.scenes:
            audio_path = Path(tmp_dir) / f"scene_{scene.index:02d}.wav"
            tts.synthesize(scene.narration, audio_path)
            total_duration += media_duration(audio_path)

        assert min_sec <= total_duration <= max_sec, (
            f"Target {target_duration}s preset (scene_count={scene_count}) produced "
            f"{total_duration:.2f}s, outside tolerance [{min_sec:.1f}s, {max_sec:.1f}s]"
        )


@pytest.mark.skipif(shutil.which("espeak") is None or shutil.which("ffprobe") is None, reason="espeak and ffprobe required for TTS duration validation")
@pytest.mark.parametrize(
    "topic",
    [
        "Kiếm tu",
        "Một kiếm tu bị tông môn ruồng bỏ nhưng thức tỉnh kiếm ý",
        "Thiên tài đệ nhất kiếm tu trùng sinh sau vạn năm tìm lại tông môn xưa",
    ],
)
def test_template_story_planner_duration_robustness_across_topics(topic: str):
    planner = TemplateStoryPlanner(scene_count=6)
    plan = planner.plan(topic)
    tts = EspeakTTS(language="vi", speed=165)
    with tempfile.TemporaryDirectory() as tmp_dir:
        total_duration = 0.0
        for scene in plan.scenes:
            audio_path = Path(tmp_dir) / f"scene_{scene.index:02d}.wav"
            tts.synthesize(scene.narration, audio_path)
            total_duration += media_duration(audio_path)

        assert 40.0 <= total_duration <= 50.0, (
            f"Topic '{topic}' with 45s preset produced {total_duration:.2f}s, outside [40.0, 50.0]"
        )
