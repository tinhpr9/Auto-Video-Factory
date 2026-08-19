"""
Data models and contracts for V5 Auto Affiliate Factory.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ClaimType(str, Enum):
    FACT = "fact"
    FEATURE = "feature"
    INFERENCE = "inference"
    MARKETING_COPY = "marketing_copy"
    PROHIBITED = "prohibited"


@dataclass(frozen=True)
class FactClaim:
    text: str
    claim_type: ClaimType
    source: str = "product_input"

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "claim_type": self.claim_type.value,
            "source": self.source,
        }


@dataclass(frozen=True)
class ProductInput:
    product_id: str
    title: str
    description: str
    price: float | int
    currency: str
    product_url: str = ""
    affiliate_url: str = ""
    images: list[str] = field(default_factory=list)
    videos: list[str] = field(default_factory=list)
    seller_or_brand: str = ""
    features: list[str] = field(default_factory=list)
    verified_claims: list[str] = field(default_factory=list)
    prohibited_or_unverified_claims: list[str] = field(default_factory=list)
    source: str = "manual"

    def get_grounded_facts(self) -> list[FactClaim]:
        facts: list[FactClaim] = []
        for feat in self.features:
            facts.append(FactClaim(text=feat.strip(), claim_type=ClaimType.FEATURE, source="features"))
        for claim in self.verified_claims:
            facts.append(FactClaim(text=claim.strip(), claim_type=ClaimType.FACT, source="verified_claims"))
        return facts

    def is_claim_prohibited(self, claim: str) -> bool:
        c_clean = claim.strip().lower()
        for p in self.prohibited_or_unverified_claims:
            p_clean = p.strip().lower()
            if p_clean and (p_clean in c_clean or c_clean == p_clean):
                return True
        return False

    def to_dict(self) -> dict[str, Any]:
        return {
            "product_id": self.product_id,
            "title": self.title,
            "description": self.description,
            "price": self.price,
            "currency": self.currency,
            "product_url": self.product_url,
            "affiliate_url": self.affiliate_url,
            "images": list(self.images),
            "videos": list(self.videos),
            "seller_or_brand": self.seller_or_brand,
            "features": list(self.features),
            "verified_claims": list(self.verified_claims),
            "prohibited_or_unverified_claims": list(self.prohibited_or_unverified_claims),
            "source": self.source,
        }


class AffiliateVariantType(str, Enum):
    PROBLEM_SOLUTION = "problem_solution"
    THREE_REASONS = "three_reasons"
    COMPARISON_HIGHLIGHT = "comparison_highlight"


@dataclass(frozen=True)
class AffiliateScene:
    scene_index: int
    scene_role: str  # "hook", "problem", "solution_feature", "proof_benefit", "cta"
    visual_media_path: str = ""
    on_screen_text: str = ""
    voice_segment: str = ""
    duration_seconds: float = 5.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "scene_index": self.scene_index,
            "scene_role": self.scene_role,
            "visual_media_path": self.visual_media_path,
            "on_screen_text": self.on_screen_text,
            "voice_segment": self.voice_segment,
            "duration_seconds": self.duration_seconds,
        }


@dataclass(frozen=True)
class AffiliateScript:
    variant_type: AffiliateVariantType
    title: str
    hook: str
    audience_problem: str
    verified_product_points: list[str]
    voice_script: str
    on_screen_text: list[str]
    cta: str
    risk_flags: list[str] = field(default_factory=list)
    scenes: list[AffiliateScene] = field(default_factory=list)
    target_duration_seconds: int = 20

    @property
    def word_count(self) -> int:
        return len([w for w in self.voice_script.split() if w])

    def to_dict(self) -> dict[str, Any]:
        return {
            "variant_type": self.variant_type.value,
            "title": self.title,
            "hook": self.hook,
            "audience_problem": self.audience_problem,
            "verified_product_points": list(self.verified_product_points),
            "voice_script": self.voice_script,
            "word_count": self.word_count,
            "on_screen_text": list(self.on_screen_text),
            "cta": self.cta,
            "risk_flags": list(self.risk_flags),
            "scenes": [s.to_dict() for s in self.scenes],
            "target_duration_seconds": self.target_duration_seconds,
        }


@dataclass(frozen=True)
class AffiliatePack:
    product: ProductInput
    variants: list[AffiliateScript]

    def get_variant(self, variant_type: AffiliateVariantType | str) -> AffiliateScript:
        vt = variant_type.value if isinstance(variant_type, AffiliateVariantType) else str(variant_type)
        for v in self.variants:
            if v.variant_type.value == vt:
                return v
        raise ValueError(f"Variant '{vt}' not found in AffiliatePack.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "product": self.product.to_dict(),
            "variants": [v.to_dict() for v in self.variants],
        }
