from __future__ import annotations

import json
import math
import shutil
import subprocess
import textwrap
from pathlib import Path
from typing import Sequence
try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    Image = None
    ImageDraw = None
    ImageFont = None

from .models import Scene


def _run(command: Sequence[str]) -> None:
    subprocess.run(list(command), check=True, capture_output=True, text=True)


def _require(binary: str) -> str:
    path = shutil.which(binary)
    if path is None:
        raise RuntimeError(f"required executable not found: {binary}")
    return path


def media_duration(path: Path) -> float:
    _require("ffprobe")
    proc = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "json", str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    duration = float(json.loads(proc.stdout)["format"]["duration"])
    if duration <= 0:
        raise RuntimeError(f"media has invalid duration: {path}")
    return duration


class EspeakTTS:
    def __init__(self, language: str = "vi", speed: int = 165) -> None:
        self.language = language
        self.speed = speed

    def synthesize(self, text: str, output: Path) -> Path:
        espeak = _require("espeak")
        output.parent.mkdir(parents=True, exist_ok=True)
        _run([espeak, "-v", self.language, "-s", str(self.speed), "-w", str(output), text])
        return output


class CardImageProvider:
    """Dependency-free visual provider used for local/CI proof of the pipeline.

    It creates original atmospheric cards, not copies of the reference video.
    A generative image provider can later implement the same create() contract.
    """

    def __init__(self, width: int = 720, height: int = 1280) -> None:
        self.width = width
        self.height = height

    def _font(self, size: int) -> ImageFont.ImageFont:
        for candidate in (
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/dejavu/DejaVuSans.ttf",
        ):
            if Path(candidate).exists():
                return ImageFont.truetype(candidate, size=size)
        return ImageFont.load_default()

    def create(self, scene: Scene, output: Path) -> Path:
        if Image is None:
            raise RuntimeError("Pillow is required for CardImageProvider. Install with: pip install pillow")
        output.parent.mkdir(parents=True, exist_ok=True)
        image = Image.new("RGB", (self.width, self.height))
        pixels = image.load()
        for y in range(self.height):
            t = y / max(1, self.height - 1)
            r = int(224 - 56 * t)
            g = int(244 - 70 * t)
            b = int(250 - 48 * t)
            for x in range(self.width):
                pixels[x, y] = (r, g, b)

        draw = ImageDraw.Draw(image, "RGBA")
        horizon = int(self.height * 0.56)
        mountain_layers = [
            (0.27, (90, 125, 145, 80)),
            (0.42, (70, 105, 130, 95)),
            (0.58, (50, 85, 115, 115)),
        ]
        for layer, color in mountain_layers:
            y_base = int(self.height * (0.62 + layer * 0.12))
            step = max(80, self.width // 4)
            points = [(0, y_base)]
            for x in range(0, self.width + step, step):
                peak = y_base - int(self.height * (0.11 + 0.06 * math.sin((x + scene.index * 43) / 120)))
                points.extend([(x, y_base), (x + step // 2, peak)])
            points.extend([(self.width, self.height), (0, self.height)])
            draw.polygon(points, fill=color)

        # Original generic robed silhouette.
        cx = int(self.width * (0.48 + 0.03 * math.sin(scene.index)))
        head_y = int(self.height * 0.36)
        head_r = max(12, self.width // 28)
        draw.ellipse((cx - head_r, head_y - head_r, cx + head_r, head_y + head_r), fill=(35, 50, 65, 210))
        draw.polygon(
            [
                (cx - int(self.width * 0.08), head_y + head_r),
                (cx + int(self.width * 0.07), head_y + head_r),
                (cx + int(self.width * 0.14), horizon + int(self.height * 0.20)),
                (cx - int(self.width * 0.15), horizon + int(self.height * 0.20)),
            ],
            fill=(28, 42, 58, 200),
        )
        draw.line((cx - 5, head_y + head_r, cx + int(self.width * 0.19), horizon), fill=(235, 245, 250, 170), width=max(2, self.width // 180))

        # Soft mist bands.
        for offset in (0.50, 0.63, 0.77):
            y = int(self.height * offset)
            draw.ellipse((-self.width * 0.2, y, self.width * 1.2, y + self.height * 0.09), fill=(245, 252, 255, 55))

        label_font = self._font(max(14, self.width // 28))
        small_font = self._font(max(11, self.width // 42))
        draw.rounded_rectangle(
            (int(self.width * 0.06), int(self.height * 0.07), int(self.width * 0.94), int(self.height * 0.20)),
            radius=max(12, self.width // 40),
            fill=(12, 26, 38, 92),
        )
        draw.text((int(self.width * 0.09), int(self.height * 0.09)), f"CẢNH {scene.index}", font=label_font, fill=(255, 255, 255, 235))
        wrapped = textwrap.fill(scene.visual_prompt.split(",")[0], width=34)
        draw.multiline_text((int(self.width * 0.09), int(self.height * 0.145)), wrapped, font=small_font, fill=(235, 247, 252, 220), spacing=3)
        image.save(output, quality=95)
        return output


class FFmpegRenderer:
    def __init__(self, width: int = 720, height: int = 1280, fps: int = 30) -> None:
        self.width = width
        self.height = height
        self.fps = fps

    @staticmethod
    def _concat_file(paths: Sequence[Path], destination: Path) -> Path:
        lines = []
        for path in paths:
            escaped = str(path.resolve()).replace("'", "'\\''")
            lines.append(f"file '{escaped}'")
        destination.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return destination

    def render(self, images: Sequence[Path], audios: Sequence[Path], subtitles: Path, output: Path) -> Path:
        ffmpeg = _require("ffmpeg")
        if len(images) != len(audios) or not images:
            raise ValueError("images and audios must be non-empty and have equal length")
        output.parent.mkdir(parents=True, exist_ok=True)
        work = output.parent / ".render-work"
        work.mkdir(parents=True, exist_ok=True)
        clips: list[Path] = []

        for index, (image, audio) in enumerate(zip(images, audios), start=1):
            duration = media_duration(audio)
            clip = work / f"clip-{index:02d}.mp4"
            frames = max(1, math.ceil(duration * self.fps))
            if image.suffix.lower() in (".mp4", ".mov", ".mkv", ".webm"):
                vf = (
                    f"scale={self.width}:{self.height}:force_original_aspect_ratio=increase,"
                    f"crop={self.width}:{self.height},"
                    f"format=yuv420p"
                )
                _run([
                    ffmpeg, "-y", "-stream_loop", "-1", "-i", str(image), "-i", str(audio),
                    "-vf", vf, "-t", f"{duration:.3f}", "-r", str(self.fps),
                    "-map", "0:v:0", "-map", "1:a:0",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "24",
                    "-c:a", "aac", "-b:a", "128k", "-shortest", str(clip),
                ])
            else:
                # Subtle zoom/pan keeps still illustrations alive without copying reference assets.
                vf = (
                    f"scale={self.width}:{self.height}:force_original_aspect_ratio=increase,"
                    f"crop={self.width}:{self.height},"
                    f"zoompan=z='min(zoom+0.0007,1.06)':d={frames}:"
                    f"s={self.width}x{self.height}:fps={self.fps},format=yuv420p"
                )
                _run([
                    ffmpeg, "-y", "-loop", "1", "-i", str(image), "-i", str(audio),
                    "-vf", vf, "-t", f"{duration:.3f}", "-r", str(self.fps),
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "24",
                    "-c:a", "aac", "-b:a", "128k", "-shortest", str(clip),
                ])
            clips.append(clip)

        concat_list = self._concat_file(clips, work / "clips.txt")
        joined = work / "joined.mp4"
        _run([ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list), "-c", "copy", str(joined)])

        # Burn centered lower subtitles. ffmpeg/libass handles Vietnamese Unicode.
        subtitle_filter = (
            f"subtitles={str(subtitles.resolve()).replace(':', r'\:')}:"
            "force_style='FontName=DejaVu Sans,FontSize=9,"
            "PrimaryColour=&H00FFFFFF,OutlineColour=&H00101010,"
            "BorderStyle=1,Outline=1.4,Shadow=0,Alignment=2,MarginV=18'"
        )
        _run([
            ffmpeg, "-y", "-i", str(joined), "-vf", subtitle_filter,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-c:a", "copy", "-movflags", "+faststart", str(output),
        ])
        shutil.rmtree(work, ignore_errors=True)
        return output
