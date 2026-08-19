"""
TDD tests for Affiliate CLI entrypoint (V5 Auto Affiliate Factory).
"""
from __future__ import annotations

import json
from pathlib import Path
import pytest

from auto_video_factory.affiliate.cli import main


def test_cli_plan_command(tmp_path: Path):
    prod_file = tmp_path / "product.json"
    prod_file.write_text(
        json.dumps({
            "product_id": "PROD-CLI-1",
            "title": "Nồi chiên không dầu Lock 5L",
            "description": "Nồi chiên không dầu công nghệ Rapid Air 1500W.",
            "price": 1200000,
            "currency": "VND",
            "images": ["img1.jpg"],
            "features": ["Công suất 1500W", "Dung tích 5L", "Khay chiên chống dính"],
            "verified_claims": ["Dung tích thực 5 lít"],
            "prohibited_or_unverified_claims": [],
        }),
        encoding="utf-8",
    )

    out_dir = tmp_path / "output_cli"
    exit_code = main(["plan", "--product", str(prod_file), "--duration", "20", "--output-dir", str(out_dir)])
    assert exit_code == 0
    
    pack_file = out_dir / "affiliate_pack.json"
    assert pack_file.exists()
    
    data = json.loads(pack_file.read_text(encoding="utf-8"))
    assert len(data["variants"]) == 3
    assert data["product"]["product_id"] == "PROD-CLI-1"
