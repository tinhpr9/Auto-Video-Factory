"""
Prompt & Planning Evaluation Harness for V5 Auto Affiliate Factory.
Verifies factual grounding, anti-hallucination, prohibited claim isolation, triad diversity, Vietnamese fluency, and CTA presence.
"""
from __future__ import annotations

import json
import sys
from typing import Any

from auto_video_factory.affiliate.models import (
    AffiliateVariantType,
    ProductInput,
)
from auto_video_factory.affiliate.planner import plan_affiliate_pack
from auto_video_factory.affiliate.validation import validate_product_input


def evaluate_affiliate_pack(product_dict: dict[str, Any], duration: int = 20) -> dict[str, Any]:
    product = validate_product_input(product_dict)
    pack = plan_affiliate_pack(product, duration_seconds=duration)

    results = {
        "product_id": product.product_id,
        "variant_count": len(pack.variants),
        "checks": {},
        "passed": True,
    }

    # 1. Check: Exactly 3 distinct variants
    v_types = [v.variant_type.value for v in pack.variants]
    results["checks"]["exact_3_variants"] = (
        len(pack.variants) == 3
        and set(v_types) == {"problem_solution", "three_reasons", "comparison_highlight"}
    )

    # 2. Check: Hook and script diversity
    hooks = [v.hook for v in pack.variants]
    results["checks"]["hook_diversity"] = len(set(hooks)) == 3

    scripts = [v.voice_script for v in pack.variants]
    results["checks"]["script_diversity"] = len(set(scripts)) == 3

    # 3. Check: Fact grounding & anti-hallucination against both generic terms & product-specific prohibited claims
    hallucination_detected = False
    generic_forbidden = [
        "giảm 50%", "giảm 70%", "giảm 80%", "5 sao", "hàng triệu người",
        "chữa dứt điểm", "trắng răng tức thì", "trị khỏi 100%", "tốt nhất thế giới",
        "dễ hỏng", "hoàn toàn an tâm", "cực kỳ bền bỉ",
    ]
    product_prohibited = [p.lower() for p in product.prohibited_or_unverified_claims if p]

    for v in pack.variants:
        s_lower = v.voice_script.lower()
        ost_lower = " ".join(v.on_screen_text).lower()

        for term in generic_forbidden:
            if term in s_lower or term in ost_lower:
                hallucination_detected = True
                break

        for prohib in product_prohibited:
            if prohib in s_lower or prohib in ost_lower:
                hallucination_detected = True
                break

    results["checks"]["no_hallucinations"] = not hallucination_detected

    # 4. Check: Vietnamese fluency & terminal punctuation
    all_terminal = all(v.voice_script.strip().endswith((".", "!", "?", "”", '"')) for v in pack.variants)
    results["checks"]["terminal_punctuation"] = all_terminal

    # 5. Check: CTA presence
    all_cta = all(len(v.cta) >= 5 and v.cta.lower() in v.voice_script.lower() for v in pack.variants)
    results["checks"]["cta_present"] = all_cta

    # Overall pass
    results["passed"] = all(results["checks"].values())
    return results


def main() -> int:
    sample_input = {
        "product_id": "EVAL-01",
        "title": "Nồi cơm điện cao tần IH Cuckoo 1.8L",
        "description": "Nồi cơm điện cao tần lòng nồi gang phủ men gốm Xwall, 16 chế độ nấu.",
        "price": 4200000,
        "currency": "VND",
        "images": ["img_cuckoo.jpg"],
        "features": [
            "Công nghệ đốt nóng trong IH cao tần",
            "Lòng nồi gang phủ men gốm",
            "16 chế độ nấu đa dạng",
        ],
        "verified_claims": [
            "Công nghệ IH giúp hạt cơm chín đều",
        ],
        "prohibited_or_unverified_claims": [
            "Cơm để 1 tháng không ôi thiu",
            "Giảm 90% lượng đường trong cơm",
        ],
    }

    if len(sys.argv) > 1 and sys.argv[1].startswith("{"):
        data = json.loads(sys.argv[1])
        product_data = data.get("vars", {}).get("product", sample_input)
    else:
        product_data = sample_input

    eval_res = evaluate_affiliate_pack(product_data, duration=20)
    print(json.dumps(eval_res, ensure_ascii=False, indent=2))

    return 0 if eval_res["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
