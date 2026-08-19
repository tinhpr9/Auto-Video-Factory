"""
TDD tests for ProductInput contract, validation, and claim safety (V5 Auto Affiliate Factory).
"""
from __future__ import annotations

import math
import pytest

from auto_video_factory.affiliate.models import (
    ClaimType,
    FactClaim,
    ProductInput,
)
from auto_video_factory.affiliate.validation import (
    ProductValidationError,
    validate_product_input,
)


def sample_raw_product() -> dict:
    return {
        "product_id": "PROD-101",
        "title": "Bàn chải điện sóng âm Sonic X1",
        "description": "Bàn chải đánh răng điện sóng âm 40.000 nhịp/phút, pin 30 ngày, chống nước IPX7.",
        "price": 299000,
        "currency": "VND",
        "product_url": "https://example.com/product/sonic-x1",
        "affiliate_url": "https://aff.example.com/click/123",
        "images": ["storage/products/sonic_x1_front.jpg", "storage/products/sonic_x1_side.jpg"],
        "videos": ["storage/products/sonic_x1_demo.mp4"],
        "seller_or_brand": "SonicCare Vietnam",
        "features": [
            "Tần số rung 40.000 nhịp/phút",
            "Thời lượng pin 30 ngày cho một lần sạc",
            "Chuẩn chống nước IPX7",
            "3 chế độ chải răng",
        ],
        "verified_claims": [
            "Chống nước chuẩn IPX7 an toàn dưới vòi nước",
            "Pin dùng được 30 ngày theo công bố nhà sản xuất",
        ],
        "prohibited_or_unverified_claims": [
            "Chữa dứt điểm 100% viêm nướu sau 3 ngày",
            "Trắng răng tức thì chỉ sau 1 lần chải",
        ],
        "source": "manual_import",
    }


# ===========================================================================
# 1. ProductInput Contract & Validation
# ===========================================================================

class TestProductInputContract:
    def test_valid_product_parsed(self):
        raw = sample_raw_product()
        product = validate_product_input(raw)
        assert isinstance(product, ProductInput)
        assert product.product_id == "PROD-101"
        assert product.title == "Bàn chải điện sóng âm Sonic X1"
        assert product.price == 299000
        assert product.currency == "VND"
        assert len(product.features) == 4
        assert len(product.verified_claims) == 2
        assert len(product.prohibited_or_unverified_claims) == 2
        assert len(product.images) == 2
        assert len(product.videos) == 1

    def test_missing_required_fields_raises(self):
        raw = sample_raw_product()
        del raw["title"]
        with pytest.raises(ProductValidationError, match="Missing required field: title"):
            validate_product_input(raw)

    def test_missing_product_id_raises(self):
        raw = sample_raw_product()
        raw["product_id"] = ""
        with pytest.raises(ProductValidationError, match="product_id"):
            validate_product_input(raw)

    def test_invalid_price_raises(self):
        raw = sample_raw_product()
        raw["price"] = -5000
        with pytest.raises(ProductValidationError, match="price"):
            validate_product_input(raw)

    def test_boolean_price_raises(self):
        raw = sample_raw_product()
        raw["price"] = True
        with pytest.raises(ProductValidationError, match="Boolean value not allowed"):
            validate_product_input(raw)

    def test_nan_or_inf_price_raises(self):
        raw = sample_raw_product()
        raw["price"] = float("nan")
        with pytest.raises(ProductValidationError, match="Finite numeric price required"):
            validate_product_input(raw)

        raw["price"] = float("inf")
        with pytest.raises(ProductValidationError, match="Finite numeric price required"):
            validate_product_input(raw)

    def test_invalid_currency_raises(self):
        raw = sample_raw_product()
        raw["currency"] = ""
        with pytest.raises(ProductValidationError, match="currency"):
            validate_product_input(raw)

    def test_missing_all_features_and_claims_raises(self):
        raw = sample_raw_product()
        raw["features"] = []
        raw["verified_claims"] = []
        with pytest.raises(ProductValidationError, match="At least 2 verified features or claims required"):
            validate_product_input(raw)


# ===========================================================================
# 2. Fact Grounding & Claim Filtering
# ===========================================================================

class TestClaimGrounding:
    def test_unverified_claims_strictly_segregated(self):
        raw = sample_raw_product()
        product = validate_product_input(raw)
        facts = product.get_grounded_facts()
        
        # Grounded facts must contain verified claims and features
        for f in facts:
            assert isinstance(f, FactClaim)
            assert f.claim_type in (ClaimType.FACT, ClaimType.FEATURE)
            # Prohibited claims must NEVER be in grounded facts
            assert "Chữa dứt điểm" not in f.text
            assert "Trắng răng tức thì" not in f.text

    def test_overlapping_prohibited_and_features_rejected(self):
        raw = sample_raw_product()
        raw["features"].append("Chữa dứt điểm 100% viêm nướu sau 3 ngày")
        with pytest.raises(ProductValidationError, match="Feature/claim overlaps with prohibited list"):
            validate_product_input(raw)

    def test_is_claim_prohibited(self):
        raw = sample_raw_product()
        product = validate_product_input(raw)
        assert product.is_claim_prohibited("Chữa dứt điểm 100% viêm nướu sau 3 ngày")
        assert product.is_claim_prohibited("Trắng răng tức thì chỉ sau 1 lần chải")
        assert not product.is_claim_prohibited("Chuẩn chống nước IPX7")


# ===========================================================================
# 3. Security & Shell Metacharacter Safety
# ===========================================================================

class TestProductSecurity:
    def test_injection_characters_preserved_as_literal_data(self):
        raw = sample_raw_product()
        raw["title"] = "Bàn chải $(whoami); rm -rf /; `id`"
        raw["description"] = "Test && echo 'leaked' | cat"
        product = validate_product_input(raw)
        # Must be parsed as plain safe string, not stripped into nothing
        assert "$(whoami)" in product.title
        assert "echo 'leaked'" in product.description
