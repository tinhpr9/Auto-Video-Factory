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


def test_constructor_legacy_kwargs_accepted_without_crash():
    """Legacy kwargs (auth_token, profile_path, cdp_endpoint) must not break callers."""
    p = ProductionFlowProvider(
        auth_token="token_abc",
        profile_path=Path("/tmp/profile"),
        cdp_endpoint="http://127.0.0.1:9222",
    )
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
    with patch.dict(sys.modules, {"flow": flow_mod, "flow._exceptions": exc_mod, "flow._api": api_mod, "flow._storage": storage_mod}):
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
    assert ProductionFlowProvider._classify_exception(mod.PolicyError("bad content"), mod) == FlowFailureClass.USER_INTERACTION_REQUIRED


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


def test_generate_video_maps_policy_error_to_user_interaction_required():
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
    assert res.failure_class == FlowFailureClass.USER_INTERACTION_REQUIRED
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
