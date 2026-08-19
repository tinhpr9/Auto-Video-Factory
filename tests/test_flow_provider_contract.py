"""
Tests for Flow Provider Contract, Data Models, and Failure Classification.
"""
from pathlib import Path
from auto_video_factory.flow_provider.models import (
    FlowFailureClass,
    FlowJobStatus,
    FlowAspectRatio,
    FlowModel,
    FlowGenerationRequest,
    FlowJobResult,
    FlowCredits,
    FlowModelInfo,
    FlowHealthStatus,
)
from auto_video_factory.flow_provider.contract import FlowProvider
from auto_video_factory.flow_provider.provider import MockFlowProvider


def test_failure_classification_members():
    expected_classes = {
        "AUTH",
        "SESSION_EXPIRED",
        "USER_INTERACTION_REQUIRED",
        "SELECTOR_CHANGED",
        "UI_CHANGED",
        "NETWORK",
        "RATE_LIMIT",
        "QUOTA",
        "MODEL_UNAVAILABLE",
        "TIMEOUT",
        "DOWNLOAD_FAILED",
        "UNKNOWN",
    }
    actual_classes = {c.value for c in FlowFailureClass}
    assert expected_classes.issubset(actual_classes), f"Missing classes: {expected_classes - actual_classes}"


def test_generation_request_defaults_and_hashing():
    req = FlowGenerationRequest(
        job_id="job_001",
        prompt="A serene golden lotus blossoming on water",
        model=FlowModel.VEO_3_1_FAST,
        aspect_ratio=FlowAspectRatio.PORTRAIT_9_16,
        count=1,
        output_dir=Path("./output"),
    )
    assert req.job_id == "job_001"
    assert req.prompt_hash is not None
    assert len(req.prompt_hash) == 64  # SHA256 hex


def test_mock_flow_provider_contract_methods():
    provider: FlowProvider = MockFlowProvider(initial_credits=200)
    
    # 1. Health check
    health = provider.health()
    assert isinstance(health, FlowHealthStatus)
    assert health.healthy is True
    
    # 2. Credits check
    credits = provider.get_credits()
    assert isinstance(credits, FlowCredits)
    assert credits.available_credits == 200
    
    # 3. Models list
    models = provider.get_models()
    assert len(models) > 0
    assert any(m.model_id == FlowModel.VEO_3_1_FAST.value for m in models)
    
    # 4. Generate video
    req = FlowGenerationRequest(
        job_id="job_test_01",
        prompt="Cyberpunk neon market in Hanoi",
        model=FlowModel.VEO_3_1_FAST,
        aspect_ratio=FlowAspectRatio.PORTRAIT_9_16,
        count=1,
        output_dir=Path("/tmp/flow_test"),
    )
    result = provider.generate_video(req)
    assert isinstance(result, FlowJobResult)
    assert result.status in (FlowJobStatus.SUBMITTED, FlowJobStatus.PENDING, FlowJobStatus.COMPLETED)
    assert result.provider_job_id != ""
    assert result.credit_before == 200
    assert result.credit_after == 180  # 20 credits cost for VEO_3_1_FAST
    
    # 5. Poll
    polled = provider.poll(result.provider_job_id)
    assert isinstance(polled, FlowJobResult)
    assert polled.status == FlowJobStatus.COMPLETED
    
    # 6. Download
    files = provider.download(result.provider_job_id, Path("/tmp/flow_test"))
    assert isinstance(files, list)
    assert len(files) > 0
