"""
Eval script for Gemini Video Provider scene planner contract.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from auto_video_factory.visual_provider import GeminiVideoProvider


def main() -> int:
    context = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    vars_ = context.get("vars", {})
    topic = vars_.get("topic", "Một đệ tử bị tông môn ruồng bỏ, thức tỉnh sức mạnh cổ đại")
    duration = int(vars_.get("duration", 45))
    quality_mode = vars_.get("quality_mode", "economy")

    provider = GeminiVideoProvider()
    scenes = provider.plan_scenes(
        topic=topic,
        duration_seconds=duration,
        quality_mode=quality_mode,
    )
    result = {
        "model": provider.model_name,
        "scene_count": len(scenes),
        "total_planned_seconds": sum(s.duration_target for s in scenes),
        "scenes": [s.to_dict() for s in scenes],
    }
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
