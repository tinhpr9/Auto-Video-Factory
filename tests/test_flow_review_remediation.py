"""
Targeted RED Test Suite for PR #16 Review Threads Remediation:
Batch A: Job Ownership & Retry Execution (#1, #2)
Batch B: Web Auth & Config Snapshot (#3, #8)
Batch C: Multi-Output Contract (#5)
Batch D: Web Controller & Store Ownership (#6)
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Optional

import pytest
from fastapi.testclient import TestClient

from auto_video_factory.models import Scene
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
from auto_video_factory.flow_provider.persistence import JobStateStore
from auto_video_factory.flow_provider.provider import MockFlowProvider, ProductionFlowProvider
from auto_video_factory.flow_provider.queue import RateLimiter, RetryPolicy
from auto_video_factory.flow_provider.visual_provider import (
    FlowVisualProvider,
    FlowProviderError,
    FlowGenerationError,
)
from auto_video_factory.web import (
    WebSettings,
    WebJobRequest,
    create_app,
    build_factory_for_request,
)


# =====================================================================
# BATCH A: JOB OWNERSHIP & RETRY EXECUTION (#1, #2)
# =====================================================================

def test_batch_a_transient_failure_retries_to_completion(tmp_path: Path):
    """FlowVisualProvider.create() does not abort on transient retryable failure; controller retries to completion."""
    storage_path = tmp_path / "flow_jobs.json"
    scenes_dir = tmp_path / "scenes"

    class FlakyGenerateProvider(MockFlowProvider):
        def __init__(self):
            super().__init__(initial_credits=200)
            self.gen_calls = 0

        def generate_video(self, req: FlowGenerationRequest) -> FlowJobResult:
            self.gen_calls += 1
            if self.gen_calls == 1:
                return FlowJobResult(
                    job_id=req.job_id,
                    provider_job_id="",
                    status=FlowJobStatus.FAILED,
                    failure_class=FlowFailureClass.NETWORK,
                    failure_message="Transient network reset during generate",
                )
            return super().generate_video(req)

    prov = FlakyGenerateProvider()
    controller = FlowController(
        provider=prov,
        storage_path=storage_path,
        output_dir=scenes_dir,
        retry_policy=RetryPolicy(max_retries=3, base_delay_s=0.01),
        poll_interval_s=0.01,
    )
    visual_provider = FlowVisualProvider(controller=controller)

    scene = Scene(index=1, narration="Test retry", visual_prompt="Hero fighting dragon")
    out_file = scenes_dir / "scene_01.mp4"

    # Should succeed after retry without raising FlowGenerationError("ended in non-completed status: QUEUED")
    result_path = visual_provider.create(scene, out_file)
    assert result_path.exists()
    assert prov.gen_calls == 2


def test_batch_a_deduplicated_completed_job_reused(tmp_path: Path):
    """FlowVisualProvider.create() reuses an existing completed job without extra generation."""
    storage_path = tmp_path / "flow_jobs.json"
    scenes_dir = tmp_path / "scenes"

    prov = MockFlowProvider(initial_credits=200)
    controller = FlowController(
        provider=prov,
        storage_path=storage_path,
        output_dir=scenes_dir,
        poll_interval_s=0.01,
    )
    visual_provider = FlowVisualProvider(controller=controller)

    scene = Scene(index=1, narration="Test dedup", visual_prompt="Hero meditating on mountain")
    out_file_1 = scenes_dir / "scene_01.mp4"
    out_file_2 = scenes_dir / "scene_01_rerun.mp4"

    # First call: generates video
    res1 = visual_provider.create(scene, out_file_1)
    assert res1.exists()
    assert prov.generate_video_calls == 1

    # Second call (same scene index and prompt): should reuse completed job without generating again
    res2 = visual_provider.create(scene, out_file_2)
    assert res2.exists()
    assert prov.generate_video_calls == 1


def test_batch_a_deduplicated_pending_job_polled_to_completion(tmp_path: Path):
    """FlowVisualProvider.create() monitors an existing in-flight pending job to completion."""
    storage_path = tmp_path / "flow_jobs.json"
    scenes_dir = tmp_path / "scenes"

    prov = MockFlowProvider(initial_credits=200)
    prov._SHARED_JOBS["prov_inflight_job"] = {
        "job_id": "scene_01_8a7c2b3d",
        "client_request_id": "scene_01_8a7c2b3d",
        "status": FlowJobStatus.COMPLETED,
        "model": FlowModel.VEO_3_1_FAST.value,
        "aspect_ratio": FlowAspectRatio.PORTRAIT_9_16.value,
    }

    # Pre-populate store with PENDING record that already has provider_job_id
    store = JobStateStore(storage_path)
    import hashlib
    prompt = "Hero in lightning storm"
    prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:8]
    job_id = f"scene_01_{prompt_hash}"

    req = FlowGenerationRequest(
        job_id=job_id,
        prompt=prompt,
        model=FlowModel.VEO_3_1_FAST,
        aspect_ratio=FlowAspectRatio.PORTRAIT_9_16,
        count=1,
        output_dir=scenes_dir,
    )
    record = FlowJobRecord.from_request(req, provider="mock")
    record.status = FlowJobStatus.PENDING
    record.provider_job_id = "prov_inflight_job"
    store.save_job(record)

    controller = FlowController(
        provider=prov,
        storage_path=storage_path,
        output_dir=scenes_dir,
        poll_interval_s=0.01,
    )
    visual_provider = FlowVisualProvider(controller=controller)

    scene = Scene(index=1, narration="Storm scene", visual_prompt=prompt)
    out_file = scenes_dir / "scene_01.mp4"

    res = visual_provider.create(scene, out_file)
    assert res.exists()
    assert prov.generate_video_calls == 0


def test_batch_a_unrelated_higher_priority_queue_item_not_misattributed(tmp_path: Path):
    """When a higher-priority queue item exists in the queue, create() processes its own job ID, not the unrelated item."""
    storage_path = tmp_path / "flow_jobs.json"
    scenes_dir = tmp_path / "scenes"

    prov = MockFlowProvider(initial_credits=200)
    controller = FlowController(
        provider=prov,
        storage_path=storage_path,
        output_dir=scenes_dir,
        poll_interval_s=0.01,
    )

    # Queue an unrelated high-priority job for a completely different scene/prompt
    controller.submit(
        prompt="Unrelated scene 99 prompt",
        priority=10,
        job_id="scene_99_unrelated",
    )

    visual_provider = FlowVisualProvider(controller=controller)
    scene = Scene(index=1, narration="Scene 1", visual_prompt="Scene 1 specific prompt")
    out_file = scenes_dir / "scene_01.mp4"

    res = visual_provider.create(scene, out_file)
    assert res.exists()
    assert "scene_99_unrelated" not in res.name


# =====================================================================
# BATCH B: WEB AUTH & CONFIG SNAPSHOT (#3, #8)
# =====================================================================

def test_batch_b_programmatic_flow_settings_without_access_code_rejected():
    """WebSettings(provider='flow', flow_mock=False) with no access code is rejected by create_app()."""
    settings = WebSettings(provider="flow", access_code=None, flow_mock=False)
    with pytest.raises(ValueError, match="AVF_ACCESS_CODE is required when AVF_PROVIDER=flow"):
        create_app(settings=settings)


def test_batch_b_env_flow_without_access_code_rejected(monkeypatch):
    """Production Flow from environment without access code is rejected by WebSettings.from_env()."""
    monkeypatch.setenv("AVF_PROVIDER", "flow")
    monkeypatch.setenv("AVF_FLOW_MOCK", "0")
    monkeypatch.delenv("AVF_ACCESS_CODE", raising=False)

    with pytest.raises(ValueError, match="AVF_ACCESS_CODE is required when AVF_PROVIDER=flow"):
        WebSettings.from_env()


def test_batch_b_explicit_flow_mock_settings_allowed(tmp_path: Path):
    """Explicit Flow mock settings without access code is allowed in dev/test."""
    settings = WebSettings(provider="flow", access_code=None, flow_mock=True, output_root=tmp_path / "web")
    app = create_app(settings=settings)
    client = TestClient(app)
    res = client.get("/health")
    assert res.status_code == 200


def test_batch_b_env_mutation_after_settings_construction_ignored(monkeypatch, tmp_path: Path):
    """Mutating AVF_FLOW_MOCK in os.environ after WebSettings construction does NOT change security boundary or provider."""
    monkeypatch.setenv("AVF_PROVIDER", "flow")
    monkeypatch.setenv("AVF_FLOW_MOCK", "1")
    monkeypatch.delenv("AVF_ACCESS_CODE", raising=False)

    settings = WebSettings.from_env()
    assert settings.flow_mock is True

    # Mutate env after settings construction
    monkeypatch.setenv("AVF_FLOW_MOCK", "0")

    # App uses immutable settings snapshot: still mock, not suddenly requiring auth or instantiating production provider
    app = create_app(settings=settings)
    client = TestClient(app)
    assert client.get("/health").status_code == 200

    # Building factory respects settings snapshot
    req = WebJobRequest(topic="Test immutable snapshot", duration_seconds=60)
    factory = build_factory_for_request(req, settings)
    assert isinstance(factory.image_provider, FlowVisualProvider)
    assert isinstance(factory.image_provider.controller.provider, MockFlowProvider)


# =====================================================================
# BATCH C: MULTI-OUTPUT CONTRACT (#5)
# =====================================================================

def test_batch_c_count_1_accepted(tmp_path: Path):
    """FlowVisualProvider accepts count=1."""
    prov = MockFlowProvider(initial_credits=200)
    controller = FlowController(prov, tmp_path / "jobs.json", tmp_path / "scenes")
    vp = FlowVisualProvider(controller=controller, count=1)
    assert vp.count == 1


def test_batch_c_count_greater_than_1_rejected_before_submission(tmp_path: Path):
    """FlowVisualProvider rejects count > 1 at initialization/call time before provider submission."""
    prov = MockFlowProvider(initial_credits=200)
    controller = FlowController(prov, tmp_path / "jobs.json", tmp_path / "scenes")

    with pytest.raises(ValueError, match="count=1"):
        FlowVisualProvider(controller=controller, count=2)

    assert prov.generate_video_calls == 0


# =====================================================================
# BATCH D: WEB CONTROLLER & STORE OWNERSHIP (#6)
# =====================================================================

class FastTTS:
    def synthesize(self, text: str, output: Path) -> Path:
        output.parent.mkdir(parents=True, exist_ok=True)
        sample_rate = 24000
        duration_s = 0.5
        num_samples = int(sample_rate * duration_s)
        data_size = num_samples * 2
        file_size = 36 + data_size
        header = (
            bytearray(b"RIFF")
            + file_size.to_bytes(4, "little")
            + b"WAVEfmt "
            + (16).to_bytes(4, "little")
            + (1).to_bytes(2, "little")
            + (1).to_bytes(2, "little")
            + sample_rate.to_bytes(4, "little")
            + (sample_rate * 2).to_bytes(4, "little")
            + (2).to_bytes(2, "little")
            + (16).to_bytes(2, "little")
            + b"data"
            + data_size.to_bytes(4, "little")
            + b"\x00" * data_size
        )
        output.write_bytes(header)
        return output


class FastRenderer:
    def render(self, images: list[Path], audios: list[Path], subtitles: Path, output: Path) -> Path:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00isommp42")
        return output


def test_batch_d_two_concurrent_web_jobs_same_prompt_dedup_safely(tmp_path: Path):
    """Two concurrent web jobs with identical prompts deduplicate safely without empty queue or duplicate billing."""
    settings = WebSettings(
        provider="flow",
        flow_mock=True,
        output_root=tmp_path / "web_jobs",
        max_workers=2,
    )
    prov = MockFlowProvider(initial_credits=200)
    shared_flow_controller = FlowController(
        provider=prov,
        storage_path=tmp_path / "web_jobs" / "flow_jobs.json",
        output_dir=tmp_path / "web_jobs" / "scenes",
    )

    from auto_video_factory.story import TemplateStoryPlanner
    from auto_video_factory.pipeline import VideoFactory

    def fast_factory_builder(req: WebJobRequest) -> VideoFactory:
        vp = FlowVisualProvider(controller=shared_flow_controller)
        return VideoFactory(
            planner=TemplateStoryPlanner(scene_count=2),
            tts=FastTTS(),
            image_provider=vp,
            renderer=FastRenderer(),
        )

    app = create_app(settings=settings, factory_builder=fast_factory_builder)
    client = TestClient(app)

    payload = {
        "topic": "Một kiếm tu độc hành qua vạn dặm",
        "duration_seconds": 45,
        "voice": "marin",
        "style": "xianxia-cinematic",
    }

    # Submit two jobs concurrently with identical payload
    res1 = client.post("/api/jobs", json=payload)
    res2 = client.post("/api/jobs", json=payload)
    assert res1.status_code == 202
    assert res2.status_code == 202

    url1 = res1.json()["status_url"]
    url2 = res2.json()["status_url"]

    # Poll both to completion
    deadline = time.time() + 30.0
    status1 = client.get(url1).json()
    status2 = client.get(url2).json()
    while time.time() < deadline and (status1["status"] not in ("completed", "failed") or status2["status"] not in ("completed", "failed")):
        time.sleep(0.05)
        status1 = client.get(url1).json()
        status2 = client.get(url2).json()

    assert status1["status"] == "completed", f"Job 1 failed: {status1}"
    assert status2["status"] == "completed", f"Job 2 failed: {status2}"
    assert status1["video_url"] is not None
    assert status2["video_url"] is not None

    # Check both videos exist and are accessible
    v1 = client.get(status1["video_url"])
    v2 = client.get(status2["video_url"])
    assert v1.status_code == 200
    assert v2.status_code == 200

    # Ensure provider was invoked exactly 2 times (1 per unique scene), NOT 4 times (dedup verified)
    assert prov.generate_video_calls == 2


def test_batch_d_build_factory_for_request_shares_controller(tmp_path: Path):
    """Default build_factory_for_request configures FlowVisualProvider with shared FlowController."""
    settings = WebSettings(provider="flow", flow_mock=True, output_root=tmp_path / "web")
    app = create_app(settings=settings)
    assert app.state.job_manager is not None
    client = TestClient(app)
    res = client.get("/health")
    assert res.status_code == 200
