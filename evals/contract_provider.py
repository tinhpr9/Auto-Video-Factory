from __future__ import annotations

import json
import re


def call_api(prompt, options, context):
    scene_match = re.search(r"Exactly (\d+) scenes", prompt)
    required = [
        "Return JSON only",
        "character_anchor",
        "visual_prompt",
        "original xianxia-inspired",
        "do not copy named characters",
        "Scene 1 must hook immediately",
        "Visual style for every scene:",
    ]
    if not scene_match or any(item not in prompt for item in required):
        return {"output": "PROMPT_CONTRACT_MISSING"}

    count = int(scene_match.group(1))
    payload = {
        "title": "Kiếm Ý Trong Tuyết",
        "character_anchor": "young adult wanderer, dark teal robe, black hair, silver hair tie",
        "scenes": [
            {
                "narration": f"Cảnh {index} tiếp tục câu chuyện bằng một nhịp kể ngắn gọn.",
                "visual_prompt": f"scene {index}, cinematic cold fantasy composition",
            }
            for index in range(1, count + 1)
        ],
    }
    return {"output": json.dumps(payload, ensure_ascii=False)}
