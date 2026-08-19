"""
Workflow integration tests for V5 Auto Affiliate Factory (render-affiliate.yml).
"""
import re
from pathlib import Path

WORKFLOW = Path(".github/workflows/render-affiliate.yml")


def _text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_affiliate_workflow_exists():
    assert WORKFLOW.exists(), "render-affiliate.yml must exist"


def test_affiliate_workflow_inputs():
    text = _text()
    assert "name: Render Affiliate Videos" in text
    assert "workflow_dispatch:" in text
    for field in [
        "product_json:",
        "duration_seconds:",
        "voice:",
    ]:
        assert field in text, f"Expected input field {field!r} in render-affiliate.yml"


def test_affiliate_workflow_least_privilege_and_retention():
    text = _text()
    assert "permissions:" in text
    assert "contents: read" in text
    assert "actions/upload-artifact@v4" in text
    assert "affiliate-output" in text
    m = re.search(r"retention-days:\s*(\d+)", text)
    assert m is not None and int(m.group(1)) <= 3, "retention-days must be <= 3"


def test_no_hardcoded_secrets_in_affiliate_workflow():
    text = _text()
    assert "ghp_" not in text, "GitHub token must not be hardcoded"
    assert "sk-" not in text, "OpenAI key must not be hardcoded"


def test_affiliate_workflow_noninteractive_and_verification():
    text = _text()
    assert "fonts-noto-cjk" not in text, "Avoid massive fonts-noto-cjk to prevent CI hang"
    assert "ffmpeg -version" in text
    assert "ffprobe -version" in text
    assert "problem_solution" in text
    assert "three_reasons" in text
    assert "comparison_highlight" in text
    assert "affiliate-output/${variant}.mp4" in text
    assert '",".join(' in text, "Materials must be comma-separated file paths for MPT CLI"
