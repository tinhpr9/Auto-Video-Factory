"""
Input validation and schema enforcement for V5 Auto Affiliate Factory.
"""
from __future__ import annotations

import math
from typing import Any

from .models import ProductInput


class ProductValidationError(ValueError):
    """Raised when ProductInput fails schema or domain validation."""
    pass


def validate_product_input(data: dict[str, Any]) -> ProductInput:
    """
    Validate a raw dictionary and construct a validated ProductInput instance.
    Fail-closed: Rejects missing required fields, nonfinite/negative/boolean prices,
    insufficient features, or prohibited claim overlaps.
    """
    if not isinstance(data, dict):
        raise ProductValidationError("Product input data must be a dictionary.")

    # 1. Required string fields
    for req in ("product_id", "title", "currency"):
        val = data.get(req)
        if not val or not str(val).strip():
            raise ProductValidationError(f"Missing required field: {req}")

    product_id = str(data["product_id"]).strip()
    title = str(data["title"]).strip()
    currency = str(data["currency"]).strip().upper()

    # 2. Price validation — strictly finite non-negative number, no boolean
    price_val = data.get("price")
    if price_val is None:
        raise ProductValidationError("Missing required field: price")
    if isinstance(price_val, bool):
        raise ProductValidationError("Boolean value not allowed for price")

    try:
        price = float(price_val)
        if not math.isfinite(price) or price < 0:
            raise ProductValidationError(f"Finite numeric price required (got {price})")
    except (ValueError, TypeError) as exc:
        raise ProductValidationError(f"Invalid price value '{price_val}': {exc}") from exc

    # 3. Optional string fields
    description = str(data.get("description", "")).strip()
    product_url = str(data.get("product_url", "")).strip()
    affiliate_url = str(data.get("affiliate_url", "")).strip()
    seller_or_brand = str(data.get("seller_or_brand", "")).strip()
    source = str(data.get("source", "manual")).strip()

    # 4. List fields
    def _clean_list(raw_list: Any) -> list[str]:
        if not raw_list:
            return []
        if isinstance(raw_list, (list, tuple)):
            return [str(item).strip() for item in raw_list if str(item).strip()]
        return [str(raw_list).strip()]

    images = _clean_list(data.get("images"))
    videos = _clean_list(data.get("videos"))
    features = _clean_list(data.get("features"))
    verified_claims = _clean_list(data.get("verified_claims"))
    prohibited_or_unverified_claims = _clean_list(data.get("prohibited_or_unverified_claims"))

    # 5. Require at least 2 grounded features or verified claims
    if len(features) + len(verified_claims) < 2:
        raise ProductValidationError(
            "At least 2 verified features or claims required to construct grounded affiliate content."
        )

    # 6. Strict prohibited claim segregation
    prohibited_lowers = [p.lower() for p in prohibited_or_unverified_claims if p]
    for candidate in features + verified_claims:
        cand_lower = candidate.lower()
        for p_low in prohibited_lowers:
            if p_low in cand_lower or cand_lower in p_low:
                raise ProductValidationError(
                    f"Feature/claim overlaps with prohibited list: '{candidate}' matches prohibited '{p_low}'"
                )

    return ProductInput(
        product_id=product_id,
        title=title,
        description=description,
        price=int(price) if price.is_integer() else price,
        currency=currency,
        product_url=product_url,
        affiliate_url=affiliate_url,
        images=images,
        videos=videos,
        seller_or_brand=seller_or_brand,
        features=features,
        verified_claims=verified_claims,
        prohibited_or_unverified_claims=prohibited_or_unverified_claims,
        source=source,
    )
