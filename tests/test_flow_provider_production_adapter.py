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


# =====================================================================
# 4. ASYNC-TO-SYNC BRIDGE (_run)
# =====================================================================

def test_run_bridge_executes_coroutine():
    async def _coro():
        return "result_42"

    assert ProductionFlowProvider._run(_coro()) == "result_42"


def test_run_bridge_propagates_exceptions():
    async def _failing():
        raise ValueError("coroutine failed")

    with pytest.raises(ValueError, match="coroutine failed"):
        ProductionFlowProvider._run(_failing())


def test_run_bridge_inside_running_loop():
    """When called inside a running event loop, _run executes in a separate thread pool."""
    async def _outer():
        async def _inner():
            return "inner_done"
        return ProductionFlowProvider._run(_inner())

    result = asyncio.run(_outer())
    assert result == "inner_done"


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
        self.generate_video = AsyncMock(return_value=[_FakeVideoJob("media_abc123")])
        self.download = AsyncMock(return_value=None)


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
        p._client_cache = fake_client
        creds = p.get_credits()

    assert creds.available_credits == 240
    assert creds.total_credits == 240
    assert creds.consumed_credits == 0


def test_get_models_returns_standard_veo_models():
    p = ProductionFlowProvider()
    models = p.get_models()
    assert len(models) == 3
    model_ids = {m.model_id for m in models}
    assert FlowModel.VEO_3_1_FAST.value in model_ids
    assert FlowModel.VEO_3_1_QUALITY.value in model_ids
    assert FlowModel.VEO_2_1_FAST.value in model_ids


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
