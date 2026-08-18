from auto_video_factory.models import SceneTiming
from auto_video_factory.subtitles import build_srt


def test_build_srt_uses_scene_timings_and_orders_entries():
    timings = [
        SceneTiming(index=1, start=0.0, end=1.25, text="Cảnh thứ nhất"),
        SceneTiming(index=2, start=1.25, end=3.5, text="Cảnh thứ hai"),
    ]
    srt = build_srt(timings)
    assert "1\n00:00:00,000 --> 00:00:01,250\nCảnh thứ nhất" in srt
    assert "2\n00:00:01,250 --> 00:00:03,500\nCảnh thứ hai" in srt

from auto_video_factory.subtitles import captionize_scene


def test_captionize_scene_splits_long_narration_into_short_lower_thirds_units():
    scene = SceneTiming(
        index=1,
        start=2.0,
        end=10.0,
        text=(
            "Mọi chuyện bắt đầu khi một kiếm tu bị tông môn ruồng bỏ nhưng thức tỉnh kiếm ý. "
            "Không ai nghĩ biến cố nhỏ ấy sẽ đổi cả số phận."
        ),
    )
    chunks = captionize_scene(scene, max_chars_per_line=28, max_lines=2)
    assert len(chunks) >= 2
    assert chunks[0].start == 2.0
    assert chunks[-1].end == 10.0
    assert all(chunk.start < chunk.end for chunk in chunks)
    assert all(len(chunk.text.splitlines()) <= 2 for chunk in chunks)
    assert all(len(line) <= 28 for chunk in chunks for line in chunk.text.splitlines())
