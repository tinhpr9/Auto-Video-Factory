"""
TDD tests for Affiliate Content Planner (V5 Auto Affiliate Factory).
"""
from __future__ import annotations

import pytest

from auto_video_factory.affiliate.models import (
    AffiliatePack,
    AffiliateScript,
    AffiliateVariantType,
    ProductInput,
)
from auto_video_factory.affiliate.planner import (
    plan_affiliate_pack,
    plan_single_variant,
)
from auto_video_factory.affiliate.validation import validate_product_input


def sample_product() -> ProductInput:
    return validate_product_input({
        "product_id": "PROD-202",
        "title": "Bình giữ nhiệt Lock&Lock Wave 500ml",
        "description": "Bình giữ nhiệt thép không gỉ 304, giữ nóng 8 giờ, giữ lạnh 12 giờ, nắp chống tràn.",
        "price": 350000,
        "currency": "VND",
        "product_url": "https://example.com/product/lock-wave-500",
        "affiliate_url": "https://aff.example.com/click/456",
        "images": ["storage/products/bottle_1.jpg", "storage/products/bottle_2.jpg"],
        "videos": ["storage/products/bottle_demo.mp4"],
        "seller_or_brand": "Lock&Lock Official",
        "features": [
            "Chất liệu thép không gỉ 304 an toàn thực phẩm",
            "Giữ nhiệt nóng 8 giờ, giữ lạnh 12 giờ",
            "Nắp silicon chống tràn 100%",
            "Dung tích 500ml nhỏ gọn",
        ],
        "verified_claims": [
            "Giữ nóng 8 tiếng và giữ lạnh 12 tiếng",
            "Chất liệu ruột bình là inox 304 không gỉ",
        ],
        "prohibited_or_unverified_claims": [
            "Giữ đá nguyên khối 7 ngày không tan",
            "Chống va đập từ tầng 10 không móp",
        ],
        "source": "manual_entry",
    })


# ===========================================================================
# 1. Triad Variant Planning
# ===========================================================================

class TestAffiliatePackPlanning:
    def test_plan_generates_exact_three_variants(self):
        product = sample_product()
        pack = plan_affiliate_pack(product, duration_seconds=20)
        assert isinstance(pack, AffiliatePack)
        assert len(pack.variants) == 3
        
        variant_types = {v.variant_type for v in pack.variants}
        assert variant_types == {
            AffiliateVariantType.PROBLEM_SOLUTION,
            AffiliateVariantType.THREE_REASONS,
            AffiliateVariantType.COMPARISON_HIGHLIGHT,
        }

    def test_variants_materially_differ(self):
        product = sample_product()
        pack = plan_affiliate_pack(product, duration_seconds=20)
        v_prob = pack.get_variant(AffiliateVariantType.PROBLEM_SOLUTION)
        v_three = pack.get_variant(AffiliateVariantType.THREE_REASONS)
        v_comp = pack.get_variant(AffiliateVariantType.COMPARISON_HIGHLIGHT)

        # Hooks must be completely distinct
        assert v_prob.hook != v_three.hook
        assert v_three.hook != v_comp.hook
        assert v_prob.hook != v_comp.hook

        # Voice scripts must be distinct
        assert v_prob.voice_script != v_three.voice_script
        assert v_three.voice_script != v_comp.voice_script


# ===========================================================================
# 2. Fact Grounding & Anti-Hallucination
# ===========================================================================

class TestPlannerFactGrounding:
    def test_all_points_grounded_in_product_facts(self):
        product = sample_product()
        pack = plan_affiliate_pack(product, duration_seconds=20)
        for variant in pack.variants:
            assert isinstance(variant, AffiliateScript)
            assert len(variant.verified_product_points) >= 2
            # No unverified or prohibited claims entered the script
            for prohibited in product.prohibited_or_unverified_claims:
                assert prohibited.lower() not in variant.voice_script.lower()
                assert prohibited.lower() not in " ".join(variant.on_screen_text).lower()

    def test_no_invented_discount_or_ratings(self):
        product = sample_product()
        pack = plan_affiliate_pack(product, duration_seconds=20)
        for variant in pack.variants:
            script_lower = variant.voice_script.lower()
            # Must not invent fake discount percentages not present in product
            assert "giảm 50%" not in script_lower
            assert "giảm 70%" not in script_lower
            assert "5 sao" not in script_lower
            assert "hàng triệu người" not in script_lower


# ===========================================================================
# 3. Vietnamese Script Quality & Duration Constraints
# ===========================================================================

class TestScriptQuality:
    @pytest.mark.parametrize("duration", [15, 20, 30])
    def test_script_duration_and_word_budget(self, duration):
        product = sample_product()
        pack = plan_affiliate_pack(product, duration_seconds=duration)
        for variant in pack.variants:
            words = variant.voice_script.split()
            word_count = len(words)
            # 15s: 30-55 words; 20s: 40-70 words; 30s: 60-100 words
            min_w = int(duration * 2.0)
            max_w = int(duration * 3.6)
            assert min_w <= word_count <= max_w, (
                f"Variant {variant.variant_type} word count {word_count} outside [{min_w}, {max_w}] for {duration}s"
            )
            # Complete sentence with terminal punctuation
            assert variant.voice_script.strip().endswith((".", "!", "?", "”", '"'))

    def test_cta_presence(self):
        product = sample_product()
        pack = plan_affiliate_pack(product, duration_seconds=20)
        for variant in pack.variants:
            assert variant.cta
            assert len(variant.cta) >= 5
            assert variant.cta.lower() in variant.voice_script.lower()
