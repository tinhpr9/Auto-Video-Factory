"""
RED Test Suite for PR #15 Batches A-G:
A. Multi-process consistency & job_id collision
B. Fail-closed user interaction survival & concurrent safety gate
C. Request persistence & count / start_image identity
D. Durable retry state machine & polling retry without regeneration
E. Completion contract (reject empty / invalid downloads)
F. Submission crash safety (no duplicate billing on crash)
G. Production health verification
"""
import multiprocessing
import os
import tempfile
import time
from pathlib import Path

from auto_video_factory.flow_provider.contract import FlowProvider
from auto_video_factory.flow_provider.controller import FlowController
from auto_video_factory.flow_provider.models import (
    FlowAspectRatio,
    FlowFailureClass,
    FlowGenerationRequest,
    FlowHealthStatus,
    FlowJobRecord,
    FlowJobResult,
    FlowJobStatus,
    FlowModel,
)
from auto_video_factory.flow_provider.persistence import JobStateStore
from auto_video_factory.flow_provider.provider import MockFlowProvider, ProductionFlowProvider
from auto_video_factory.flow_provider.queue import RateLimiter, RetryPolicy


# =====================================================================
# BATCH A: PERSISTENCE / MULTI-PROCESS CONSISTENCY & ID COLLISION
# =====================================================================

def _worker_write_job(db_path_str: str, job_id: str, prompt: str):
    store = JobStateStore(Path(db_path_str))
    req = FlowGenerationRequest(
        job_id=job_id,
        prompt=prompt,
        model=FlowModel.VEO_3_1_FAST,
        aspect_ratio=FlowAspectRatio.PORTRAIT_9_16,
    )
    rec = FlowJobRecord.from_request(req, provider="flow")
    rec.status = FlowJobStatus.QUEUED
    store.save_job(rec)


def test_batch_a_multiprocess_writes_preserve_all_jobs():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "jobs.json"
        store = JobStateStore(db_path)

        p1 = multiprocessing.Process(target=_worker_write_job, args=(str(db_path), "proc_job_1", "Prompt 1"))
        p2 = multiprocessing.Process(target=_worker_write_job, args=(str(db_path), "proc_job_2", "Prompt 2"))

        p1.start()
        p2.start()
        p1.join(timeout=5)
        p2.join(timeout=5)

        all_jobs = store.list_all_jobs()
        job_ids = {j.job_id for j in all_jobs}
        assert "proc_job_1" in job_ids
        assert "proc_job_2" in job_ids
        assert len(all_jobs) == 2


def test_batch_a_custom_job_id_collision_rejected_for_different_request():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "jobs.json"
        provider = MockFlowProvider()
        controller = FlowController(provider=provider, storage_path=db_path, output_dir=Path(tmpdir))

        id1 = controller.submit("First unique prompt", job_id="custom_id_100")
        assert id1 == "custom_id_100"

        # Idempotent resubmission of identical request succeeds and returns same ID
        id1_same = controller.submit("First unique prompt", job_id="custom_id_100")
        assert id1_same == "custom_id_100"

        # Submission of DIFFERENT prompt with same custom job_id must raise ValueError
        try:
            controller.submit("Completely different prompt", job_id="custom_id_100")
            assert False, "Expected ValueError on custom job_id collision with different request"
        except ValueError as e:
            assert "collision" in str(e).lower() or "exists" in str(e).lower()


# =====================================================================
# BATCH B: FAIL-CLOSED USER INTERACTION
# =====================================================================

def test_batch_b_safety_pause_survives_restart():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "jobs.json"
        store1 = JobStateStore(db_path)

        req = FlowGenerationRequest(
            job_id="job_captcha_1",
            prompt="Triggers captcha",
            model=FlowModel.VEO_3_1_FAST,
        )
        rec = FlowJobRecord.from_request(req, provider="flow")
        rec.status = FlowJobStatus.USER_INTERACTION_REQUIRED
        rec.failure_class = FlowFailureClass.USER_INTERACTION_REQUIRED
        store1.save_job(rec)

        # Fresh controller initialized from storage
        provider = MockFlowProvider()
        controller = FlowController(provider=provider, storage_path=db_path, output_dir=Path(tmpdir))

        # Must initialize paused
        assert controller.is_paused is True
        # process_next must return None without executing
        assert controller.process_next() is None


def test_batch_b_concurrent_workers_stop_after_interaction_pause():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "jobs.json"

        class CaptchaThenNormalProvider(MockFlowProvider):
            def __init__(self):
                super().__init__()
                self.calls = 0

            def generate_video(self, request):
                self.calls += 1
                if self.calls == 1:
                    return FlowJobResult(
                        job_id=request.job_id,
                        provider_job_id="",
                        status=FlowJobStatus.USER_INTERACTION_REQUIRED,
                        failure_class=FlowFailureClass.USER_INTERACTION_REQUIRED,
                        failure_message="Captcha detected",
                    )
                return super().generate_video(request)

        provider = CaptchaThenNormalProvider()
        controller = FlowController(provider=provider, storage_path=db_path, output_dir=Path(tmpdir))

        controller.submit("Prompt 1")
        controller.submit("Prompt 2")

        res1 = controller.process_next()
        assert res1.status == FlowJobStatus.USER_INTERACTION_REQUIRED
        assert controller.is_paused is True

        res2 = controller.process_next()
        assert res2 is None
        # Provider should have been called only once; subsequent calls must not hit provider
        assert provider.calls == 1


# =====================================================================
# BATCH C: REQUEST PERSISTENCE & COUNT / START_IMAGE RECOVERY
# =====================================================================

def test_batch_c_count_and_start_image_survive_restart():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "jobs.json"
        store1 = JobStateStore(db_path)

        req = FlowGenerationRequest(
            job_id="job_multi_img",
            prompt="Animate floral patterns",
            model=FlowModel.VEO_3_1_FAST,
            aspect_ratio=FlowAspectRatio.PORTRAIT_9_16,
            count=3,
            start_image_path=Path("/tmp/flower.png"),
        )
        rec = FlowJobRecord.from_request(req, provider="flow")
        store1.save_job(rec)

        store2 = JobStateStore(db_path)
        loaded = store2.get_job("job_multi_img")
        assert loaded.count == 3
        assert str(loaded.start_image_path) == "/tmp/flower.png"


def test_batch_c_dedup_hash_differentiates_count():
    req1 = FlowGenerationRequest(
        job_id="j1",
        prompt="Sample prompt",
        count=1,
    )
    req2 = FlowGenerationRequest(
        job_id="j2",
        prompt="Sample prompt",
        count=2,
    )
    assert req1.prompt_hash != req2.prompt_hash


# =====================================================================
# BATCH D: DURABLE RETRY STATE MACHINE & POLLING RETRY
# =====================================================================

def test_batch_d_retryable_submission_persisted_in_retry_state():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "jobs.json"
        provider = MockFlowProvider(simulate_failure=FlowFailureClass.NETWORK)
        policy = RetryPolicy(max_retries=2, base_delay_s=0.01)
        controller = FlowController(
            provider=provider,
            storage_path=db_path,
            output_dir=Path(tmpdir),
            retry_policy=policy,
        )

        job_id = controller.submit("Retryable network prompt")
        res = controller.process_next()

        assert res.status == FlowJobStatus.QUEUED or res.failure_class == FlowFailureClass.NETWORK
        # Store should NOT leave it in terminal FAILED; it should be QUEUED or RETRY_WAIT in store and queue
        rec = controller.get_job_status(job_id)
        assert rec.status in (FlowJobStatus.QUEUED, FlowJobStatus.RETRY_WAIT)


def test_batch_d_polling_transient_failure_retries_poll_without_regeneration():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "jobs.json"

        class FlakyPollProvider(MockFlowProvider):
            def __init__(self):
                super().__init__()
                self.gen_count = 0
                self.poll_count = 0

            def generate_video(self, request):
                self.gen_count += 1
                return super().generate_video(request)

            def poll(self, provider_job_id):
                self.poll_count += 1
                if self.poll_count == 1:
                    # Transient network error on poll
                    return FlowJobResult(
                        job_id="",
                        provider_job_id=provider_job_id,
                        status=FlowJobStatus.FAILED,
                        failure_class=FlowFailureClass.NETWORK,
                        failure_message="Temporary network hiccup during poll",
                    )
                return super().poll(provider_job_id)

        provider = FlakyPollProvider()
        controller = FlowController(
            provider=provider,
            storage_path=db_path,
            output_dir=Path(tmpdir),
            poll_interval_s=0.01,
            retry_policy=RetryPolicy(max_retries=3, base_delay_s=0.01),
        )

        controller.submit("Flaky poll prompt")
        res = controller.process_next()

        assert res.status == FlowJobStatus.COMPLETED
        # Generation must happen exactly ONCE
        assert provider.gen_count == 1
        # Polling happened twice (1 failed network + 1 completed)
        assert provider.poll_count == 2


# =====================================================================
# BATCH E: COMPLETION / ARTIFACT CONTRACT
# =====================================================================

def test_batch_e_empty_download_fails_with_download_failed():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "jobs.json"

        class EmptyDownloadProvider(MockFlowProvider):
            def download(self, provider_job_id, output_dir):
                return []  # Empty list

        provider = EmptyDownloadProvider()
        controller = FlowController(provider=provider, storage_path=db_path, output_dir=Path(tmpdir))

        job_id = controller.submit("Empty download prompt")
        res = controller.process_next()

        assert res.status == FlowJobStatus.FAILED
        assert res.failure_class == FlowFailureClass.DOWNLOAD_FAILED

        rec = controller.get_job_status(job_id)
        assert rec.status == FlowJobStatus.FAILED
        assert rec.failure_class == FlowFailureClass.DOWNLOAD_FAILED


# =====================================================================
# BATCH F: SUBMISSION CRASH SAFETY & DUPLICATE BILLING PREVENTION
# =====================================================================

def test_batch_f_ambiguous_generating_job_on_restart_marks_ambiguous_no_auto_regenerate():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "jobs.json"
        store1 = JobStateStore(db_path)

        # Record left in GENERATING state with no provider_job_id (process crashed during generate_video)
        rec = FlowJobRecord(
            job_id="crash_mid_gen_1",
            prompt="Ambiguous crash prompt",
            prompt_hash="ambig_hash_1",
            provider="flow",
            model="veo_3_1_t2v_fast",
            aspect_ratio="9:16",
            priority=5,
            status=FlowJobStatus.GENERATING,
            provider_job_id=None,
        )
        store1.save_job(rec)

        provider = MockFlowProvider()
        controller = FlowController(provider=provider, storage_path=db_path, output_dir=Path(tmpdir))

        # resume_pending_jobs must transition ambiguous job to SUBMISSION_AMBIGUOUS / NEEDS_RECONCILIATION
        # and NOT push to queue to avoid double-charging
        resumed = controller.resume_pending_jobs()
        assert controller.queue.is_empty() is True

        rec_updated = controller.get_job_status("crash_mid_gen_1")
        assert rec_updated.status in (FlowJobStatus.SUBMISSION_AMBIGUOUS, FlowJobStatus.FAILED)


# =====================================================================
# BATCH G: PRODUCTION HEALTH CONTRACT
# =====================================================================

def test_batch_g_unauthenticated_production_provider_fails_health():
    prod_provider = ProductionFlowProvider(auth_token=None, profile_path=None)
    health = prod_provider.health()

    assert health.healthy is False
    assert health.authenticated is False
    assert health.browser_ready is False
    assert "missing_auth" in health.details.get("reason", "") or "unauthenticated" in health.details.get("reason", "")
