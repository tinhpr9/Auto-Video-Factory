"""
Creative Angle & Content Planner for V5 Auto Affiliate Factory.
Generates 3 distinct, fact-grounded Vietnamese short-video scripts (15, 20, or 30s).
"""
from __future__ import annotations

from typing import Sequence

from .models import (
    AffiliatePack,
    AffiliateScene,
    AffiliateScript,
    AffiliateVariantType,
    ProductInput,
)

SUPPORTED_DURATIONS = (15, 20, 30)


def _format_price(price: float | int, currency: str) -> str:
    if currency.upper() == "VND":
        return f"{int(price):,}đ".replace(",", ".")
    return f"{price} {currency}"


def plan_single_variant(
    product: ProductInput,
    variant_type: AffiliateVariantType,
    *,
    duration_seconds: int = 20,
) -> AffiliateScript:
    """
    Plan a single fact-grounded affiliate script variant for the specified duration.
    Fails closed if duration is not 15, 20, or 30 seconds, or if grounded features are missing.
    """
    if duration_seconds not in SUPPORTED_DURATIONS:
        raise ValueError(
            f"Unsupported duration {duration_seconds}s. Only {SUPPORTED_DURATIONS} seconds are supported."
        )

    # Strictly consume verified features and claims without inventing placeholders
    grounded_points = product.features + product.verified_claims
    if len(grounded_points) < 2:
        raise ValueError(
            f"Insufficient grounded features for product '{product.product_id}'. Need at least 2."
        )

    feat1 = grounded_points[0]
    feat2 = grounded_points[1]
    feat3 = grounded_points[2] if len(grounded_points) > 2 else feat1

    brand_clause = f" của {product.seller_or_brand}" if product.seller_or_brand else ""
    price_str = _format_price(product.price, product.currency)

    if variant_type == AffiliateVariantType.PROBLEM_SOLUTION:
        hook_15 = f"Xem ngay {product.title} tiện ích này."
        hook_std = f"Nếu bạn đang tìm kiếm giải pháp tiện lợi mỗi ngày, hãy xem ngay {product.title}."
        audience_problem = f"Khó khăn khi chưa có {product.title}."
        cta = "Bấm ngay vào link giỏ hàng bên dưới để sở hữu nhé!"

        if duration_seconds == 15:
            voice_script = (
                f"{hook_15} "
                f"Sản phẩm có {feat1.lower()}. "
                f"Giá chỉ {price_str}, {cta}"
            )
            scene_duration = 15.0 / 3.0
            scenes = [
                AffiliateScene(1, "hook", on_screen_text=f"Giải pháp: {product.title}", voice_segment=hook_15, duration_seconds=scene_duration),
                AffiliateScene(2, "solution_feature", on_screen_text=feat1, voice_segment=f"Sản phẩm có {feat1.lower()}.", duration_seconds=scene_duration),
                AffiliateScene(3, "cta", on_screen_text=f"Chỉ {price_str} - Mua ngay", voice_segment=f"Giá chỉ {price_str}, {cta}", duration_seconds=scene_duration),
            ]
            hook = hook_15
        elif duration_seconds == 20:
            voice_script = (
                f"{hook_std} "
                f"Sản phẩm có {feat1.lower()}. "
                f"Đi kèm {feat2.lower()}{brand_clause}. "
                f"Mức giá chỉ {price_str}. {cta}"
            )
            scene_duration = 20.0 / 4.0
            scenes = [
                AffiliateScene(1, "hook", on_screen_text=product.title, voice_segment=hook_std, duration_seconds=scene_duration),
                AffiliateScene(2, "solution_feature", on_screen_text=feat1, voice_segment=f"Sản phẩm có {feat1.lower()}.", duration_seconds=scene_duration),
                AffiliateScene(3, "proof_benefit", on_screen_text=feat2, voice_segment=f"Đi kèm {feat2.lower()}{brand_clause}.", duration_seconds=scene_duration),
                AffiliateScene(4, "cta", on_screen_text=f"Giá: {price_str} | Mua ngay", voice_segment=f"Mức giá chỉ {price_str}. {cta}", duration_seconds=scene_duration),
            ]
            hook = hook_std
        else:  # 30s
            voice_script = (
                f"{hook_std} "
                f"Điểm nổi bật đầu tiên là {feat1.lower()}. "
                f"Tiếp theo là {feat2.lower()}. "
                f"Thêm vào đó là {feat3.lower()}{brand_clause}. "
                f"Chỉ với {price_str}, đây là lựa chọn rất tiện ích. {cta}"
            )
            scene_duration = 30.0 / 5.0
            scenes = [
                AffiliateScene(1, "hook", on_screen_text=product.title, voice_segment=hook_std, duration_seconds=scene_duration),
                AffiliateScene(2, "solution_feature", on_screen_text=feat1, voice_segment=f"Điểm nổi bật đầu tiên là {feat1.lower()}.", duration_seconds=scene_duration),
                AffiliateScene(3, "proof_benefit", on_screen_text=feat2, voice_segment=f"Tiếp theo là {feat2.lower()}.", duration_seconds=scene_duration),
                AffiliateScene(4, "extra_benefit", on_screen_text=feat3, voice_segment=f"Thêm vào đó là {feat3.lower()}{brand_clause}.", duration_seconds=scene_duration),
                AffiliateScene(5, "cta", on_screen_text=f"Giá tốt {price_str}", voice_segment=f"Chỉ với {price_str}, {cta}", duration_seconds=scene_duration),
            ]
            hook = hook_std

        on_screen = [s.on_screen_text for s in scenes]
        verified_pts = [feat1, feat2]

    elif variant_type == AffiliateVariantType.THREE_REASONS:
        hook_15 = f"3 điểm nổi bật của {product.title}."
        hook_std = f"Đây là 3 điểm đáng chú ý của {product.title}."
        audience_problem = "Cần thông tin tính năng cụ thể trước khi mua."
        cta = "Nhấn vào góc trái màn hình để xem chi tiết nhé!"

        if duration_seconds == 15:
            voice_script = (
                f"{hook_15} "
                f"Thứ nhất, {feat1.lower()}. Thứ hai, {feat2.lower()}. "
                f"Chỉ {price_str}, {cta}"
            )
            scene_duration = 15.0 / 3.0
            scenes = [
                AffiliateScene(1, "hook", on_screen_text=f"3 Điểm Về {product.title}", voice_segment=hook_15, duration_seconds=scene_duration),
                AffiliateScene(2, "reasons", on_screen_text=f"1. {feat1}\n2. {feat2}", voice_segment=f"Thứ nhất, {feat1.lower()}. Thứ hai, {feat2.lower()}.", duration_seconds=scene_duration),
                AffiliateScene(3, "cta", on_screen_text=f"Giá: {price_str}", voice_segment=f"Chỉ {price_str}, {cta}", duration_seconds=scene_duration),
            ]
            hook = hook_15
        elif duration_seconds == 20:
            voice_script = (
                f"{hook_std} "
                f"Thứ nhất: {feat1.lower()}. "
                f"Thứ hai: {feat2.lower()}. "
                f"Và thứ ba: Mức giá {price_str}{brand_clause}. {cta}"
            )
            scene_duration = 20.0 / 4.0
            scenes = [
                AffiliateScene(1, "hook", on_screen_text=f"3 Điểm Đáng Chú Ý", voice_segment=hook_std, duration_seconds=scene_duration),
                AffiliateScene(2, "reason_1", on_screen_text=f"1. {feat1}", voice_segment=f"Thứ nhất: {feat1.lower()}.", duration_seconds=scene_duration),
                AffiliateScene(3, "reason_2", on_screen_text=f"2. {feat2}", voice_segment=f"Thứ hai: {feat2.lower()}.", duration_seconds=scene_duration),
                AffiliateScene(4, "reason_3_cta", on_screen_text=f"3. Giá {price_str}", voice_segment=f"Và thứ ba: Mức giá {price_str}{brand_clause}. {cta}", duration_seconds=scene_duration),
            ]
            hook = hook_std
        else:  # 30s
            voice_script = (
                f"{hook_std} "
                f"Điểm số một: {feat1.lower()}. "
                f"Điểm số hai: {feat2.lower()}. "
                f"Điểm số ba: {feat3.lower()}{brand_clause}. "
                f"Giá bán hiện tại là {price_str}. {cta}"
            )
            scene_duration = 30.0 / 5.0
            scenes = [
                AffiliateScene(1, "hook", on_screen_text=f"3 Điểm Nổi Bật", voice_segment=hook_std, duration_seconds=scene_duration),
                AffiliateScene(2, "reason_1", on_screen_text=f"1. {feat1}", voice_segment=f"Điểm số một: {feat1.lower()}.", duration_seconds=scene_duration),
                AffiliateScene(3, "reason_2", on_screen_text=f"2. {feat2}", voice_segment=f"Điểm số hai: {feat2.lower()}.", duration_seconds=scene_duration),
                AffiliateScene(4, "reason_3", on_screen_text=f"3. {feat3}", voice_segment=f"Điểm số ba: {feat3.lower()}{brand_clause}.", duration_seconds=scene_duration),
                AffiliateScene(5, "cta", on_screen_text=f"Thông Tin: {price_str}", voice_segment=f"Giá bán là {price_str}. {cta}", duration_seconds=scene_duration),
            ]
            hook = hook_std

        on_screen = [s.on_screen_text for s in scenes]
        verified_pts = [feat1, feat2, feat3]

    else:  # COMPARISON_HIGHLIGHT
        hook_15 = f"Khám phá các đặc điểm của {product.title}."
        hook_std = f"Cùng tìm hiểu những điểm đáng chú ý trên {product.title}."
        audience_problem = "Muốn nắm rõ thông số và điểm nổi bật trước khi chọn mua."
        cta = "Xem ngay link chi tiết tại phần mô tả bên dưới nhé!"

        if duration_seconds == 15:
            voice_script = (
                f"{hook_15} "
                f"Sản phẩm có {feat1.lower()}. "
                f"Giá chỉ {price_str}, {cta}"
            )
            scene_duration = 15.0 / 3.0
            scenes = [
                AffiliateScene(1, "hook", on_screen_text=f"Đặc Điểm {product.title}", voice_segment=hook_15, duration_seconds=scene_duration),
                AffiliateScene(2, "comparison", on_screen_text=f"Tính năng: {feat1}", voice_segment=f"Sản phẩm có {feat1.lower()}.", duration_seconds=scene_duration),
                AffiliateScene(3, "cta", on_screen_text=f"Chỉ {price_str}", voice_segment=f"Giá chỉ {price_str}, {cta}", duration_seconds=scene_duration),
            ]
            hook = hook_15
        elif duration_seconds == 20:
            voice_script = (
                f"{hook_std} "
                f"Đầu tiên là {feat1.lower()}. "
                f"Kết hợp cùng {feat2.lower()}{brand_clause}. "
                f"Mức giá chỉ {price_str}. {cta}"
            )
            scene_duration = 20.0 / 4.0
            scenes = [
                AffiliateScene(1, "hook", on_screen_text=f"Thông Tin Chi Tiết", voice_segment=hook_std, duration_seconds=scene_duration),
                AffiliateScene(2, "advantage_1", on_screen_text=f"Đặc điểm: {feat1}", voice_segment=f"Đầu tiên là {feat1.lower()}.", duration_seconds=scene_duration),
                AffiliateScene(3, "advantage_2", on_screen_text=f"Kèm theo: {feat2}", voice_segment=f"Kết hợp cùng {feat2.lower()}{brand_clause}.", duration_seconds=scene_duration),
                AffiliateScene(4, "cta", on_screen_text=f"Giá: {price_str} | Xem ngay", voice_segment=f"Mức giá chỉ {price_str}. {cta}", duration_seconds=scene_duration),
            ]
            hook = hook_std
        else:  # 30s
            voice_script = (
                f"{hook_std} "
                f"Chiếc này có {feat1.lower()}. "
                f"Thêm vào đó là {feat2.lower()}. "
                f"Và đặc biệt có {feat3.lower()}{brand_clause}. "
                f"Mức giá {price_str} rất phù hợp nhu cầu. {cta}"
            )
            scene_duration = 30.0 / 5.0
            scenes = [
                AffiliateScene(1, "hook", on_screen_text=f"Đặc Điểm {product.title}", voice_segment=hook_std, duration_seconds=scene_duration),
                AffiliateScene(2, "advantage_1", on_screen_text=feat1, voice_segment=f"Chiếc này có {feat1.lower()}.", duration_seconds=scene_duration),
                AffiliateScene(3, "advantage_2", on_screen_text=feat2, voice_segment=f"Thêm vào đó là {feat2.lower()}.", duration_seconds=scene_duration),
                AffiliateScene(4, "advantage_3", on_screen_text=feat3, voice_segment=f"Và có {feat3.lower()}{brand_clause}.", duration_seconds=scene_duration),
                AffiliateScene(5, "cta", on_screen_text=f"Giá chỉ {price_str}", voice_segment=f"Mức giá {price_str} rất phù hợp. {cta}", duration_seconds=scene_duration),
            ]
            hook = hook_std

        on_screen = [s.on_screen_text for s in scenes]
        verified_pts = [feat1, feat2]

    return AffiliateScript(
        variant_type=variant_type,
        title=product.title,
        hook=hook,
        audience_problem=audience_problem,
        verified_product_points=verified_pts,
        voice_script=voice_script,
        on_screen_text=on_screen,
        cta=cta,
        risk_flags=[],
        scenes=scenes,
        target_duration_seconds=duration_seconds,
    )


def plan_affiliate_pack(
    product: ProductInput,
    *,
    duration_seconds: int = 20,
) -> AffiliatePack:
    """
    Plan all three creative strategies for the given product.
    """
    if duration_seconds not in SUPPORTED_DURATIONS:
        raise ValueError(
            f"Unsupported duration {duration_seconds}s. Only {SUPPORTED_DURATIONS} seconds are supported."
        )

    variants = [
        plan_single_variant(product, AffiliateVariantType.PROBLEM_SOLUTION, duration_seconds=duration_seconds),
        plan_single_variant(product, AffiliateVariantType.THREE_REASONS, duration_seconds=duration_seconds),
        plan_single_variant(product, AffiliateVariantType.COMPARISON_HIGHLIGHT, duration_seconds=duration_seconds),
    ]
    return AffiliatePack(product=product, variants=variants)
