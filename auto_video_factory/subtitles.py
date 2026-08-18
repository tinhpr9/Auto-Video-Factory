from __future__ import annotations

import textwrap

from .models import SceneTiming


def _stamp(seconds: float) -> str:
    total_ms = max(0, round(seconds * 1000))
    hours, rem = divmod(total_ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, millis = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def captionize_scene(
    timing: SceneTiming,
    *,
    max_chars_per_line: int = 28,
    max_lines: int = 2,
) -> list[SceneTiming]:
    if max_chars_per_line < 8 or max_lines < 1:
        raise ValueError("caption limits are too small")
    if timing.end <= timing.start:
        raise ValueError("caption timing must have positive duration")

    lines = textwrap.wrap(
        " ".join(timing.text.split()),
        width=max_chars_per_line,
        break_long_words=False,
        break_on_hyphens=False,
    )
    if not lines:
        return []
    texts = ["\n".join(lines[i : i + max_lines]) for i in range(0, len(lines), max_lines)]
    weights = [max(1, len(text.replace("\n", " ").replace(" ", ""))) for text in texts]
    total_weight = sum(weights)
    total_duration = timing.end - timing.start

    result: list[SceneTiming] = []
    cursor = timing.start
    for idx, (text, weight) in enumerate(zip(texts, weights)):
        end = timing.end if idx == len(texts) - 1 else cursor + total_duration * weight / total_weight
        result.append(SceneTiming(index=timing.index, start=cursor, end=end, text=text))
        cursor = end
    return result


def build_srt(timings: list[SceneTiming]) -> str:
    blocks: list[str] = []
    for number, timing in enumerate(timings, start=1):
        if timing.end < timing.start:
            raise ValueError("subtitle timing end cannot precede start")
        blocks.append(
            f"{number}\n{_stamp(timing.start)} --> {_stamp(timing.end)}\n{timing.text.strip()}"
        )
    return "\n\n".join(blocks) + ("\n" if blocks else "")
