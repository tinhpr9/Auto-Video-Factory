from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Scene:
    index: int
    narration: str
    visual_prompt: str


@dataclass(frozen=True)
class StoryPlan:
    title: str
    scenes: list[Scene]


@dataclass(frozen=True)
class SceneTiming:
    index: int
    start: float
    end: float
    text: str


@dataclass(frozen=True)
class GenerationResult:
    video: Path
    subtitles: Path
    timings: list[SceneTiming]
    plan: StoryPlan
