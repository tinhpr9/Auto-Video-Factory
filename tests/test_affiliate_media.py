"""
TDD tests for Real Product Media Ingestion & Staging (V5 Auto Affiliate Factory).
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from auto_video_factory.affiliate.media import (
    AffiliateMediaStager,
    InsufficientMediaError,
    prepare_product_media_clips,
)
from auto_video_factory.affiliate.models import (
    AffiliateVariantType,
    ProductInput,
)
from auto_video_factory.affiliate.planner import plan_affiliate_pack
from auto_video_factory.affiliate.validation import validate_product_input


def sample_product_with_files(tmp_path: Path) -> ProductInput:
    img1 = tmp_path / "img1.jpg"
    img1.write_bytes(b"dummy_image_1")
    img2 = tmp_path / "img2.jpg"
    img2.write_bytes(b"dummy_image_2")
    vid1 = tmp_path / "vid1.mp4"
    vid1.write_bytes(b"dummy_video_1")

    return validate_product_input({
        "product_id": "PROD-303",
        "title": "Tai nghe Bluetooth True Wireless AirBass",
        "description": "Tai nghe chống ồn chủ động ANC, pin 24 giờ, âm bass mạnh mẽ.",
        "price": 490000,
        "currency": "VND",
        "product_url": "https://example.com/product/airbass",
        "affiliate_url": "https://aff.example.com/click/789",
        "images": [str(img1), str(img2)],
        "videos": [str(vid1)],
        "seller_or_brand": "AirSound Official",
        "features": ["Chống ồn ANC 25dB", "Pin 24 giờ cùng hộp sạc", "Chuẩn Bluetooth 5.3"],
        "verified_claims": ["Thời lượng pin thực tế 24 giờ gồm dock sạc"],
        "prohibited_or_unverified_claims": ["Âm thanh hay nhất thế giới"],
        "source": "manual_entry",
    })


# ===========================================================================
# 1. Media Staging & Ordering
# ===========================================================================

class TestMediaStaging:
    @patch("subprocess.run")
    def test_staged_clips_ordered_deterministically(self, mock_run, tmp_path):
        product = sample_product_with_files(tmp_path)
        pack = plan_affiliate_pack(product, duration_seconds=20)
        variant = pack.get_variant(AffiliateVariantType.PROBLEM_SOLUTION)

        out_dir = tmp_path / "staged"
        def fake_run(cmd, *args, **kwargs):
            out_file = Path(cmd[-1])
            out_file.write_bytes(b"dummy_rendered_clip")
            return MagicMock(returncode=0, stderr="")
        mock_run.side_effect = fake_run

        # Mock clip duration probing
        with patch("auto_video_factory.affiliate.media.probe_clip_duration", return_value=5.0):
            stager = AffiliateMediaStager()
            staged_clips = stager.stage_media_for_variant(
                product=product,
                variant=variant,
                output_dir=out_dir,
            )

        assert len(staged_clips) >= 3
        # Ensure filenames follow scene01.mp4, scene02.mp4 format
        for i, clip in enumerate(staged_clips, start=1):
            assert clip.name == f"scene{i:02d}.mp4"

    def test_missing_media_raises_insufficient_media_error(self, tmp_path):
        product = validate_product_input({
            "product_id": "PROD-EMPTY",
            "title": "Sản phẩm không có ảnh",
            "description": "Mô tả",
            "price": 100000,
            "currency": "VND",
            "images": [],
            "videos": [],
            "features": ["Tính năng 1"],
            "verified_claims": ["Claim 1"],
            "prohibited_or_unverified_claims": [],
            "source": "manual",
        })
        pack = plan_affiliate_pack(product, duration_seconds=20)
        variant = pack.get_variant(AffiliateVariantType.PROBLEM_SOLUTION)

        stager = AffiliateMediaStager()
        with pytest.raises(InsufficientMediaError, match="No usable product images or videos provided"):
            stager.stage_media_for_variant(
                product=product,
                variant=variant,
                output_dir=tmp_path / "staged_empty",
            )
