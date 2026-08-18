"""
Eval: prove two substantially different topics produce materially different scene actions
on the archetype-specific scenes (not invariant establishing/cultivation scenes).
Run with: UV_LINK_MODE=copy uv run python evals/flow_topic_diversity_eval.py
Exits 0 if PASS, 1 if FAIL.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from auto_video_factory.flow_planner import plan_flow_scenes

# Scenes that are intentionally archetype-invariant (establishing/meditation):
INVARIANT_SCENE_IDS = {1, 3}


def _jaccard(a: str, b: str) -> float:
    tok_a = set(a.lower().split())
    tok_b = set(b.lower().split())
    if not tok_a or not tok_b:
        return 1.0
    return len(tok_a & tok_b) / len(tok_a | tok_b)


def main() -> int:
    topic_sword = "Một kiếm tu bị trục xuất khỏi Thanh Vân Môn, thức tỉnh kiếm hồn viễn cổ"
    topic_pill  = "Một nữ đan sư bị hãm hại, luyện thành Cửu Chuyển Kim Đan nghịch thiên cứu thế"

    pack_sword = plan_flow_scenes(topic_sword)
    pack_pill  = plan_flow_scenes(topic_pill)

    results = []
    variant_fails = []
    for idx, (s_a, s_b) in enumerate(zip(pack_sword.scenes, pack_pill.scenes), start=1):
        sim = _jaccard(s_a.action, s_b.action)
        is_invariant = idx in INVARIANT_SCENE_IDS
        # Invariant scenes (establishing/meditation) may share structure — exempt from check
        different = is_invariant or sim < 0.80
        entry = {
            "scene": idx,
            "invariant": is_invariant,
            "action_sword": s_a.action[:80],
            "action_pill": s_b.action[:80],
            "jaccard_similarity": round(sim, 3),
            "materially_different": different,
        }
        results.append(entry)
        if not different:
            variant_fails.append(entry)

    # Archetype-specific scenes (2, 4, 5, 6) must ALL be materially different
    variant_results = [r for r in results if not r["invariant"]]
    all_variant_pass = all(r["materially_different"] for r in variant_results)

    output = {
        "eval": "flow_topic_diversity",
        "topic_a": topic_sword,
        "topic_b": topic_pill,
        "invariant_scene_ids": sorted(INVARIANT_SCENE_IDS),
        "scenes": results,
        "variant_scene_count": len(variant_results),
        "variant_pass_count": sum(1 for r in variant_results if r["materially_different"]),
        "verdict": "PASS" if all_variant_pass else "FAIL",
        "failures": variant_fails,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if all_variant_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
