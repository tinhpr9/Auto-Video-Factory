"""
Eval script for Google Flow Quality Scene Planner contract (Promptfoo compatible).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from auto_video_factory.flow_planner import plan_flow_scenes


def main() -> int:
    context = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    vars_ = context.get("vars", {})
    topic = vars_.get("topic", "Một đệ tử bị tông môn ruồng bỏ, nhiều năm sau thức tỉnh sức mạnh cổ đại và quay trở lại.")
    duration = int(vars_.get("duration", 45))
    flow_mode = vars_.get("flow_mode", "flow_balanced")

    pack = plan_flow_scenes(
        topic,
        duration_seconds=duration,
        flow_mode=flow_mode,
    )
    result = {
        "flow_mode": pack.flow_mode,
        "total_scenes": pack.total_scenes,
        "estimated_total_credits": pack.estimated_total_credits,
        "character_bible_summary": pack.character_bible_summary,
        "scenes": [s.to_dict() for s in pack.scenes],
    }
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
