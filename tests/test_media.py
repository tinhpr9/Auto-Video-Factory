import json
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from auto_video_factory.media import CardImageProvider, EspeakTTS, FFmpegRenderer
from auto_video_factory.models import Scene


@pytest.mark.skipif(shutil.which("espeak") is None, reason="espeak unavailable")
def test_espeak_tts_creates_nonempty_wav(tmp_path: Path):
    out = tmp_path / "voice.wav"
    EspeakTTS(language="vi").synthesize("Đây là một câu thử nghiệm.", out)
    assert out.exists()
    assert out.stat().st_size > 1000


def test_card_image_provider_creates_vertical_scene(tmp_path: Path):
    out = tmp_path / "scene.png"
    scene = Scene(index=1, narration="Một người bước qua tuyết.", visual_prompt="núi tuyết")
    CardImageProvider(width=720, height=1280).create(scene, out)
    with Image.open(out) as image:
        assert image.size == (720, 1280)


@pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="ffmpeg unavailable")
def test_renderer_produces_vertical_mp4_with_audio(tmp_path: Path):
    tts = EspeakTTS(language="vi")
    image_provider = CardImageProvider(width=360, height=640)

    scene1 = Scene(index=1, narration="Tuyết phủ kín sơn môn.", visual_prompt="núi tuyết")
    scene2 = Scene(index=2, narration="Một kiếm tu lặng lẽ quay về.", visual_prompt="kiếm tu")
    images = []
    audios = []
    for scene in (scene1, scene2):
        image = tmp_path / f"scene-{scene.index}.png"
        audio = tmp_path / f"scene-{scene.index}.wav"
        image_provider.create(scene, image)
        tts.synthesize(scene.narration, audio)
        images.append(image)
        audios.append(audio)

    out = tmp_path / "video.mp4"
    srt = tmp_path / "captions.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:10,000\nTuyết phủ kín sơn môn.\n", encoding="utf-8")
    FFmpegRenderer(width=360, height=640, fps=24).render(images, audios, srt, out)

    assert out.exists() and out.stat().st_size > 10_000
    probe = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_streams", "-of", "json", str(out)
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    streams = json.loads(probe.stdout)["streams"]
    video = next(s for s in streams if s["codec_type"] == "video")
    assert (video["width"], video["height"]) == (360, 640)
    assert any(s["codec_type"] == "audio" for s in streams)
