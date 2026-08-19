"""
Input validation and schema enforcement for V5 Auto Affiliate Factory.
"""
from __future__ import annotations

from typing import Any

from .models import ProductInput


class ProductValidationError(ValueError):
    """Raised when ProductInput fails schema or domain validation."""
    pass


def validate_product_input(data: dict[str, Any]) -> ProductInput:
    """
    Validate a raw dictionary and construct a validated ProductInput instance.
    Fail-closed: Rejects missing required fields, negative prices, or invalid schemas.
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

    # 2. Price validation
    price_val = data.get("price")
    if price_val is None:
        raise ProductValidationError("Missing required field: price")
    try:
        price = float(price_val)
        if price < 0:
            raise ProductValidationError(f"price cannot be negative: {price}")
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
