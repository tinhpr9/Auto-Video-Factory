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
    assert "apt-get install" in text and "ffmpeg" in text
    assert "ffprobe -version" in text or "ffprobe" in text


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
# Valid 1 (RED): workflow summary must reflect actual scene count, not hardcoded 6
# ---------------------------------------------------------------------------

def test_prepare_flow_pack_summary_does_not_hardcode_6_clips():
    """Summary must NOT hardcode '6 clips' or 'scene06.mp4' independent of duration."""
    text = _text()
    # '6 clips' hardcoded in summary step for ALL durations is wrong for 90s
    assert "generate the 6 clips" not in text, (
        "Workflow summary must not hardcode '6 clips' — count depends on duration"
    )

def test_prepare_flow_pack_summary_does_not_hardcode_scene06():
    """Summary must NOT hardcode 'scene06.mp4' — 90s requires scene09.mp4."""
    text = _text()
    assert "through scene06.mp4" not in text, (
        "Workflow summary must not hardcode 'through scene06.mp4' — 90s uses 9 scenes"
    )

def test_prepare_flow_pack_summary_references_scene_count_variable():
    """Summary must use EXPECTED_SCENES / LAST_SCENE derived from FlowScenePack."""
    text = _text()
    assert "EXPECTED_SCENES" in text
    assert "LAST_SCENE" in text


def test_prepare_flow_pack_derives_scenes_from_pack_json_not_duration_heuristics():
    """Workflow prepare job must read flow_scene_pack.json as the single authority
    for scene count and last scene filename, without duplicating duration heuristics."""
    text = _text()
    prepare_job = text.split("prepare:", 1)[1].split("render:", 1)[0]
    assert "flow_scene_pack.json" in prepare_job
    assert "-ge 90" not in prepare_job, (
        "prepare job must not duplicate flow_planner.py logic with '-ge 90'"
    )


def test_workflow_does_not_duplicate_duration_to_scene_heuristics():
    """Neither prepare nor render job should contain hardcoded 'if duration >= 90' or '-ge 90'."""
    text = _text()
    assert "-ge 90" not in text, "Workflow should not contain shell duration >= 90 heuristic"
    assert "9 if duration >= 90 else 6" not in text, (
        "Workflow should not contain python duration >= 90 heuristic outside flow_planner.py"
    )


def test_summary_scene_derivation_from_simulated_custom_pack(tmp_path):
    """Simulate a custom FlowScenePack with arbitrary scene count and verify extraction."""
    import json
    import subprocess
    import sys
    pack_data = {
        "total_scenes": 12,
        "scenes": [{"expected_filename": f"scene{i:02d}.mp4"} for i in range(1, 13)]
    }
    pack_file = tmp_path / "flow_scene_pack.json"
    pack_file.write_text(json.dumps(pack_data), encoding="utf-8")

    cmd_scenes = [
        sys.executable, "-c",
        f"import json; d=json.load(open('{pack_file}')); print(d.get('total_scenes', len(d.get('scenes', []))))"
    ]
    cmd_last = [
        sys.executable, "-c",
        f"import json; d=json.load(open('{pack_file}')); print(d['scenes'][-1]['expected_filename'] if d.get('scenes') else f'scene{{int(d.get(\"total_scenes\", 6)):02d}}.mp4')"
    ]

    res_scenes = subprocess.run(cmd_scenes, capture_output=True, text=True, check=True)
    res_last = subprocess.run(cmd_last, capture_output=True, text=True, check=True)

    assert res_scenes.stdout.strip() == "12"
    assert res_last.stdout.strip() == "scene12.mp4"


# ---------------------------------------------------------------------------
# Valid 2 (RED): least privilege — render path must not have contents: write
# ---------------------------------------------------------------------------

def test_prepare_flow_pack_job_has_contents_write():
    """prepare_flow_pack path needs contents: write to create Draft Release."""
    text = _text()
    assert "contents: write" in text, (
        "prepare_flow_pack path must have contents: write to create Draft Release"
    )

def test_render_path_does_not_have_workflow_wide_contents_write():
    """Workflow-level permissions block must NOT set contents: write.
    Only the prepare job (job-level) should have contents: write."""
    import re
    text = _text()
    before_jobs = text.split("jobs:", 1)[0]
    # Match actual YAML key 'contents: write' (indented, not inside a comment '#...')
    # Remove comment lines before checking
    non_comment_lines = "\n".join(
        line for line in before_jobs.splitlines()
        if not line.lstrip().startswith("#")
    )
    assert not re.search(r"^\s*contents:\s*write", non_comment_lines, re.MULTILINE), (
        "contents: write must not be set at workflow level (outside comments) — "
        "it grants write access to ALL paths. Only prepare job should have write access."
    )

def test_render_job_has_contents_read_not_write():
    """Workflow-level permissions must be contents: read (render job must not get write)."""
    import re
    text = _text()
    before_jobs = text.split("jobs:", 1)[0]
    non_comment_lines = "\n".join(
        line for line in before_jobs.splitlines()
        if not line.lstrip().startswith("#")
    )
    assert re.search(r"^\s*contents:\s*read", non_comment_lines, re.MULTILINE), (
        "Workflow-level permissions must set contents: read so render path gets read-only access"
    )


# ---------------------------------------------------------------------------
# Valid 3 (RED): release view ordering — must strictly require gh release view
# ---------------------------------------------------------------------------

def test_flow_upload_url_not_set_before_release_verify():
    """FLOW_UPLOAD_URL must only be set AFTER gh release view confirms creation.
    SESSION_TAG alone is not sufficient verification."""
    text = _text()
    create_idx = text.find("gh release create")
    url_idx = text.find("FLOW_UPLOAD_URL=")
    if create_idx == -1 or url_idx == -1:
        return
    between = text[create_idx:url_idx]
    # Must contain 'gh release view' — SESSION_TAG alone is NOT sufficient
    assert "gh release view" in between, (
        "FLOW_UPLOAD_URL must only be set after 'gh release view' confirms the release exists. "
        "SESSION_TAG alone does not verify the release was created."
    )

def test_release_view_required_between_create_and_url():
    """Removing 'gh release view' must cause this test to fail — it is not optional."""
    text = _text()
    # If gh release view is absent entirely, the ordering guarantee is broken
    assert "gh release view" in text, (
        "gh release view must exist in workflow to verify Draft Release before exposing URL"
    )
    # Verify ordering: create → view → URL
    create_idx = text.find("gh release create")
    view_idx = text.find("gh release view")
    url_idx = text.find("FLOW_UPLOAD_URL=")
    assert create_idx != -1 and view_idx != -1 and url_idx != -1, (
        "All three steps (create, view, URL set) must exist"
    )
    assert create_idx < view_idx < url_idx, (
        f"Ordering violated: create@{create_idx} view@{view_idx} url@{url_idx}. "
        "Must be create < view < FLOW_UPLOAD_URL="
    )


# ---------------------------------------------------------------------------
# Bug 4 (existing): draft release must not be suppressed
# ---------------------------------------------------------------------------

def test_draft_release_create_not_suppressed():
    """gh release create block must NOT be suppressed with '|| echo' or '|| true'."""
    text = _text()
    assert "gh release create" in text, "Expected 'gh release create' in workflow"
    assert "Draft release creation skipped" not in text, (
        "gh release create must not suppress failure with error-masking warning text"
    )


# ---------------------------------------------------------------------------
# Valid Finding: Flow session manifest authority & drift prevention
# ---------------------------------------------------------------------------

def test_flow_session_downloads_manifest_and_clips():
    """When AVF_FLOW_SESSION is provided, render job must download flow_scene_pack.json
    and not just *.mp4, so session metadata is preserved."""
    text = _text()
    render_job = text.split("render:", 1)[1]
    # Check that gh release download includes flow_scene_pack.json or downloads the pack
    assert "flow_scene_pack.json" in render_job, (
        "render job must download and read flow_scene_pack.json from flow_session"
    )
    assert "gh release download" in render_job


def test_render_preflight_loads_flow_session_manifest_to_prevent_drift():
    """Render job must load authoritative topic, duration, flow_mode from session manifest
    when AVF_FLOW_SESSION is supplied, rather than using raw workflow dispatch inputs."""
    text = _text()
    render_job = text.split("render:", 1)[1]
    assert "load_and_validate_flow_session" in render_job or "load_flow_session" in render_job or "session_pack" in render_job, (
        "render job must use authoritative session loader to prevent dispatch drift"
    )


# ---------------------------------------------------------------------------
# Valid Bug: Single-owner flow session download (prevent double download collision)
# ---------------------------------------------------------------------------

def test_flow_session_download_has_single_owner_in_preflight():
    """Network download of flow_session assets must occur exactly once in Preflight.
    Stage Flow Clips must NOT duplicate the download, which causes file collision failures."""
    text = _text()
    render_job = text.split("render:", 1)[1]

    # Extract Preflight step vs Stage Flow Clips step
    preflight_chunk = render_job.split("Validate secrets and provider preflight", 1)[1].split("Checkout MoneyPrinterTurbo", 1)[0]
    assert "gh release download" in preflight_chunk, (
        "Preflight must own the single network ingress download of flow_session assets"
    )

    stage_chunk = render_job.split("Stage Flow Clips", 1)[1].split("Render video with MoneyPrinterTurbo", 1)[0]
    assert "gh release download" not in stage_chunk, (
        "Stage Flow Clips must NOT duplicate gh release download. "
        "Preflight is the sole network ingress owner."
    )


def test_render_job_exact_single_release_download():
    """Render job must have exactly ONE gh release download invocation across all steps."""
    text = _text()
    render_job = text.split("render:", 1)[1]
    download_count = render_job.count("gh release download")
    assert download_count == 1, (
        f"Render job should have exactly 1 'gh release download' invocation, found {download_count}."
    )


