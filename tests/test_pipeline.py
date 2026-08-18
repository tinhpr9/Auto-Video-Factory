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
