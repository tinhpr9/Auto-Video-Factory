"""
Workflow integration tests — updated for MoneyPrinterTurbo adapter (V4.0).

Tests verify the render-video.yml workflow contract:
- workflow_dispatch phone-friendly inputs
- upstream MPT SHA pinned
- secrets scoped correctly (LLM_API_KEY, PEXELS_API_KEY)
- least-privilege permissions
- artifact name and retention
- timeout guard
- output verification step
- no hardcoded tokens
"""
import re
from pathlib import Path

WORKFLOW = Path(".github/workflows/render-video.yml")


def _text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Phone-friendly inputs
# ---------------------------------------------------------------------------

def test_manual_render_workflow_exposes_phone_friendly_inputs():
    text = _text()
    assert "name: Auto Video Factory" in text
    assert "workflow_dispatch:" in text
    for field in [
        "topic:",
        "duration_seconds:",
        "voice:",
        "video_source:",
        "visual_provider:",
        "flow_mode:",
        "flow_session:",
    ]:
        assert field in text, f"Expected input field {field!r} in workflow"
    assert "pull_request:" not in text


# ---------------------------------------------------------------------------
# Security and artifact constraints
# ---------------------------------------------------------------------------

def test_workflow_uses_least_privilege_and_short_retention():
    text = _text()
    assert "contents: write" in text
    assert "actions/upload-artifact@v4" in text
    assert "auto-video-output" in text
    assert "flow-quality-pack" in text
    assert "retention-days:" in text
    m = re.search(r"retention-days:\s*(\d+)", text)
    assert m is not None and int(m.group(1)) <= 3, "retention-days must be <= 3"
    assert "timeout-minutes:" in text


def test_no_hardcoded_secrets_in_workflow():
    text = _text()
    assert "ghp_" not in text, "GitHub token must not be hardcoded"
    assert "sk-" not in text, "OpenAI key must not be hardcoded"


def test_secrets_scoped_to_steps_not_job_env():
    """GEMINI_API_KEY and PEXELS_API_KEY must appear only in step-level env blocks."""
    text = _text()
    # Secrets must be referenced only via ${{ secrets.* }} syntax — not hardcoded
    assert "${{ secrets.PEXELS_API_KEY }}" in text
    assert "${{ secrets.GEMINI_API_KEY }}" in text
    # Secrets must NOT appear in the top-level job env block
    job_level = text.split("    steps:", 1)[0]
    assert "secrets.GEMINI_API_KEY" not in job_level, (
        "GEMINI_API_KEY must be step-scoped, not job-level env"
    )
    assert "secrets.PEXELS_API_KEY" not in job_level, (
        "PEXELS_API_KEY must be step-scoped, not job-level env"
    )


# ---------------------------------------------------------------------------
# MoneyPrinterTurbo upstream pinning
# ---------------------------------------------------------------------------

def test_mpt_upstream_sha_pinned_in_workflow():
    text = _text()
    expected_sha = "b42e945b497176c823579f9b1895d9323446de23"
    assert expected_sha in text, "Workflow must pin MPT at exact tested SHA"


def test_mpt_repo_referenced():
    text = _text()
    assert "harry0703/MoneyPrinterTurbo" in text


# ---------------------------------------------------------------------------
# Runtime dependencies
# ---------------------------------------------------------------------------

def test_workflow_installs_ffmpeg():
    text = _text()
    assert "ffmpeg" in text


def test_workflow_installs_python_and_uv():
    text = _text()
    assert "setup-python" in text
    assert "uv" in text


# ---------------------------------------------------------------------------
# Output verification
# ---------------------------------------------------------------------------

def test_verify_output_before_upload():
    text = _text()
    assert "test -s" in text, "Workflow must verify output video is non-empty"


def test_artifact_upload_path_contains_video():
    text = _text()
    assert "video.mp4" in text


# ---------------------------------------------------------------------------
# Bug 4 (RED): draft release mailbox must fail closed
# ---------------------------------------------------------------------------

def test_draft_release_create_not_suppressed():
    """gh release create block must NOT be suppressed with '|| echo' or '|| true'."""
    text = _text()
    # The run block may use multiline backslash continuation.
    # Check that within any run: block containing 'gh release create', there is no
    # '|| echo' or '|| true' following the create command (on same or continuation lines).
    assert "gh release create" in text, "Expected 'gh release create' in workflow"
    assert "|| echo" not in text or "Draft release creation skipped" not in text, (
        "gh release create must not be silently suppressed with '|| echo'"
    )
    # Specifically: the old || echo warning pattern must be gone
    assert "Draft release creation skipped" not in text, (
        "gh release create must not suppress failure with error-masking warning text"
    )

def test_flow_upload_url_not_set_before_release_verify():
    """FLOW_UPLOAD_URL must only be set after verifying release creation succeeded."""
    text = _text()
    create_idx = text.find("gh release create")
    url_idx = text.find("FLOW_UPLOAD_URL=")
    if create_idx == -1 or url_idx == -1:
        return  # steps may not both exist yet
    # There must be a verification step (gh release view) between create and URL set
    between = text[create_idx:url_idx]
    assert "gh release view" in between or "SESSION_TAG" in between, (
        "FLOW_UPLOAD_URL must only be set after verifying the release was actually created"
    )


