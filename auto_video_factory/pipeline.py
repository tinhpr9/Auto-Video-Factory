from __future__ import annotations

from pathlib import Path
from typing import Callable, Protocol

from .media import media_duration
from .models import GenerationResult, Scene, SceneTiming, StoryPlan
from .subtitles import build_srt, captionize_scene


class Planner(Protocol):
    def plan(self, topic: str) -> StoryPlan: ...


class TTSProvider(Protocol):
    def synthesize(self, text: str, output: Path) -> Path: ...


class ImageProvider(Protocol):
    def create(self, scene: Scene, output: Path) -> Path: ...


class Renderer(Protocol):
    def render(self, images: list[Path], audios: list[Path], subtitles: Path, output: Path) -> Path: ...


ProgressCallback = Callable[[int, str], None]


class VideoFactory:
    def __init__(self, planner: Planner, tts: TTSProvider, image_provider: ImageProvider, renderer: Renderer) -> None:
        self.planner = planner
        self.tts = tts
        self.image_provider = image_provider
        self.renderer = renderer

    def generate(
        self,
        topic: str,
        job_dir: Path,
        *,
        on_progress: ProgressCallback | None = None,
    ) -> GenerationResult:
        def report(percent: int, message: str) -> None:
            if on_progress is not None:
                on_progress(max(0, min(100, percent)), message)

        job_dir.mkdir(parents=True, exist_ok=True)
        scene_dir = job_dir / "scenes"
        scene_dir.mkdir(exist_ok=True)
        report(5, "Đang viết kịch bản")
        plan = self.planner.plan(topic)
        report(12, "Đã có kịch bản")

        images: list[Path] = []
        audios: list[Path] = []
        timings: list[SceneTiming] = []
        cursor = 0.0
        scene_total = max(1, len(plan.scenes))
        for position, scene in enumerate(plan.scenes, start=1):
            media_path = scene_dir / f"scene-{scene.index:02d}.png"
            audio = scene_dir / f"scene-{scene.index:02d}.wav"
            created_media = self.image_provider.create(scene, media_path)
            actual_media = created_media if isinstance(created_media, Path) and created_media.exists() else media_path
            self.tts.synthesize(scene.narration, audio)
            duration = media_duration(audio)
            timings.append(
                SceneTiming(
                    index=scene.index,
                    start=cursor,
                    end=cursor + duration,
                    text=scene.narration,
                )
            )
            cursor += duration
            images.append(actual_media)
            audios.append(audio)
            percent = 12 + round(68 * position / scene_total)
            report(percent, f"Đang tạo cảnh {position}/{scene_total}")

        subtitles = job_dir / "captions.srt"
        caption_timings = [caption for timing in timings for caption in captionize_scene(timing)]
        subtitles.write_text(build_srt(caption_timings), encoding="utf-8")
        report(84, "Đang render video")
        video = job_dir / "video.mp4"
        self.renderer.render(images, audios, subtitles, video)
        report(100, "Hoàn tất")
        return GenerationResult(video=video, subtitles=subtitles, timings=timings, plan=plan)
