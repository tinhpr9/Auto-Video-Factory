"""
Creative Angle & Content Planner for V5 Auto Affiliate Factory.
Generates 3 distinct, fact-grounded Vietnamese short-video scripts (15–30s).
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
    """
    feats = product.features or ["Sản phẩm chất lượng", "Thiết kế thông minh", "Tiện ích vượt trội"]
    claims = product.verified_claims or feats
    brand = product.seller_or_brand or "thương hiệu uy tín"
    price_str = _format_price(product.price, product.currency)

    feat1 = feats[0] if len(feats) > 0 else "Thiết kế hiện đại"
    feat2 = feats[1] if len(feats) > 1 else (claims[0] if claims else "Độ bền bỉ cao")
    feat3 = feats[2] if len(feats) > 2 else (feats[0] if len(feats) > 0 else "Dễ dàng sử dụng")

    if variant_type == AffiliateVariantType.PROBLEM_SOLUTION:
        hook_15 = f"Xem ngay {product.title} tiện ích này."
        hook_std = f"Nếu bạn đang tìm kiếm giải pháp tiện lợi mỗi ngày, hãy xem ngay {product.title}."
        audience_problem = f"Khó chịu với những bất tiện khi chưa có {product.title}."
        cta = "Bấm ngay vào link giỏ hàng bên dưới để sở hữu nhé!"

        if duration_seconds <= 15:
            voice_script = (
                f"{hook_15} "
                f"Sản phẩm có {feat1.lower()}. "
                f"Giá chỉ {price_str}, {cta}"
            )
            scene_duration = duration_seconds / 3.0
            scenes = [
                AffiliateScene(1, "hook", on_screen_text=f"Giải pháp: {product.title}", voice_segment=hook_15, duration_seconds=scene_duration),
                AffiliateScene(2, "solution_feature", on_screen_text=feat1, voice_segment=f"Sản phẩm có {feat1.lower()}.", duration_seconds=scene_duration),
                AffiliateScene(3, "cta", on_screen_text=f"Chỉ {price_str} - Mua ngay", voice_segment=f"Giá chỉ {price_str}, {cta}", duration_seconds=scene_duration),
            ]
            hook = hook_15
        elif duration_seconds <= 20:
            voice_script = (
                f"{hook_std} "
                f"Sở hữu {feat1.lower()}, an tâm sử dụng. "
                f"Đặc biệt là {feat2.lower()} từ {brand}. "
                f"Mức giá chỉ {price_str}. {cta}"
            )
            scene_duration = duration_seconds / 4.0
            scenes = [
                AffiliateScene(1, "hook", on_screen_text=product.title, voice_segment=hook_std, duration_seconds=scene_duration),
                AffiliateScene(2, "solution_feature", on_screen_text=feat1, voice_segment=f"Sở hữu {feat1.lower()}.", duration_seconds=scene_duration),
                AffiliateScene(3, "proof_benefit", on_screen_text=feat2, voice_segment=f"Đặc biệt là {feat2.lower()} từ {brand}.", duration_seconds=scene_duration),
                AffiliateScene(4, "cta", on_screen_text=f"Giá: {price_str} | Mua ngay", voice_segment=f"Mức giá chỉ {price_str}. {cta}", duration_seconds=scene_duration),
            ]
            hook = hook_std
        else:  # 30s
            voice_script = (
                f"{hook_std} "
                f"Điểm đáng giá đầu tiên là {feat1.lower()}. "
                f"Tiếp theo, bạn sẽ cực kỳ ưng ý với {feat2.lower()}. "
                f"Thêm vào đó là {feat3.lower()}, mang lại trải nghiệm tối ưu từ {brand}. "
                f"Chỉ với {price_str}, đây là lựa chọn cực kỳ hợp lý. {cta}"
            )
            scene_duration = duration_seconds / 5.0
            scenes = [
                AffiliateScene(1, "hook", on_screen_text=product.title, voice_segment=hook_std, duration_seconds=scene_duration),
                AffiliateScene(2, "solution_feature", on_screen_text=feat1, voice_segment=f"Điểm đáng giá đầu tiên là {feat1.lower()}.", duration_seconds=scene_duration),
                AffiliateScene(3, "proof_benefit", on_screen_text=feat2, voice_segment=f"Tiếp theo là {feat2.lower()}.", duration_seconds=scene_duration),
                AffiliateScene(4, "extra_benefit", on_screen_text=feat3, voice_segment=f"Thêm vào đó là {feat3.lower()} từ {brand}.", duration_seconds=scene_duration),
                AffiliateScene(5, "cta", on_screen_text=f"Giá tốt {price_str}", voice_segment=f"Chỉ với {price_str}, {cta}", duration_seconds=scene_duration),
            ]
            hook = hook_std

        on_screen = [s.on_screen_text for s in scenes]
        verified_pts = [feat1, feat2]

    elif variant_type == AffiliateVariantType.THREE_REASONS:
        hook_15 = f"3 lý do khiến {product.title} rất đáng mua."
        hook_std = f"Đây là 3 lý do khiến {product.title} cực kỳ đáng mua."
        audience_problem = "Cần lý do thuyết phục và tính năng thực tế trước khi quyết định mua."
        cta = "Nhấn vào góc trái màn hình để xem chi tiết nhé!"

        if duration_seconds <= 15:
            voice_script = (
                f"{hook_15} "
                f"Thứ nhất, {feat1.lower()}. Thứ hai, {feat2.lower()}. "
                f"Chỉ {price_str}, {cta}"
            )
            scene_duration = duration_seconds / 3.0
            scenes = [
                AffiliateScene(1, "hook", on_screen_text=f"3 Lý Do Mua {product.title}", voice_segment=hook_15, duration_seconds=scene_duration),
                AffiliateScene(2, "reasons", on_screen_text=f"1. {feat1}\n2. {feat2}", voice_segment=f"Thứ nhất, {feat1.lower()}. Thứ hai, {feat2.lower()}.", duration_seconds=scene_duration),
                AffiliateScene(3, "cta", on_screen_text=f"Giá: {price_str}", voice_segment=f"Chỉ {price_str}, {cta}", duration_seconds=scene_duration),
            ]
            hook = hook_15
        elif duration_seconds <= 20:
            voice_script = (
                f"{hook_std} "
                f"Thứ nhất: {feat1.lower()}. "
                f"Thứ hai: {feat2.lower()} rất tiện lợi. "
                f"Và thứ ba: Mức giá cực tốt chỉ {price_str} từ {brand}. {cta}"
            )
            scene_duration = duration_seconds / 4.0
            scenes = [
                AffiliateScene(1, "hook", on_screen_text=f"3 Lý Do Mua Ngay", voice_segment=hook_std, duration_seconds=scene_duration),
                AffiliateScene(2, "reason_1", on_screen_text=f"1. {feat1}", voice_segment=f"Thứ nhất: {feat1.lower()}.", duration_seconds=scene_duration),
                AffiliateScene(3, "reason_2", on_screen_text=f"2. {feat2}", voice_segment=f"Thứ hai: {feat2.lower()} rất tiện lợi.", duration_seconds=scene_duration),
                AffiliateScene(4, "reason_3_cta", on_screen_text=f"3. Giá chỉ {price_str}", voice_segment=f"Và thứ ba: Mức giá chỉ {price_str}. {cta}", duration_seconds=scene_duration),
            ]
            hook = hook_std
        else:  # 30s
            voice_script = (
                f"{hook_std} "
                f"Lý do số một: {feat1.lower()}. "
                f"Lý do số hai: {feat2.lower()} cực kỳ bền bỉ. "
                f"Lý do số ba: {feat3.lower()}, chuẩn chính hãng từ {brand}. "
                f"Giá bán hiện tại chỉ {price_str}. {cta}"
            )
            scene_duration = duration_seconds / 5.0
            scenes = [
                AffiliateScene(1, "hook", on_screen_text=f"3 Điểm Đáng Tiền Nhất", voice_segment=hook_std, duration_seconds=scene_duration),
                AffiliateScene(2, "reason_1", on_screen_text=f"1. {feat1}", voice_segment=f"Lý do số một: {feat1.lower()}.", duration_seconds=scene_duration),
                AffiliateScene(3, "reason_2", on_screen_text=f"2. {feat2}", voice_segment=f"Lý do số hai: {feat2.lower()}.", duration_seconds=scene_duration),
                AffiliateScene(4, "reason_3", on_screen_text=f"3. {feat3}", voice_segment=f"Lý do số ba: {feat3.lower()} từ {brand}.", duration_seconds=scene_duration),
                AffiliateScene(5, "cta", on_screen_text=f"Ưu Đãi: {price_str}", voice_segment=f"Giá bán chỉ {price_str}. {cta}", duration_seconds=scene_duration),
            ]
            hook = hook_std

        on_screen = [s.on_screen_text for s in scenes]
        verified_pts = [feat1, feat2, feat3]

    else:  # COMPARISON_HIGHLIGHT
        hook_15 = f"Khác biệt của {product.title} so với loại thường."
        hook_std = f"Đừng vội mua loại thông thường nếu chưa biết đến {product.title}."
        audience_problem = "Dễ mua phải sản phẩm kém chất lượng, nhanh hỏng và thiếu tính năng."
        cta = "Xem ngay link chính hãng tại phần mô tả bên dưới nhé!"

        if duration_seconds <= 15:
            voice_script = (
                f"{hook_15} "
                f"Sản phẩm vượt trội nhờ {feat1.lower()}. "
                f"Giá chỉ {price_str}, {cta}"
            )
            scene_duration = duration_seconds / 3.0
            scenes = [
                AffiliateScene(1, "hook", on_screen_text=f"Khác Biệt Của {product.title}", voice_segment=hook_15, duration_seconds=scene_duration),
                AffiliateScene(2, "comparison", on_screen_text=f"Ưu thế: {feat1}", voice_segment=f"Vượt trội nhờ {feat1.lower()}.", duration_seconds=scene_duration),
                AffiliateScene(3, "cta", on_screen_text=f"Chỉ {price_str}", voice_segment=f"Giá chỉ {price_str}, {cta}", duration_seconds=scene_duration),
            ]
            hook = hook_15
        elif duration_seconds <= 20:
            voice_script = (
                f"{hook_std} "
                f"Sự vượt trội đến từ {feat1.lower()} giúp nâng tầm trải nghiệm. "
                f"Kết hợp cùng {feat2.lower()} từ {brand}. "
                f"Chỉ với {price_str}. {cta}"
            )
            scene_duration = duration_seconds / 4.0
            scenes = [
                AffiliateScene(1, "hook", on_screen_text=f"So Sánh Thực Tế", voice_segment=hook_std, duration_seconds=scene_duration),
                AffiliateScene(2, "advantage_1", on_screen_text=f"Vượt trội: {feat1}", voice_segment=f"Sự vượt trội đến từ {feat1.lower()}.", duration_seconds=scene_duration),
                AffiliateScene(3, "advantage_2", on_screen_text=f"Chính hãng {brand}: {feat2}", voice_segment=f"Kết hợp cùng {feat2.lower()} từ {brand}.", duration_seconds=scene_duration),
                AffiliateScene(4, "cta", on_screen_text=f"Giá: {price_str} | Xem ngay", voice_segment=f"Chỉ với {price_str}. {cta}", duration_seconds=scene_duration),
            ]
            hook = hook_std
        else:  # 30s
            voice_script = (
                f"{hook_std} "
                f"Trong khi nhiều loại khác hay gặp vấn đề, chiếc này có {feat1.lower()}. "
                f"Thêm vào đó là {feat2.lower()} đem lại sự tiện dụng mỗi ngày. "
                f"Và đặc biệt {feat3.lower()} chuẩn chất lượng từ {brand}. "
                f"Mức giá {price_str} thật sự rất đáng đầu tư. {cta}"
            )
            scene_duration = duration_seconds / 5.0
            scenes = [
                AffiliateScene(1, "hook", on_screen_text=f"Vì Sao Nên Chọn {product.title}?", voice_segment=hook_std, duration_seconds=scene_duration),
                AffiliateScene(2, "advantage_1", on_screen_text=feat1, voice_segment=f"Trong khi loại khác dễ hỏng, chiếc này có {feat1.lower()}.", duration_seconds=scene_duration),
                AffiliateScene(3, "advantage_2", on_screen_text=feat2, voice_segment=f"Thêm vào đó là {feat2.lower()}.", duration_seconds=scene_duration),
                AffiliateScene(4, "advantage_3", on_screen_text=feat3, voice_segment=f"Và {feat3.lower()} chuẩn từ {brand}.", duration_seconds=scene_duration),
                AffiliateScene(5, "cta", on_screen_text=f"Giá chỉ {price_str}", voice_segment=f"Mức giá {price_str} rất đáng đầu tư. {cta}", duration_seconds=scene_duration),
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
    variants = [
        plan_single_variant(product, AffiliateVariantType.PROBLEM_SOLUTION, duration_seconds=duration_seconds),
        plan_single_variant(product, AffiliateVariantType.THREE_REASONS, duration_seconds=duration_seconds),
        plan_single_variant(product, AffiliateVariantType.COMPARISON_HIGHLIGHT, duration_seconds=duration_seconds),
    ]
    return AffiliatePack(product=product, variants=variants)
