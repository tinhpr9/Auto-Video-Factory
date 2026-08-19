"""
Auto Affiliate Factory (V5) — High-converting, fact-grounded affiliate short-video engine.
"""
from .models import (
    AffiliatePack,
    AffiliateScene,
    AffiliateScript,
    AffiliateVariantType,
    ClaimType,
    FactClaim,
    ProductInput,
)
from .validation import (
    ProductValidationError,
    validate_product_input,
)
from .planner import (
    plan_affiliate_pack,
    plan_single_variant,
)
from .media import (
    AffiliateMediaStager,
    InsufficientMediaError,
    prepare_product_media_clips,
)

__all__ = [
    "AffiliatePack",
    "AffiliateScene",
    "AffiliateScript",
    "AffiliateVariantType",
    "ClaimType",
    "FactClaim",
    "ProductInput",
    "ProductValidationError",
    "validate_product_input",
    "plan_affiliate_pack",
    "plan_single_variant",
    "AffiliateMediaStager",
    "InsufficientMediaError",
    "prepare_product_media_clips",
]
