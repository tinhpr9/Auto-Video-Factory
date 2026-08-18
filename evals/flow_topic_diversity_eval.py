"""
Eval: prove multiple topic categories produce materially different scene actions/roles.

Validates:
1. Archetype-specific topics (sword vs pill) — scenes 2,4,5,6 must differ
2. Generic topics (mirror vs spirit beast) — roles AND scenes 5,6 must differ
   (not just topic string injected at scenes 2,4)

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

# Scenes that are intentionally archetype-invariant (establishing/cultivation):
INVARIANT_SCENE_IDS = {1, 3}


def _jaccard(a: str, b: str) -> float:
    tok_a = set(a.lower().split())
    tok_b = set(b.lower().split())
    if not tok_a or not tok_b:
        return 1.0
    return len(tok_a & tok_b) / len(tok_a | tok_b)


def run_case(label: str, topic_a: str, topic_b: str, check_roles: bool = False) -> dict:
    pack_a = plan_flow_scenes(topic_a)
    pack_b = plan_flow_scenes(topic_b)

    results = []
    variant_fails = []
    for idx, (sa, sb) in enumerate(zip(pack_a.scenes, pack_b.scenes), start=1):
        is_invariant = idx in INVARIANT_SCENE_IDS
        sim = _jaccard(sa.action, sb.action)
        role_same = sa.narrative_role == sb.narrative_role
        different = is_invariant or sim < 0.80
        entry = {
            "scene": idx,
            "invariant": is_invariant,
            "role_a": sa.narrative_role,
            "role_b": sb.narrative_role,
            "role_same": role_same,
            "action_a": sa.action[:70],
            "action_b": sb.action[:70],
            "jaccard": round(sim, 3),
            "materially_different": different,
        }
        results.append(entry)
        if not different:
            variant_fails.append(entry)

    variant_results = [r for r in results if not r["invariant"]]
    all_variant_pass = all(r["materially_different"] for r in variant_results)

    # For generic topics: also check that roles differ in at least 2 variant scenes
    role_pass = True
    if check_roles:
        differing_roles = sum(1 for r in variant_results if not r["role_same"])
        role_pass = differing_roles >= 2

    return {
        "label": label,
        "topic_a": topic_a,
        "topic_b": topic_b,
        "scenes": results,
        "variant_scene_count": len(variant_results),
        "variant_pass_count": sum(1 for r in variant_results if r["materially_different"]),
        "role_pass": role_pass,
        "action_pass": all_variant_pass,
        "verdict": "PASS" if (all_variant_pass and role_pass) else "FAIL",
        "failures": variant_fails,
    }


def main() -> int:
    cases = [
        run_case(
            "archetype_sword_vs_pill",
            "Một kiếm tu bị trục xuất khỏi Thanh Vân Môn, thức tỉnh kiếm hồn viễn cổ",
            "Một nữ đan sư bị hãm hại, luyện thành Cửu Chuyển Kim Đan nghịch thiên cứu thế",
            check_roles=True,
        ),
        run_case(
            "generic_mirror_vs_spirit_beast",
            "Một đệ tử khám phá cổ kính đảo ngược thời gian tại mộ địa cổ đại",
            "Một cô nhi bảo vệ trứng linh thú trong cuộc chiến tranh tông môn",
            check_roles=True,
        ),
    ]

    all_pass = all(c["verdict"] == "PASS" for c in cases)
    output = {
        "eval": "flow_topic_diversity",
        "invariant_scene_ids": sorted(INVARIANT_SCENE_IDS),
        "cases": cases,
        "overall_verdict": "PASS" if all_pass else "FAIL",
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if all_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
