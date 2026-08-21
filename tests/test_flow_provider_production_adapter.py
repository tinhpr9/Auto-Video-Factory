"""
Comprehensive unit, fake-client, and contract tests for ProductionFlowProvider
(the thin adapter over eddie-fqh/flow-py pinned to bd01679304d6fccc4c96fea3b33151c2e9e836f4).

Covers:
  1. Upstream provenance & strict non-fallback guarantees
  2. Construction & legacy parameter compatibility
  3. Import guard when flow-py is not installed
  4. Health check (uninstalled, unauthenticated, healthy)
  5. Credits fetching with fake client
  6. Model catalog matching FlowModel enum
  7. Video generation with fake client (T2V, I2V, aspect ratios, fail-closed count)
  8. Polling with fake client (completed, failed, pending, timeouts)
  9. Download with fake client (atomic move, error handling)
  10. Async-to-sync bridge (_run) and exception propagation
  11. Exception taxonomy classification
  12. FlowController integration with fake ProductionFlowProvider
"""
from __future__ import annotations

import asyncio
import sys
import time
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from auto_video_factory.flow_provider.contract import FlowProvider
from auto_video_factory.flow_provider.controller import FlowController
from auto_video_factory.flow_provider.models import (
    FlowAspectRatio,
    FlowFailureClass,
    FlowGenerationRequest,
    FlowJobRecord,
    FlowJobResult,
    FlowJobStatus,
    FlowModel,
)
from auto_video_factory.flow_provider.provider import (
    EXPERIMENTAL_RPC_PROVIDER,
    ProductionFlowProvider,
)


# =====================================================================
# 1. UPSTREAM PROVENANCE & ISOLATION GUARDS
# =====================================================================

def test_upstream_constants_locked():
    """Verify that the pinned upstream commit and repo cannot silently drift."""
    assert ProductionFlowProvider.UPSTREAM_REPO == "eddie-fqh/flow-py"
    assert ProductionFlowProvider.UPSTREAM_COMMIT == "bd01679304d6fccc4c96fea3b33151c2e9e836f4"
    assert ProductionFlowProvider.UPSTREAM_PACKAGE == "flow-py"

    # Strict architecture invariants: excluded repositories must never be referenced
    assert "google-flow-cli" not in ProductionFlowProvider.UPSTREAM_REPO
    assert "veo3tool" not in ProductionFlowProvider.UPSTREAM_REPO
    assert "veo-automation-extension" not in ProductionFlowProvider.UPSTREAM_REPO


def test_provenance_pin_matches_pyproject_toml_dynamically():
    """
    Provenance drift test: parses pyproject.toml directly and compares against
    ProductionFlowProvider constants to prevent drift between dependency declaration and runtime metadata.
    """
    pyproject_path = Path(__file__).resolve().parent.parent / "pyproject.toml"
    assert pyproject_path.exists(), f"Missing pyproject.toml at {pyproject_path}"
    content = pyproject_path.read_text(encoding="utf-8")

    # Extract prod dependency line
    assert "eddie-fqh/flow-py.git@" in content
    # Extract git commit SHA from pyproject.toml
    import re
    match = re.search(r"eddie-fqh/flow-py\.git@([0-9a-f]{40})", content)
    assert match is not None, "Could not find pinned flow-py git commit in pyproject.toml"
    declared_sha = match.group(1)

    assert declared_sha == "bd01679304d6fccc4c96fea3b33151c2e9e836f4"
    assert ProductionFlowProvider.UPSTREAM_COMMIT == declared_sha

    # Verify research-only repos are not in runtime/optional dependencies
    assert "google-flow-cli" not in content.replace("# DO NOT substitute google-flow-cli, veo3tool, or veo-automation-extension.", "")
    assert "veo3tool" not in content.replace("# DO NOT substitute google-flow-cli, veo3tool, or veo-automation-extension.", "")


def test_experimental_rpc_provider_is_false():
    """EXPERIMENTAL_RPC_PROVIDER must remain False at module level."""
    assert EXPERIMENTAL_RPC_PROVIDER is False


def test_implements_flow_provider_contract():
    """ProductionFlowProvider must implement the FlowProvider ABC."""
    assert issubclass(ProductionFlowProvider, FlowProvider)
    provider = ProductionFlowProvider()
    assert isinstance(provider, FlowProvider)


# =====================================================================
# 2. CONSTRUCTOR COMPATIBILITY
# =====================================================================

def test_constructor_new_args():
    p = ProductionFlowProvider(project_id="proj-123", headless=False, cdp_url="http://127.0.0.1:9222")
    assert p._project_id == "proj-123"
    assert p._headless is False
    assert p._cdp_url == "http://127.0.0.1:9222"


def test_constructor_rejects_legacy_auth_token_deterministically():
    """Supplied auth_token must be rejected deterministically rather than silently ignored."""
    with pytest.raises(ValueError, match="auth_token"):
        ProductionFlowProvider(auth_token="legacy_token_123")


def test_constructor_rejects_legacy_profile_path_deterministically():
    """Supplied profile_path must be rejected deterministically rather than silently ignored."""
    with pytest.raises(ValueError, match="profile_path"):
        ProductionFlowProvider(profile_path=Path("/tmp/legacy_profile"))


def test_constructor_cdp_endpoint_alias():
    """cdp_endpoint alias is preserved for compatibility."""
    p = ProductionFlowProvider(cdp_endpoint="http://127.0.0.1:9222")
    assert p._cdp_url == "http://127.0.0.1:9222"


# =====================================================================
# 3. IMPORT GUARD & UNINSTALLED BEHAVIOR
# =====================================================================

def test_import_flow_raises_importerror_with_install_hint_when_absent():
    with patch.dict(sys.modules, {"flow": None, "flow._exceptions": None}):
        with pytest.raises(ImportError) as exc_info:
            ProductionFlowProvider._import_flow()
    msg = str(exc_info.value)
    assert "eddie-fqh/flow-py" in msg
    assert "bd01679304d6fccc4c96fea3b33151c2e9e836f4" in msg
    assert "auto-video-factory[prod]" in msg


def test_health_reports_unhealthy_when_flow_py_not_installed():
    p = ProductionFlowProvider()
    with patch.dict(sys.modules, {"flow": None, "flow._exceptions": None}):
        status = p.health()
    assert status.healthy is False
    assert status.authenticated is False
    assert status.browser_ready is False
    assert "missing_auth" in status.details.get("reason", "") or "unauthenticated" in status.details.get("reason", "")
    assert status.details.get("captcha_bypass_disabled") is True
    assert status.details.get("experimental_rpc_disabled") is True
    assert status.details.get("upstream") == "eddie-fqh/flow-py"
    assert status.details.get("upstream_commit") == "bd01679304d6fccc4c96fea3b33151c2e9e836f4"


def test_health_cdp_mode_healthy_without_local_profile():
    """CDP-only mode must be healthy when CDP url is valid even if no local profile exists."""
    storage_mod = types.ModuleType("flow._storage")
    storage_mod.PROFILE_DIR = MagicMock(exists=MagicMock(return_value=False))
    storage_mod.get_active_project = MagicMock(return_value=("proj_cdp", "https://..."))

    flow_mod = types.ModuleType("flow")
    flow_mod.FlowClient = MagicMock()
    exc_mod = _make_stub_exc_module()
    api_mod = types.ModuleType("flow._api")
    api_mod.VideoJob = MagicMock()

    p = ProductionFlowProvider(cdp_url="http://127.0.0.1:9222", project_id="proj_cdp")
    from auto_video_factory.flow_provider.cdp_endpoint import CDPEndpointStatus
    mock_status = CDPEndpointStatus(
        ready=True,
        tcp_reachable=True,
        version_valid=True,
        targets_valid=True,
        endpoint_url="http://127.0.0.1:9222",
        browser_version="Chrome/130.0.0.0",
    )
    with patch.dict(sys.modules, {"flow": flow_mod, "flow._exceptions": exc_mod, "flow._api": api_mod, "flow._storage": storage_mod}), \
         patch("auto_video_factory.flow_provider.provider.check_cdp_endpoint_detailed", return_value=mock_status):
        status = p.health()

    assert status.healthy is True
    assert status.authenticated is True
    assert status.browser_ready is True
    assert status.profile_exists is False


def test_health_cdp_mode_unhealthy_on_malformed_url():
    """CDP mode with malformed URL must report unhealthy with invalid_cdp_url reason."""
    storage_mod = types.ModuleType("flow._storage")
    storage_mod.PROFILE_DIR = MagicMock(exists=MagicMock(return_value=False))
    storage_mod.get_active_project = MagicMock(return_value=("proj_cdp", "https://..."))

    flow_mod = types.ModuleType("flow")
    flow_mod.FlowClient = MagicMock()
    exc_mod = _make_stub_exc_module()
    api_mod = types.ModuleType("flow._api")
    api_mod.VideoJob = MagicMock()

    p = ProductionFlowProvider(cdp_url="invalid_no_scheme", project_id="proj_cdp")
    with patch.dict(sys.modules, {"flow": flow_mod, "flow._exceptions": exc_mod, "flow._api": api_mod, "flow._storage": storage_mod}):
        status = p.health()

    assert status.healthy is False
    assert status.browser_ready is False
    assert "invalid_cdp_url" in status.details.get("reason", "")


def test_health_profile_mode_healthy_when_profile_exists():
    """Profile mode is healthy when PROFILE_DIR exists and active project is found."""
    storage_mod = types.ModuleType("flow._storage")
    storage_mod.PROFILE_DIR = MagicMock(exists=MagicMock(return_value=True))
    storage_mod.get_active_project = MagicMock(return_value=("proj_local", "https://..."))

    flow_mod = types.ModuleType("flow")
    flow_mod.FlowClient = MagicMock()
    exc_mod = _make_stub_exc_module()
    api_mod = types.ModuleType("flow._api")
    api_mod.VideoJob = MagicMock()

    p = ProductionFlowProvider(cdp_url=None, project_id="proj_local")
    with patch.dict(sys.modules, {"flow": flow_mod, "flow._exceptions": exc_mod, "flow._api": api_mod, "flow._storage": storage_mod}):
        status = p.health()

    assert status.healthy is True
    assert status.authenticated is True
    assert status.browser_ready is True
    assert status.profile_exists is True


def test_health_profile_mode_unhealthy_when_profile_missing():
    """Profile mode is unhealthy when PROFILE_DIR is missing."""
    storage_mod = types.ModuleType("flow._storage")
    storage_mod.PROFILE_DIR = MagicMock(exists=MagicMock(return_value=False))
    storage_mod.get_active_project = MagicMock(return_value=("proj_local", "https://..."))

    flow_mod = types.ModuleType("flow")
    flow_mod.FlowClient = MagicMock()
    exc_mod = _make_stub_exc_module()
    api_mod = types.ModuleType("flow._api")
    api_mod.VideoJob = MagicMock()

    p = ProductionFlowProvider(cdp_url=None, project_id="proj_local")
    with patch.dict(sys.modules, {"flow": flow_mod, "flow._exceptions": exc_mod, "flow._api": api_mod, "flow._storage": storage_mod}):
        status = p.health()

    assert status.healthy is False
    assert status.authenticated is False
    assert status.browser_ready is False
    assert "browser_profile_missing" in status.details.get("reason", "")


# =====================================================================
# 4. ASYNC-TO-SYNC BRIDGE & EVENT LOOP LIFECYCLE
# =====================================================================

def test_run_bridge_executes_coroutine():
    async def _coro():
        return "result_42"

    assert ProductionFlowProvider._run(ProductionFlowProvider, _coro()) == "result_42"


def test_run_bridge_propagates_exceptions():
    async def _failing():
        raise ValueError("coroutine failed")

    with pytest.raises(ValueError, match="coroutine failed"):
        ProductionFlowProvider._run(ProductionFlowProvider, _failing())


def test_run_bridge_inside_running_loop():
    """When called inside a running event loop, _run dispatches to the dedicated loop thread safely."""
    async def _outer():
        async def _inner():
            return "inner_done"
        return ProductionFlowProvider._run(ProductionFlowProvider, _inner())

    result = asyncio.run(_outer())
    assert result == "inner_done"


def test_client_lifecycle_persistent_loop_reused_across_calls():
    """
    A FlowClient and its async resources must remain on the exact same
    persistent background event loop across consecutive calls.
    """
    created_loop_id: list[int] = []

    class _LoopBoundClient:
        def __init__(self):
            self.loop_id = id(asyncio.get_running_loop())
            created_loop_id.append(self.loop_id)
            self._api = MagicMock()
            self._api.get_credits = self.get_credits
            self._api.wait_for_video = self.wait_for_video
            self._api.get_video_model_config = self.get_model_config
            self.get_model_config = self.get_model_config
            self.close = self.close

        async def get_credits(self):
            current_id = id(asyncio.get_running_loop())
            assert current_id == self.loop_id, f"Loop changed: {current_id} != {self.loop_id}"
            return _FakeCredits(credits=300)

        async def get_model_config(self):
            current_id = id(asyncio.get_running_loop())
            assert current_id == self.loop_id
            return {"videoModels": [{"key": "veo_test", "creditCost": 20, "capabilities": ["TEXT"]}]}

        async def wait_for_video(self, *args, **kwargs):
            current_id = id(asyncio.get_running_loop())
            assert current_id == self.loop_id
            return _FakeVideoStatus("media_test")

        async def close(self):
            pass

    flow_mod = types.ModuleType("flow")
    flow_mod.FlowClient = MagicMock()

    async def _fake_create(*args, **kwargs):
        return _LoopBoundClient()

    flow_mod.FlowClient.create = _fake_create
    class _StubVideoJob:
        def __init__(self, media_name="", workflow_id="", project_id=""):
            self._raw = {}
            self.media_name = media_name
            self.workflow_id = workflow_id
            self.project_id = project_id

    video_job_cls = _StubVideoJob
    exc_mod = _make_stub_exc_module()

    p = ProductionFlowProvider()
    with patch.object(p, "_import_flow", return_value=(flow_mod.FlowClient, exc_mod, video_job_cls)):
        # Call 1: triggers client creation on persistent loop
        creds = p.get_credits()
        assert creds.available_credits == 300

        # Call 2: reuses client on same persistent loop
        models = p.get_models()
        assert len(models) == 1

        # Call 3: poll reuses client on same persistent loop
        status = p.poll("media_test")
        assert status.status == FlowJobStatus.COMPLETED

    assert len(created_loop_id) == 1
    p.close()
    assert p._client is None
    assert p._loop is None


def test_provider_close_and_context_manager():
    """Provider close() cleanly shuts down client and loop; context manager closes on exit."""
    fake_client = MagicMock()
    fake_client.close = AsyncMock()

    with ProductionFlowProvider() as p:
        p._client = fake_client
        p._ensure_loop()
        assert p._loop is not None
        assert p._thread is not None and p._thread.is_alive()

    # Exited context manager
    assert p._client is None
    assert p._loop is None


# =====================================================================
# 5. EXCEPTION TAXONOMY CLASSIFICATION
# =====================================================================

def _make_stub_exc_module():
    mod = types.ModuleType("flow._exceptions_stub")

    class FlowError(Exception): pass
    class AuthError(FlowError): pass
    class NotLoggedInError(AuthError): pass
    class GenerationError(FlowError): pass
    class GenerationTimeout(FlowError): pass
    class PolicyError(FlowError): pass
    class UIError(FlowError): pass
    class NotFoundError(GenerationError): pass
    class FeatureUnavailableError(FlowError): pass

    mod.FlowError = FlowError
    mod.AuthError = AuthError
    mod.NotLoggedInError = NotLoggedInError
    mod.GenerationError = GenerationError
    mod.GenerationTimeout = GenerationTimeout
    mod.PolicyError = PolicyError
    mod.UIError = UIError
    mod.NotFoundError = NotFoundError
    mod.FeatureUnavailableError = FeatureUnavailableError
    return mod


def test_classify_auth_and_session_errors():
    mod = _make_stub_exc_module()
    assert ProductionFlowProvider._classify_exception(mod.AuthError("expired"), mod) == FlowFailureClass.AUTH
    assert ProductionFlowProvider._classify_exception(mod.NotLoggedInError("no session"), mod) == FlowFailureClass.AUTH


def test_classify_timeout_and_policy_errors():
    mod = _make_stub_exc_module()
    assert ProductionFlowProvider._classify_exception(mod.GenerationTimeout(60), mod) == FlowFailureClass.TIMEOUT
    assert ProductionFlowProvider._classify_exception(mod.PolicyError("bad content"), mod) == FlowFailureClass.POLICY_VIOLATION


def test_classify_ui_and_not_found_errors():
    mod = _make_stub_exc_module()
    assert ProductionFlowProvider._classify_exception(mod.UIError("selector missing"), mod) == FlowFailureClass.UI_CHANGED
    assert ProductionFlowProvider._classify_exception(mod.NotFoundError("missing model"), mod) == FlowFailureClass.MODEL_UNAVAILABLE


def test_classify_network_and_rate_limit_messages():
    mod = _make_stub_exc_module()
    assert ProductionFlowProvider._classify_exception(ConnectionError("read timeout"), mod) == FlowFailureClass.TIMEOUT
    assert ProductionFlowProvider._classify_exception(Exception("HTTP 429 quota exceeded"), mod) == FlowFailureClass.RATE_LIMIT
    assert ProductionFlowProvider._classify_exception(RuntimeError("unrecognized"), mod) == FlowFailureClass.UNKNOWN


# =====================================================================
# 6. FAKE FLOW CLIENT TESTS (CREDITS, MODELS, GENERATE, POLL, DOWNLOAD)
# =====================================================================

class _FakeVideoJob:
    def __init__(self, media_name: str, workflow_id: str = "wf_1", project_id: str = "proj_1"):
        self.media_name = media_name
        self.workflow_id = workflow_id
        self.project_id = project_id


class _FakeVideoStatus:
    def __init__(self, media_name: str, complete: bool = True, failed: bool = False, fife_url: str = "https://lh3.google.com/video.mp4"):
        self.media_name = media_name
        self.complete = complete
        self.failed = failed
        self.fife_url = fife_url
        self.status = "MEDIA_GENERATION_STATUS_COMPLETE" if complete else ("MEDIA_GENERATION_STATUS_FAILED" if failed else "PENDING")


class _FakeCredits:
    def __init__(self, credits: int = 180):
        self.credits = credits


class _FakeFlowClient:
    def __init__(self):
        self._api = MagicMock()
        self._api.get_credits = AsyncMock(return_value=_FakeCredits(credits=240))
        self._api.wait_for_video = AsyncMock(return_value=_FakeVideoStatus("media_abc123"))
        self._api.get_video_model_config = AsyncMock(return_value={
            "videoModels": [
                {
                    "key": FlowModel.VEO_3_1_FAST.value,
                    "displayName": "Veo 3.1 Fast (portrait, audio, 20cr)",
                    "creditCost": 20,
                    "capabilities": ["TEXT", "START_IMAGE", "AUDIO"],
                    "supportedAspectRatios": ["PORTRAIT"],
                },
                {
                    "key": FlowModel.VEO_3_1_QUALITY.value,
                    "displayName": "Veo 3.1 Quality (portrait, audio, 100cr)",
                    "creditCost": 100,
                    "capabilities": ["TEXT", "START_IMAGE", "AUDIO", "UPSCALE"],
                    "supportedAspectRatios": ["PORTRAIT"],
                },
                {
                    "key": FlowModel.VEO_2_1_FAST.value,
                    "displayName": "Veo 2.1 Fast (portrait, no-audio, 10cr)",
                    "creditCost": 10,
                    "capabilities": ["TEXT", "START_IMAGE"],
                    "supportedAspectRatios": ["PORTRAIT"],
                },
            ]
        })
        self.get_model_config = AsyncMock(return_value={
            "videoModels": [
                {
                    "key": FlowModel.VEO_3_1_FAST.value,
                    "displayName": "Veo 3.1 Fast (portrait, audio, 20cr)",
                    "creditCost": 20,
                    "capabilities": ["TEXT", "START_IMAGE", "AUDIO"],
                    "supportedAspectRatios": ["PORTRAIT"],
                },
                {
                    "key": FlowModel.VEO_3_1_QUALITY.value,
                    "displayName": "Veo 3.1 Quality (portrait, audio, 100cr)",
                    "creditCost": 100,
                    "capabilities": ["TEXT", "START_IMAGE", "AUDIO", "UPSCALE"],
                    "supportedAspectRatios": ["PORTRAIT"],
                },
                {
                    "key": FlowModel.VEO_2_1_FAST.value,
                    "displayName": "Veo 2.1 Fast (portrait, no-audio, 10cr)",
                    "creditCost": 10,
                    "capabilities": ["TEXT", "START_IMAGE"],
                    "supportedAspectRatios": ["PORTRAIT"],
                },
            ]
        })
        self.generate_video = AsyncMock(return_value=[_FakeVideoJob("media_abc123")])
        self.download = AsyncMock(return_value=None)
        self.close = AsyncMock(return_value=None)


def _setup_fake_flow_env(fake_client: _FakeFlowClient, exc_mod=None):
    if exc_mod is None:
        exc_mod = _make_stub_exc_module()

    flow_mod = types.ModuleType("flow")
    flow_mod.FlowClient = MagicMock()
    flow_mod.FlowClient.create = AsyncMock(return_value=fake_client)

    class _StubVideoJob:
        def __init__(self, media_name="", workflow_id="", project_id=""):
            self._raw = {}
            self.media_name = media_name
            self.workflow_id = workflow_id
            self.project_id = project_id

    return flow_mod, exc_mod, _StubVideoJob


def test_get_credits_with_fake_client():
    fake_client = _FakeFlowClient()
    flow_mod, exc_mod, video_job_cls = _setup_fake_flow_env(fake_client)

    p = ProductionFlowProvider()
    with patch.object(p, "_import_flow", return_value=(flow_mod.FlowClient, exc_mod, video_job_cls)):
        p._client = fake_client
        creds = p.get_credits()

    assert creds.available_credits == 240
    assert creds.total_credits == 240
    assert creds.consumed_credits == 0


def test_get_models_dynamically_derived_from_upstream():
    fake_client = _FakeFlowClient()
    flow_mod, exc_mod, video_job_cls = _setup_fake_flow_env(fake_client)

    p = ProductionFlowProvider()
    with patch.object(p, "_import_flow", return_value=(flow_mod.FlowClient, exc_mod, video_job_cls)):
        p._client = fake_client
        models = p.get_models()

    assert len(models) == 3
    model_ids = {m.model_id for m in models}
    assert FlowModel.VEO_3_1_FAST.value in model_ids
    assert FlowModel.VEO_3_1_QUALITY.value in model_ids
    assert FlowModel.VEO_2_1_FAST.value in model_ids


def test_get_models_dynamic_catalog_and_filtering():
    """Verify that get_models adapts to upstream config changes and filters deprecated models."""
    fake_client = _FakeFlowClient()
    fake_client.get_model_config = AsyncMock(return_value={
        "videoModels": [
            {
                "key": "veo-4.0-future",
                "displayName": "Veo 4.0 Future",
                "creditCost": 50,
                "capabilities": ["TEXT", "START_IMAGE", "AUDIO"],
                "supportedAspectRatios": ["LANDSCAPE"],
                "modelStatus": "MODEL_STATUS_ACTIVE",
            },
            {
                "key": "veo-1.0-legacy",
                "displayName": "Veo 1.0 Legacy",
                "creditCost": 5,
                "modelStatus": "MODEL_STATUS_DEPRECATED",
            },
        ]
    })
    flow_mod, exc_mod, video_job_cls = _setup_fake_flow_env(fake_client)

    p = ProductionFlowProvider()
    with patch.object(p, "_import_flow", return_value=(flow_mod.FlowClient, exc_mod, video_job_cls)):
        p._client = fake_client
        models = p.get_models()

    assert len(models) == 1
    assert models[0].model_id == "veo-4.0-future"
    assert models[0].name == "Veo 4.0 Future"
    assert models[0].cost_credits == 50
    assert models[0].default_aspect_ratio == FlowAspectRatio.LANDSCAPE_16_9
    assert "image_to_video" in models[0].capabilities
    assert "text_to_video" in models[0].capabilities


def test_get_models_returns_empty_when_upstream_catalog_is_empty():
    """No fixed three-model fallback remains when upstream returns empty catalog."""
    fake_client = _FakeFlowClient()
    fake_client.get_model_config = AsyncMock(return_value={"videoModels": []})
    flow_mod, exc_mod, video_job_cls = _setup_fake_flow_env(fake_client)

    p = ProductionFlowProvider()
    with patch.object(p, "_import_flow", return_value=(flow_mod.FlowClient, exc_mod, video_job_cls)):
        p._client = fake_client
        models = p.get_models()

    assert models == []


def test_generate_video_count_gt1_rejected_before_upstream():
    p = ProductionFlowProvider()
    req = FlowGenerationRequest(
        job_id="job_gt1",
        prompt="A serene temple in mist",
        count=2,
    )
    result = p.generate_video(req)
    assert result.status == FlowJobStatus.FAILED
    assert "count=1" in (result.failure_message or "")


def test_generate_video_missing_start_image_fails_closed_zero_upstream_calls(tmp_path: Path):
    """If start_image_path is missing, generate_video must fail closed with zero upstream calls."""
    fake_client = _FakeFlowClient()
    flow_mod, exc_mod, video_job_cls = _setup_fake_flow_env(fake_client)

    p = ProductionFlowProvider()
    req = FlowGenerationRequest(
        job_id="job_missing_img",
        prompt="Animate this nonexistent image",
        start_image_path=tmp_path / "nonexistent_photo.png",
        count=1,
    )

    with patch.object(p, "_import_flow", return_value=(flow_mod.FlowClient, exc_mod, video_job_cls)):
        p._client = fake_client
        res = p.generate_video(req)

    assert res.status == FlowJobStatus.FAILED
    assert res.failure_class == FlowFailureClass.UNKNOWN
    assert "does not exist or is empty" in (res.failure_message or "")
    assert fake_client.generate_video.call_count == 0


def test_generate_video_empty_start_image_fails_closed_zero_upstream_calls(tmp_path: Path):
    """If start_image_path is 0 bytes, generate_video must fail closed with zero upstream calls."""
    fake_client = _FakeFlowClient()
    flow_mod, exc_mod, video_job_cls = _setup_fake_flow_env(fake_client)

    empty_img = tmp_path / "empty.png"
    empty_img.write_bytes(b"")

    p = ProductionFlowProvider()
    req = FlowGenerationRequest(
        job_id="job_empty_img",
        prompt="Animate this empty image",
        start_image_path=empty_img,
        count=1,
    )

    with patch.object(p, "_import_flow", return_value=(flow_mod.FlowClient, exc_mod, video_job_cls)):
        p._client = fake_client
        res = p.generate_video(req)

    assert res.status == FlowJobStatus.FAILED
    assert res.failure_class == FlowFailureClass.UNKNOWN
    assert "does not exist or is empty" in (res.failure_message or "")
    assert fake_client.generate_video.call_count == 0


def test_generate_video_valid_start_image_passed_to_upstream(tmp_path: Path):
    """If start_image_path is valid, it is passed to upstream client without degrading to T2V."""
    fake_client = _FakeFlowClient()
    flow_mod, exc_mod, video_job_cls = _setup_fake_flow_env(fake_client)

    valid_img = tmp_path / "input.png"
    valid_img.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00fake_image_data")

    p = ProductionFlowProvider(project_id="proj_xyz")
    req = FlowGenerationRequest(
        job_id="job_i2v_valid",
        prompt="Animate this valid photo",
        start_image_path=valid_img,
        count=1,
    )

    with patch.object(p, "_import_flow", return_value=(flow_mod.FlowClient, exc_mod, video_job_cls)):
        p._client = fake_client
        res = p.generate_video(req)

    assert res.status == FlowJobStatus.SUBMITTED
    assert fake_client.generate_video.call_count == 1
    call_kwargs = fake_client.generate_video.call_args[1]
    assert call_kwargs["start_image"] == str(valid_img)


def test_generate_video_success_with_fake_client():
    fake_client = _FakeFlowClient()
    flow_mod, exc_mod, video_job_cls = _setup_fake_flow_env(fake_client)

    p = ProductionFlowProvider(project_id="proj_xyz")
    req = FlowGenerationRequest(
        job_id="job_001",
        prompt="A cyberpunk pagoda at twilight",
        model=FlowModel.VEO_3_1_FAST,
        aspect_ratio=FlowAspectRatio.PORTRAIT_9_16,
        count=1,
    )

    with patch.object(p, "_import_flow", return_value=(flow_mod.FlowClient, exc_mod, video_job_cls)):
        p._client_cache = fake_client
        res = p.generate_video(req)

    assert res.status == FlowJobStatus.SUBMITTED
    assert res.provider_job_id == "media_abc123"
    assert res.credit_before == 240
    assert res.runtime_evidence["upstream"] == "eddie-fqh/flow-py"
    fake_client.generate_video.assert_called_once_with(
        prompt="A cyberpunk pagoda at twilight",
        model=FlowModel.VEO_3_1_FAST.value,
        aspect="portrait",
        count=1,
        start_image=None,
    )


def test_generate_video_maps_policy_error_to_policy_violation():
    fake_client = _FakeFlowClient()
    exc_mod = _make_stub_exc_module()
    fake_client.generate_video.side_effect = exc_mod.PolicyError("Sensitive keywords")
    flow_mod, _, video_job_cls = _setup_fake_flow_env(fake_client, exc_mod)

    p = ProductionFlowProvider()
    req = FlowGenerationRequest(
        job_id="job_policy",
        prompt="A policy triggering prompt",
        count=1,
    )

    with patch.object(p, "_import_flow", return_value=(flow_mod.FlowClient, exc_mod, video_job_cls)):
        p._client_cache = fake_client
        res = p.generate_video(req)

    assert res.status == FlowJobStatus.FAILED
    assert res.failure_class == FlowFailureClass.POLICY_VIOLATION
    assert "content policy" in (res.failure_message or "").lower()


def test_generate_video_maps_auth_error_to_user_interaction_required():
    fake_client = _FakeFlowClient()
    exc_mod = _make_stub_exc_module()
    fake_client.generate_video.side_effect = exc_mod.NotLoggedInError("Cookie expired")
    flow_mod, _, video_job_cls = _setup_fake_flow_env(fake_client, exc_mod)

    p = ProductionFlowProvider()
    req = FlowGenerationRequest(
        job_id="job_auth",
        prompt="A warrior with sword",
        count=1,
    )

    with patch.object(p, "_import_flow", return_value=(flow_mod.FlowClient, exc_mod, video_job_cls)):
        p._client_cache = fake_client
        res = p.generate_video(req)

    assert res.status == FlowJobStatus.USER_INTERACTION_REQUIRED
    assert res.failure_class == FlowFailureClass.USER_INTERACTION_REQUIRED


def test_poll_completed_with_fake_client():
    fake_client = _FakeFlowClient()
    flow_mod, exc_mod, video_job_cls = _setup_fake_flow_env(fake_client)
    fake_client._api.wait_for_video.return_value = _FakeVideoStatus(
        media_name="media_abc123", complete=True, fife_url="https://lh3.google.com/rendered.mp4"
    )

    p = ProductionFlowProvider()
    with patch.object(p, "_import_flow", return_value=(flow_mod.FlowClient, exc_mod, video_job_cls)):
        p._client_cache = fake_client
        res = p.poll("media_abc123")

    assert res.status == FlowJobStatus.COMPLETED
    assert res.provider_job_id == "media_abc123"
    assert res.runtime_evidence.get("fife_url") == "https://lh3.google.com/rendered.mp4"


def test_poll_pending_when_timeout():
    fake_client = _FakeFlowClient()
    exc_mod = _make_stub_exc_module()
    fake_client._api.wait_for_video.side_effect = exc_mod.GenerationTimeout("Still generating")
    flow_mod, _, video_job_cls = _setup_fake_flow_env(fake_client, exc_mod)

    p = ProductionFlowProvider()
    with patch.object(p, "_import_flow", return_value=(flow_mod.FlowClient, exc_mod, video_job_cls)):
        p._client_cache = fake_client
        res = p.poll("media_pending")

    assert res.status == FlowJobStatus.PENDING


def test_download_success_atomic_move(tmp_path: Path):
    fake_client = _FakeFlowClient()
    flow_mod, exc_mod, video_job_cls = _setup_fake_flow_env(fake_client)

    # Simulate fake download writing to the temp path
    async def _fake_download(fife_url, output_path):
        out = Path(output_path)
        out.write_bytes(b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00fake_video")
        return str(out)

    fake_client.download = AsyncMock(side_effect=_fake_download)

    p = ProductionFlowProvider()
    with patch.object(p, "_import_flow", return_value=(flow_mod.FlowClient, exc_mod, video_job_cls)):
        p._client_cache = fake_client
        files = p.download("media_abc123", tmp_path)

    assert len(files) == 1
    assert files[0].exists()
    assert files[0].stat().st_size > 0
    assert files[0].name == "media_abc123.mp4"


def test_download_returns_empty_list_on_failure(tmp_path: Path):
    fake_client = _FakeFlowClient()
    exc_mod = _make_stub_exc_module()
    fake_client._api.wait_for_video.side_effect = RuntimeError("Network error")
    flow_mod, _, video_job_cls = _setup_fake_flow_env(fake_client, exc_mod)

    p = ProductionFlowProvider()
    with patch.object(p, "_import_flow", return_value=(flow_mod.FlowClient, exc_mod, video_job_cls)):
        p._client_cache = fake_client
        files = p.download("media_fail", tmp_path)

    assert files == []


# =====================================================================
# 7. FLOW CONTROLLER INTEGRATION WITH FAKE PRODUCTION PROVIDER
# =====================================================================

def test_flow_controller_end_to_end_with_fake_production_provider(tmp_path: Path):
    fake_client = _FakeFlowClient()
    flow_mod, exc_mod, video_job_cls = _setup_fake_flow_env(fake_client)

    async def _fake_download(fife_url, output_path):
        out = Path(output_path)
        out.write_bytes(b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00fake_video")
        return str(out)

    fake_client.download = AsyncMock(side_effect=_fake_download)

    p = ProductionFlowProvider(project_id="proj_integration")
    with patch.object(p, "_import_flow", return_value=(flow_mod.FlowClient, exc_mod, video_job_cls)):
        p._client_cache = fake_client

        controller = FlowController(
            provider=p,
            storage_path=tmp_path / "flow_jobs.json",
            output_dir=tmp_path / "scenes",
        )

        req = FlowGenerationRequest(
            job_id="int_job_001",
            prompt="A majestic dragon in flight",
            model=FlowModel.VEO_3_1_FAST,
            aspect_ratio=FlowAspectRatio.PORTRAIT_9_16,
            count=1,
            output_dir=tmp_path / "scenes",
        )

        job_id = controller.submit(req)
        assert job_id == "int_job_001"

        # Process through controller
        result = controller.process_next()
        assert result is not None
        assert result.job_id == "int_job_001"
        assert result.status == FlowJobStatus.COMPLETED

        rec = controller.get_job_status("int_job_001")
        assert rec is not None
        assert rec.status == FlowJobStatus.COMPLETED

        paths = result.output_files
        assert len(paths) == 1
        assert paths[0].exists()
        assert rec.output_paths == [str(p) for p in paths] or [Path(p) for p in rec.output_paths] == paths


def test_finding_b_preserve_project_identity_and_restart(tmp_path: Path):
    """
    Finding B: Upstream project_id must be durable across submit, poll, download,
    and controller/provider restart without losing the active project context.
    """
    fake_client = _FakeFlowClient()
    flow_mod, exc_mod, video_job_cls = _setup_fake_flow_env(fake_client)

    async def _fake_download(fife_url, output_path):
        out = Path(output_path)
        out.write_bytes(b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00fake_video")
        return str(out)

    fake_client.download = AsyncMock(side_effect=_fake_download)

    # Submission session: project_id not given to provider constructor, but flow-py returns project_id in job
    p1 = ProductionFlowProvider()
    db_file = tmp_path / "flow_jobs.json"

    with patch.object(p1, "_import_flow", return_value=(flow_mod.FlowClient, exc_mod, video_job_cls)):
        p1._client_cache = fake_client

        controller1 = FlowController(
            provider=p1,
            storage_path=db_file,
            output_dir=tmp_path / "scenes",
        )

        req = FlowGenerationRequest(
            job_id="job_restart_proj",
            prompt="A misty bamboo forest",
            model=FlowModel.VEO_3_1_FAST,
            count=1,
            output_dir=tmp_path / "scenes",
        )

        res_sub = p1.generate_video(req)
        assert res_sub.status == FlowJobStatus.SUBMITTED
        assert res_sub.runtime_evidence.get("project_id") == "proj_1"

        # Durably save pending job in controller store with the upstream project_id
        rec1 = FlowJobRecord.from_request(req)
        rec1.provider_job_id = res_sub.provider_job_id
        rec1.project_id = res_sub.runtime_evidence.get("project_id")
        rec1.status = FlowJobStatus.PENDING
        controller1.store.save_job(rec1)

        saved_rec = controller1.get_job_status("job_restart_proj")
        assert saved_rec is not None
        assert saved_rec.project_id == "proj_1"

    # Restart session: new controller and new provider instance loaded from storage
    p2 = ProductionFlowProvider()
    with patch.object(p2, "_import_flow", return_value=(flow_mod.FlowClient, exc_mod, video_job_cls)):
        p2._client_cache = fake_client

        controller2 = FlowController(
            provider=p2,
            storage_path=db_file,
            output_dir=tmp_path / "scenes",
        )

        # Now upstream completes
        fake_client._api.wait_for_video.return_value = _FakeVideoStatus(
            media_name="media_proj_test", complete=True, fife_url="https://lh3.google.com/proj_video.mp4"
        )

        resumed = controller2.resume_pending_jobs()
        assert len(resumed) == 1
        assert resumed[0].status == FlowJobStatus.COMPLETED

        rec2 = controller2.get_job_status("job_restart_proj")
        assert rec2 is not None
        assert rec2.status == FlowJobStatus.COMPLETED
        assert rec2.project_id == "proj_1"


def test_finding_c_policy_rejection_does_not_pause_unrelated_jobs(tmp_path: Path):
    """
    Finding C: PolicyError on one job must fail terminally with POLICY_VIOLATION
    without pausing the controller or blocking the next unrelated job in the queue.
    """
    fake_client = _FakeFlowClient()
    exc_mod = _make_stub_exc_module()
    flow_mod, _, video_job_cls = _setup_fake_flow_env(fake_client, exc_mod)

    async def _fake_download(fife_url, output_path):
        out = Path(output_path)
        out.write_bytes(b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00fake_video")
        return str(out)

    fake_client.download = AsyncMock(side_effect=_fake_download)

    p = ProductionFlowProvider()
    with patch.object(p, "_import_flow", return_value=(flow_mod.FlowClient, exc_mod, video_job_cls)):
        p._client_cache = fake_client

        controller = FlowController(
            provider=p,
            storage_path=tmp_path / "flow_jobs.json",
            output_dir=tmp_path / "scenes",
        )

        # Job 1: Triggers PolicyError
        fake_client.generate_video.side_effect = exc_mod.PolicyError("Content policy violation")
        req1 = FlowGenerationRequest(
            job_id="job_policy_violating",
            prompt="Violating content prompt",
            count=1,
            output_dir=tmp_path / "scenes",
        )
        controller.submit(req1)

        # Job 2: Valid prompt
        req2 = FlowGenerationRequest(
            job_id="job_valid_next",
            prompt="Peaceful golden temple at sunrise",
            count=1,
            output_dir=tmp_path / "scenes",
        )
        controller.submit(req2)

        # Process Job 1 -> Fails with POLICY_VIOLATION
        res1 = controller.process_next()
        assert res1 is not None
        assert res1.job_id == "job_policy_violating"
        assert res1.status == FlowJobStatus.FAILED
        assert res1.failure_class == FlowFailureClass.POLICY_VIOLATION

        # Crucial check: Controller MUST NOT be paused!
        assert controller.is_paused is False

        # Reset generate_video for Job 2
        fake_client.generate_video.side_effect = None
        fake_client._api.wait_for_video.return_value = _FakeVideoStatus(
            media_name="media_valid_2", complete=True, fife_url="https://lh3.google.com/valid.mp4"
        )

        # Process Job 2 -> Succeeds
        res2 = controller.process_next()
        assert res2 is not None
        assert res2.job_id == "job_valid_next"
        assert res2.status == FlowJobStatus.COMPLETED


def test_finding_d_download_auth_error_triggers_safety_pause(tmp_path: Path):
    """
    Finding D: Auth/UI error during download must be classified as USER_INTERACTION_REQUIRED
    and trigger a controller safety pause rather than falling back to DOWNLOAD_FAILED.
    """
    fake_client = _FakeFlowClient()
    exc_mod = _make_stub_exc_module()
    flow_mod, _, video_job_cls = _setup_fake_flow_env(fake_client, exc_mod)

    # Upstream poll reports complete, but download raises NotLoggedInError
    fake_client._api.wait_for_video.return_value = _FakeVideoStatus(
        media_name="media_auth_dl", complete=True, fife_url="https://lh3.google.com/auth_fail.mp4"
    )
    fake_client.download.side_effect = exc_mod.NotLoggedInError("Session expired during download")

    p = ProductionFlowProvider()
    with patch.object(p, "_import_flow", return_value=(flow_mod.FlowClient, exc_mod, video_job_cls)):
        p._client_cache = fake_client

        controller = FlowController(
            provider=p,
            storage_path=tmp_path / "flow_jobs.json",
            output_dir=tmp_path / "scenes",
        )

        req = FlowGenerationRequest(
            job_id="job_auth_dl",
            prompt="A warrior looking at the horizon",
            count=1,
            output_dir=tmp_path / "scenes",
        )
        controller.submit(req)

        res = controller.process_next()
        assert res is not None
        assert res.status == FlowJobStatus.USER_INTERACTION_REQUIRED
        assert res.failure_class == FlowFailureClass.USER_INTERACTION_REQUIRED
        assert controller.is_paused is True

        rec = controller.get_job_status("job_auth_dl")
        assert rec is not None
        assert rec.status == FlowJobStatus.USER_INTERACTION_REQUIRED


def test_finding_d_download_corrupt_artifact_triggers_download_failed_without_pause(tmp_path: Path):
    """
    Finding D: Ordinary corrupt/zero-byte artifact during download must produce
    DOWNLOAD_FAILED without triggering the safety pause.
    """
    fake_client = _FakeFlowClient()
    flow_mod, exc_mod, video_job_cls = _setup_fake_flow_env(fake_client)

    # Download creates 0-byte file
    async def _fake_download_zero_byte(fife_url, output_path):
        out = Path(output_path)
        out.write_bytes(b"")
        return str(out)

    fake_client.download = AsyncMock(side_effect=_fake_download_zero_byte)
    fake_client._api.wait_for_video.return_value = _FakeVideoStatus(
        media_name="media_corrupt_dl", complete=True, fife_url="https://lh3.google.com/corrupt.mp4"
    )

    p = ProductionFlowProvider()
    with patch.object(p, "_import_flow", return_value=(flow_mod.FlowClient, exc_mod, video_job_cls)):
        p._client_cache = fake_client

        controller = FlowController(
            provider=p,
            storage_path=tmp_path / "flow_jobs.json",
            output_dir=tmp_path / "scenes",
        )

        req = FlowGenerationRequest(
            job_id="job_corrupt_dl",
            prompt="A mountain lake at dusk",
            count=1,
            output_dir=tmp_path / "scenes",
        )
        controller.submit(req)

        res = controller.process_next()
        assert res is not None
        assert res.status == FlowJobStatus.FAILED
        assert res.failure_class == FlowFailureClass.DOWNLOAD_FAILED
        assert controller.is_paused is False


def test_finding_a_cli_and_web_production_config_wiring(monkeypatch, tmp_path: Path):
    """
    Finding A: CLI and web must pass explicit supported configuration
    (FLOW_PROJECT_ID, FLOW_CDP_URL) to ProductionFlowProvider rather than obsolete arguments.
    """
    from auto_video_factory.cli import build_factory_from_args, build_parser
    from auto_video_factory.web import WebJobRequest, WebSettings, build_factory_for_request

    monkeypatch.setenv("FLOW_PROJECT_ID", "test_proj_env_123")
    monkeypatch.setenv("FLOW_CDP_URL", "http://127.0.0.1:9222")

    # 1. CLI build
    parser = build_parser()
    args = parser.parse_args(["--topic", "ancient legends", "--provider", "flow", "--output", str(tmp_path)])
    factory = build_factory_from_args(args)
    # The visual_provider has a controller whose provider is ProductionFlowProvider
    flow_prov = factory.image_provider.controller.provider
    assert isinstance(flow_prov, ProductionFlowProvider)
    assert flow_prov._project_id == "test_proj_env_123"
    assert flow_prov._cdp_url == "http://127.0.0.1:9222"

    # 2. Web build
    settings = WebSettings(output_root=tmp_path, flow_mock=False, provider="flow")
    req = WebJobRequest(topic="ancient legends", duration_seconds=45)
    web_factory = build_factory_for_request(
        request=req,
        settings=settings,
    )
    web_flow_prov = web_factory.image_provider.controller.provider
    assert isinstance(web_flow_prov, ProductionFlowProvider)
    assert web_flow_prov._project_id == "test_proj_env_123"
    assert web_flow_prov._cdp_url == "http://127.0.0.1:9222"


# =====================================================================
# 13. ASPECT RATIO TRANSLATION & UI HARDENING TESTS
# =====================================================================

@pytest.mark.parametrize(
    ("input_aspect", "expected_flow_aspect"),
    [
        (FlowAspectRatio.PORTRAIT_9_16, "portrait"),
        ("9:16", "portrait"),
        ("PORTRAIT", "portrait"),
        ("portrait", "portrait"),
        (FlowAspectRatio.LANDSCAPE_16_9, "landscape"),
        ("16:9", "landscape"),
        ("LANDSCAPE", "landscape"),
        ("landscape", "landscape"),
        (FlowAspectRatio.SQUARE_1_1, "square"),
        ("1:1", "square"),
        ("SQUARE", "square"),
        ("square", "square"),
    ],
)
def test_production_flow_provider_aspect_ratio_translation_matrix(
    input_aspect, expected_flow_aspect
):
    fake_client = _FakeFlowClient()
    flow_mod, exc_mod, video_job_cls = _setup_fake_flow_env(fake_client)

    p = ProductionFlowProvider(project_id="proj_aspect_test")
    req = FlowGenerationRequest(
        job_id="job_aspect",
        prompt="A serene lotus flower on a pond",
        model=FlowModel.OMNI_FLASH,
        aspect_ratio=input_aspect,
        count=1,
    )

    with patch.object(p, "_import_flow", return_value=(flow_mod.FlowClient, exc_mod, video_job_cls)):
        p._client_cache = fake_client
        res = p.generate_video(req)

    assert res.status == FlowJobStatus.SUBMITTED
    fake_client.generate_video.assert_called_once_with(
        prompt="A serene lotus flower on a pond",
        model="Omni Flash",
        aspect=expected_flow_aspect,
        count=1,
        start_image=None,
    )


def test_flow_visual_provider_preserves_portrait_contract(tmp_path: Path):
    from auto_video_factory.flow_provider.models import FlowHealthStatus
    from auto_video_factory.flow_provider.visual_provider import FlowVisualProvider
    from auto_video_factory.models import Scene

    mock_controller = MagicMock()
    mock_controller._is_safety_paused.return_value = False
    mock_controller.health.return_value = FlowHealthStatus(
        healthy=True,
        authenticated=True,
        profile_exists=True,
        browser_ready=True,
        details={"flow_url": "https://mock.flow"},
    )
    (tmp_path / "scene_01.mp4").write_bytes(b"dummy-mp4-data")
    mock_controller.submit.side_effect = lambda req: req.job_id
    mock_controller.process_job.side_effect = lambda jid: FlowJobResult(
        job_id=jid,
        provider_job_id="prov_123",
        status=FlowJobStatus.COMPLETED,
        output_files=[tmp_path / "scene_01.mp4"],
    )

    vp = FlowVisualProvider(
        controller=mock_controller,
        model=FlowModel.OMNI_FLASH,
        aspect_ratio=FlowAspectRatio.PORTRAIT_9_16,
    )

    scene = Scene(index=1, narration="Test narration", visual_prompt="A misty mountain path")
    out_file = tmp_path / "out.mp4"
    with patch.object(vp, "_strip_audio_if_present"):
        res_path = vp.create(scene, out_file)

    assert res_path == out_file
    mock_controller.submit.assert_called_once()
    submitted_req = mock_controller.submit.call_args[0][0]
    assert submitted_req.aspect_ratio == FlowAspectRatio.PORTRAIT_9_16
    assert submitted_req.model == FlowModel.OMNI_FLASH


def test_harden_client_ui_aspect_ratio_tab_selectors():
    """
    Test that _harden_client_ui installs a robust set_aspect_ratio method
    that correctly finds material icon ligatures, Vietnamese text, and data attributes.

    Uses pytest.importorskip so standard dev test runs without the prod flow extra installed.
    """
    # P3: guard with importorskip — test skips cleanly when prod flow extra absent
    flow_pkg = pytest.importorskip("flow", reason="prod flow extra not installed; skip")
    AspectRatio = flow_pkg.AspectRatio

    p = ProductionFlowProvider()
    mock_client = MagicMock()
    mock_ui = MagicMock()
    mock_ui._is_hardened = False
    mock_ui.open_settings_panel = AsyncMock()
    mock_client._ui = mock_ui

    exc_mod = _make_stub_exc_module()
    p._harden_client_ui(mock_client, exc_mod)
    assert mock_ui._is_hardened is True

    # 1. Page with matching role tab → clicks and returns True
    mock_page = MagicMock()
    mock_tab = MagicMock()
    mock_tab.count = AsyncMock(return_value=1)
    mock_tab.click = AsyncMock()
    mock_page.get_by_role.return_value.first = mock_tab

    res = p._run(mock_ui.set_aspect_ratio(mock_page, AspectRatio.PORTRAIT))
    assert res is True
    mock_tab.click.assert_called_once()

    # 2. Page where role matching fails but JS evaluate succeeds → True
    mock_page_eval = MagicMock()
    mock_page_eval.get_by_role.return_value.first.count = AsyncMock(return_value=0)
    mock_page_eval.locator.return_value.filter.return_value.first.count = AsyncMock(return_value=0)
    mock_page_eval.locator.return_value.first.count = AsyncMock(return_value=0)
    mock_page_eval.evaluate = AsyncMock(return_value=True)

    res_eval = p._run(mock_ui.set_aspect_ratio(mock_page_eval, AspectRatio.PORTRAIT))
    assert res_eval is True
    assert mock_page_eval.evaluate.call_count >= 1


def test_harden_client_ui_data_aspect_ratio_attribute():
    """
    P2: Verify that the JS evaluate selector string includes data-aspect-ratio comparison.
    A button with data-aspect-ratio="portrait" must match portrait, not landscape/square.
    Test is offline/mock-only — no paid Flow call.
    """
    flow_pkg = pytest.importorskip("flow", reason="prod flow extra not installed; skip")
    AspectRatio = flow_pkg.AspectRatio

    p = ProductionFlowProvider()
    mock_client = MagicMock()
    mock_ui = MagicMock()
    mock_ui._is_hardened = False
    mock_ui.open_settings_panel = AsyncMock()
    mock_client._ui = mock_ui

    exc_mod = _make_stub_exc_module()
    p._harden_client_ui(mock_client, exc_mod)

    # Simulate a page where role/text matching all fail, but evaluate sees a data-aspect-ratio button.
    # We simulate JS returning True for portrait (attribute matches) and False for landscape/square.
    mock_page = MagicMock()
    mock_page.get_by_role.return_value.first.count = AsyncMock(return_value=0)
    mock_page.locator.return_value.filter.return_value.first.count = AsyncMock(return_value=0)
    mock_page.locator.return_value.first.count = AsyncMock(return_value=0)

    # Portrait data-attribute matches → evaluate returns True
    mock_page.evaluate = AsyncMock(return_value=True)
    res = p._run(mock_ui.set_aspect_ratio(mock_page, AspectRatio.PORTRAIT))
    assert res is True

    # Verify the JS string passed to evaluate contains data-aspect-ratio
    eval_script = mock_page.evaluate.call_args[0][0]
    assert "data-aspect-ratio" in eval_script, (
        "JS evaluate must include data-aspect-ratio attribute comparison"
    )
    assert "dataAspect" in eval_script, (
        "JS evaluate must compare dataAspect variable for attribute matching"
    )

    # Landscape/square must NOT match a portrait data-attribute (evaluate returns False → UIError)
    mock_page_mismatch = MagicMock()
    mock_page_mismatch.get_by_role.return_value.first.count = AsyncMock(return_value=0)
    mock_page_mismatch.locator.return_value.filter.return_value.first.count = AsyncMock(return_value=0)
    mock_page_mismatch.locator.return_value.first.count = AsyncMock(return_value=0)
    mock_page_mismatch.evaluate = AsyncMock(return_value=False)

    import pytest as _pytest
    with _pytest.raises(exc_mod.UIError):
        p._run(mock_ui.set_aspect_ratio(mock_page_mismatch, AspectRatio.PORTRAIT))


def test_harden_client_ui_fail_closed_raises_uierror():
    """
    P1: When no aspect ratio tab can be found, robust_set_aspect_ratio must raise UIError
    so generate_video routes to USER_INTERACTION_REQUIRED, never silently submitting
    with the wrong default aspect ratio.
    """
    flow_pkg = pytest.importorskip("flow", reason="prod flow extra not installed; skip")
    AspectRatio = flow_pkg.AspectRatio

    p = ProductionFlowProvider()
    mock_client = MagicMock()
    mock_ui = MagicMock()
    mock_ui._is_hardened = False
    mock_ui.open_settings_panel = AsyncMock()
    mock_client._ui = mock_ui

    exc_mod = _make_stub_exc_module()
    p._harden_client_ui(mock_client, exc_mod)

    # All Playwright locators return 0, JS evaluate returns False → must raise UIError
    mock_page = MagicMock()
    mock_page.get_by_role.return_value.first.count = AsyncMock(return_value=0)
    mock_page.locator.return_value.filter.return_value.first.count = AsyncMock(return_value=0)
    mock_page.locator.return_value.first.count = AsyncMock(return_value=0)
    mock_page.evaluate = AsyncMock(return_value=False)

    import pytest as _pytest
    with _pytest.raises(exc_mod.UIError, match="Aspect ratio selection failed"):
        p._run(mock_ui.set_aspect_ratio(mock_page, AspectRatio.PORTRAIT))


def test_provider_does_not_proceed_when_portrait_selection_fails():
    """
    P1 integration: When the upstream flow-py client raises UIError during generate_video
    (because our hardened set_aspect_ratio raised it after failing to select the aspect tab),
    ProductionFlowProvider.generate_video must:
    - return USER_INTERACTION_REQUIRED
    - NOT emit a SUBMITTED result (no paid generation)

    The real propagation path: set_aspect_ratio raises UIError inside flow-py's
    FlowClient.generate_video, which propagates up to ProductionFlowProvider.generate_video's
    except-UIError handler. We simulate this by making fake_client.generate_video raise UIError.
    """
    fake_client = _FakeFlowClient()
    flow_mod, exc_mod, video_job_cls = _setup_fake_flow_env(fake_client)

    # Simulate the real propagation: UIError raised inside flow-py's generate_video
    # (from set_aspect_ratio failing) bubbles out to ProductionFlowProvider's except block.
    ui_error = exc_mod.UIError("portrait tab not found — fail-closed test")
    fake_client.generate_video = AsyncMock(side_effect=ui_error)

    p = ProductionFlowProvider(project_id="proj_fail_closed")
    req = FlowGenerationRequest(
        job_id="job_fail_closed",
        prompt="A sword cultivator awakens ancient sword intent",
        model=FlowModel.OMNI_FLASH,
        aspect_ratio=FlowAspectRatio.PORTRAIT_9_16,
        count=1,
    )

    with patch.object(p, "_import_flow", return_value=(flow_mod.FlowClient, exc_mod, video_job_cls)):
        with patch.object(p, "_get_client", return_value=fake_client):
            result = p.generate_video(req)

    # Must route to USER_INTERACTION_REQUIRED — never SUBMITTED
    assert result.status == FlowJobStatus.USER_INTERACTION_REQUIRED, (
        f"Expected USER_INTERACTION_REQUIRED, got {result.status}"
    )
    assert result.failure_class == FlowFailureClass.USER_INTERACTION_REQUIRED
    # generate_video was called (and raised UIError) — the result must be the error path,
    # not a successful submission that proceeded past the exception.
    fake_client.generate_video.assert_called_once()


def test_harden_client_ui_multi_locale_settings_and_aspect():
    """Verify hardened UI open_settings_panel, switch_mode, and set_aspect_ratio support multi-locale and evaluate fallbacks."""
    flow_pkg = pytest.importorskip("flow", reason="prod flow extra not installed; skip")
    AspectRatio = flow_pkg.AspectRatio

    fake_ui = MagicMock()
    fake_ui._is_hardened = False
    fake_ui.open_settings_panel = AsyncMock(return_value=False)
    fake_ui._settings_visible = AsyncMock(return_value=True)

    fake_client = MagicMock()
    fake_client._ui = fake_ui

    exc_mod = _make_stub_exc_module()

    provider = ProductionFlowProvider()
    provider._harden_client_ui(fake_client, exc_mod)

    assert fake_ui._is_hardened is True

    # Test open_settings_panel when evaluate returns True
    mock_page = MagicMock()
    mock_page.evaluate = AsyncMock(return_value=True)
    opened = provider._run(fake_ui.open_settings_panel(mock_page))
    assert opened is True

    # Test set_aspect_ratio portrait when evaluate finds portrait
    mock_page.get_by_role = MagicMock(return_value=MagicMock(first=MagicMock(count=AsyncMock(return_value=0))))
    mock_page.locator = MagicMock(return_value=MagicMock(filter=MagicMock(return_value=MagicMock(first=MagicMock(count=AsyncMock(return_value=0))))))
    mock_page.evaluate = AsyncMock(return_value=True)
    aspect_ok = provider._run(fake_ui.set_aspect_ratio(mock_page, AspectRatio.PORTRAIT))
    assert aspect_ok is True

