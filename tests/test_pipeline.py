import shutil
from pathlib import Path

import pytest

from auto_video_factory.pipeline import VideoFactory
from auto_video_factory.story import TemplateStoryPlanner
from auto_video_factory.media import CardImageProvider, EspeakTTS, FFmpegRenderer


@pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("espeak") is None, reason="media tools unavailable")
def test_factory_runs_end_to_end(tmp_path: Path):
    factory = VideoFactory(
        planner=TemplateStoryPlanner(scene_count=2),
        tts=EspeakTTS(language="vi", speed=190),
        image_provider=CardImageProvider(width=360, height=640),
        renderer=FFmpegRenderer(width=360, height=640, fps=24),
    )
    result = factory.generate("Kiếm tu trở lại", tmp_path / "job")
    assert result.video.exists()
    assert result.subtitles.exists()
    assert len(result.timings) == 2
    assert result.timings[1].start >= result.timings[0].end


def test_factory_reports_monotonic_progress(tmp_path: Path):
    class FakeTTS:
        def synthesize(self, text: str, output: Path) -> Path:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"RIFFfake")
            return output

    class FakeImage:
        def create(self, scene, output: Path) -> Path:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"png")
            return output

    class FakeRenderer:
        def render(self, images, audios, subtitles, output: Path) -> Path:
            output.write_bytes(b"mp4")
            return output

    import auto_video_factory.pipeline as pipeline_module

    original_duration = pipeline_module.media_duration
    pipeline_module.media_duration = lambda path: 1.0
    try:
        events: list[tuple[int, str]] = []
        factory = VideoFactory(
            planner=TemplateStoryPlanner(scene_count=2),
            tts=FakeTTS(),
            image_provider=FakeImage(),
            renderer=FakeRenderer(),
        )
        factory.generate("Kiếm tu", tmp_path / "progress-job", on_progress=lambda p, m: events.append((p, m)))
    finally:
        pipeline_module.media_duration = original_duration

    assert events[0][0] > 0
    assert events[-1] == (100, "Hoàn tất")
    assert [p for p, _ in events] == sorted(p for p, _ in events)
    assert any("cảnh" in message.lower() for _, message in events)


def test_duplicate_scene_index_rejected_before_media_and_tts_generation(tmp_path: Path):
    from unittest.mock import MagicMock
    from auto_video_factory.models import Scene, StoryPlan

    class DuplicatePlanner:
        def plan(self, topic: str) -> StoryPlan:
            return StoryPlan(
                title="Duplicate Index Test",
                scenes=[
                    Scene(index=1, narration="Scene 1", visual_prompt="p1"),
                    Scene(index=1, narration="Scene 1 duplicate", visual_prompt="p2"),
                ],
            )

    tts_mock = MagicMock()
    image_mock = MagicMock()
    renderer_mock = MagicMock()

    factory = VideoFactory(
        planner=DuplicatePlanner(),
        tts=tts_mock,
        image_provider=image_mock,
        renderer=renderer_mock,
    )

    with pytest.raises(ValueError, match="Duplicate scene index detected"):
        factory.generate("Test topic", tmp_path / "dup-job")

    # Critical requirement: validation MUST occur BEFORE image/TTS calls
    assert image_mock.create.call_count == 0, "image_provider.create must not be called when duplicate scene indices exist"
    assert tts_mock.synthesize.call_count == 0, "tts.synthesize must not be called when duplicate scene indices exist"
    assert renderer_mock.render.call_count == 0


def test_empty_scene_plan_rejected_before_generation(tmp_path: Path):
    from unittest.mock import MagicMock
    from auto_video_factory.models import StoryPlan

    class EmptyPlanner:
        def plan(self, topic: str) -> StoryPlan:
            return StoryPlan(title="Empty Test", scenes=[])

    tts_mock = MagicMock()
    image_mock = MagicMock()
    renderer_mock = MagicMock()

    factory = VideoFactory(
        planner=EmptyPlanner(),
        tts=tts_mock,
        image_provider=image_mock,
        renderer=renderer_mock,
    )

    with pytest.raises(ValueError, match="no scenes"):
        factory.generate("Test topic", tmp_path / "empty-job")

    assert image_mock.create.call_count == 0
    assert tts_mock.synthesize.call_count == 0
    assert renderer_mock.render.call_count == 0


def test_invalid_scene_index_rejected_before_generation(tmp_path: Path):
    from unittest.mock import MagicMock
    from auto_video_factory.models import Scene, StoryPlan

    class InvalidIndexPlanner:
        def plan(self, topic: str) -> StoryPlan:
            return StoryPlan(
                title="Invalid Index Test",
                scenes=[
                    Scene(index=0, narration="Scene 0 invalid", visual_prompt="p0"),
                ],
            )

    tts_mock = MagicMock()
    image_mock = MagicMock()
    renderer_mock = MagicMock()

    factory = VideoFactory(
        planner=InvalidIndexPlanner(),
        tts=tts_mock,
        image_provider=image_mock,
        renderer=renderer_mock,
    )

    with pytest.raises(ValueError, match="Invalid scene index"):
        factory.generate("Test topic", tmp_path / "invalid-index-job")

    assert image_mock.create.call_count == 0
    assert tts_mock.synthesize.call_count == 0
    assert renderer_mock.render.call_count == 0

