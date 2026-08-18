#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from auto_video_factory.prompts import build_story_prompt


def main() -> int:
    context = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    vars_ = context.get("vars", {})
    topic = vars_.get("topic", "Một kiếm tu bị ruồng bỏ")
    scene_count = int(vars_.get("scene_count", 8))
    visual_style = vars_.get("visual_style")
    print(build_story_prompt(topic, scene_count, visual_style=visual_style))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
