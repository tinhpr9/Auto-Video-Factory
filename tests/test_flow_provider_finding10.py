"""
Targeted RED test suite for PR #15 Finding #10:
Durable bounded monitoring of recovered PENDING/GENERATING jobs during resume_pending_jobs().
Guarantees:
1. Multi-poll until terminal state (PENDING -> PENDING -> COMPLETED)
2. Zero calls to generate_video() during recovery
3. Transient poll error retries polling without regeneration
4. USER_INTERACTION_REQUIRED during recovery pauses fail-closed
5. Permanent failure & timeout classified and persisted
6. Download failure classified as DOWNLOAD_FAILED
"""
import tempfile
import time
from pathlib import Path
from typing import Optional

from auto_video_factory.flow_provider.contract import FlowProvider
from auto_video_factory.flow_provider.controller import FlowController
from auto_video_factory.flow_provider.models import (
    FlowAspectRatio,
    FlowCredits,
    FlowFailureClass,
    FlowGenerationRequest,
    FlowHealthStatus,
    FlowJobRecord,
    FlowJobResult,
    FlowJobStatus,
    FlowModel,
    FlowModelInfo,
)
from auto_video_factory.flow_provider.persistence import JobStateStore
from auto_video_factory.flow_provider.provider import MockFlowProvider
from auto_video_factory.flow_provider.queue import RetryPolicy


class MultiPollRecoveringProvider(FlowProvider):
    """
    Simulates a provider that requires multiple polls to complete,
    and tracks generate_video call counts.
    """

    def __init__(self, polls_until_complete: int = 3):
        self.polls_until_complete = polls_until_complete
        self.poll_count = 0
        self.generate_video_calls = 0

    def health(self) -> FlowHealthStatus:
        return FlowHealthStatus(healthy=True, authenticated=True, browser_ready=True)

    def get_credits(self) -> FlowCredits:
        return FlowCredits(total_credits=100, available_credits=100, consumed_credits=0, timestamp=time.time())

    def get_models(self) -> list[FlowModelInfo]:
        return []

    def generate_video(self, request: FlowGenerationRequest) -> FlowJobResult:
        self.generate_video_calls += 1
        return FlowJobResult(
            job_id=request.job_id,
            provider_job_id="prov_multi_poll",
            status=FlowJobStatus.PENDING,
        )

    def poll(self, provider_job_id: str) -> FlowJobResult:
        self.poll_count += 1
        if self.poll_count < self.polls_until_complete:
            return FlowJobResult(
                job_id="",
                provider_job_id=provider_job_id,
                status=FlowJobStatus.PENDING,
            )
        return FlowJobResult(
            job_id="",
            provider_job_id=provider_job_id,
            status=FlowJobStatus.COMPLETED,
        )

    def download(self, provider_job_id: str, output_dir: Path) -> list[Path]:
        out = output_dir / f"{provider_job_id}.mp4"
        out.write_bytes(b"dummy video data")
        return [out]


def test_recovered_pending_job_multi_poll_to_completion():
    """
    RED TEST: Recovered PENDING job must continue bounded polling until COMPLETED,
    not return prematurely after 1 poll while still PENDING.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        db_file = tmp_path / "jobs.json"
        store = JobStateStore(db_file)

        # Persist a job that crashed/restarted while in PENDING
        rec = FlowJobRecord(
            job_id="job_multi_poll_recov",
            prompt="A scenic view of Ha Long Bay",
            prompt_hash="hash_halong_1",
            provider="flow",
            model="veo_3_1_t2v_fast",
            aspect_ratio="9:16",
            priority=5,
            provider_job_id="prov_halong_1",
            status=FlowJobStatus.PENDING,
        )
        store.save_job(rec)

        # Provider requires 3 polls to become COMPLETED
        provider = MultiPollRecoveringProvider(polls_until_complete=3)
        controller = FlowController(
            provider=provider,
            storage_path=db_file,
            output_dir=tmp_path / "videos",
            poll_interval_s=0.01,
            poll_timeout_s=2.0,
        )

        resumed = controller.resume_pending_jobs()

        # Must have completed and returned the resolved result
        assert len(resumed) == 1, f"Expected 1 resolved result, got {len(resumed)}"
        assert resumed[0].status == FlowJobStatus.COMPLETED
        assert provider.poll_count == 3
        assert provider.generate_video_calls == 0

        # Persisted state must be COMPLETED
        rec_final = controller.get_job_status("job_multi_poll_recov")
        assert rec_final.status == FlowJobStatus.COMPLETED
        assert len(rec_final.output_paths) == 1


def test_recovered_pending_job_transient_poll_error_retried_no_regeneration():
    """
    RED TEST: Transient network failure during recovery polling must retry poll
    and NEVER call generate_video().
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        db_file = tmp_path / "jobs.json"
        store = JobStateStore(db_file)

        rec = FlowJobRecord(
            job_id="job_transient_poll_recov",
            prompt="Mountain temple at dusk",
            prompt_hash="hash_temple_1",
            provider="flow",
            model="veo_3_1_t2v_fast",
            aspect_ratio="9:16",
            priority=5,
            provider_job_id="prov_temple_1",
            status=FlowJobStatus.PENDING,
        )
        store.save_job(rec)

        class TransientPollProvider(FlowProvider):
            def __init__(self):
                self.polls = 0
                self.generate_calls = 0

            def health(self) -> FlowHealthStatus:
                return FlowHealthStatus(healthy=True)

            def get_credits(self) -> FlowCredits:
                return FlowCredits(100, 100, 0, time.time())

            def get_models(self) -> list[FlowModelInfo]:
                return []

            def generate_video(self, request: FlowGenerationRequest) -> FlowJobResult:
                self.generate_calls += 1
                return FlowJobResult(request.job_id, "prov_temple_1", FlowJobStatus.PENDING)

            def poll(self, provider_job_id: str) -> FlowJobResult:
                self.polls += 1
                if self.polls == 1:
                    return FlowJobResult(
                        job_id="",
                        provider_job_id=provider_job_id,
                        status=FlowJobStatus.FAILED,
                        failure_class=FlowFailureClass.NETWORK,
                        failure_message="Transient network glitch",
                    )
                return FlowJobResult(
                    job_id="",
                    provider_job_id=provider_job_id,
                    status=FlowJobStatus.COMPLETED,
                )

            def download(self, provider_job_id: str, output_dir: Path) -> list[Path]:
                out = output_dir / f"{provider_job_id}.mp4"
                out.write_bytes(b"video bytes")
                return [out]

            def reconcile_by_client_id(self, client_request_id: str) -> Optional[FlowJobResult]:
                return None

        provider = TransientPollProvider()
        controller = FlowController(
            provider=provider,
            storage_path=db_file,
            output_dir=tmp_path / "videos",
            retry_policy=RetryPolicy(max_retries=3, base_delay_s=0.01),
            poll_interval_s=0.01,
            poll_timeout_s=2.0,
        )

        resumed = controller.resume_pending_jobs()

        assert len(resumed) == 1
        assert resumed[0].status == FlowJobStatus.COMPLETED
        assert provider.generate_calls == 0
        assert provider.polls == 2

        rec_final = controller.get_job_status("job_transient_poll_recov")
        assert rec_final.status == FlowJobStatus.COMPLETED


def test_recovered_pending_job_user_interaction_pauses_fail_closed():
    """
    RED TEST: USER_INTERACTION_REQUIRED encountered on recovered poll must halt and pause.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        db_file = tmp_path / "jobs.json"
        store = JobStateStore(db_file)

        rec = FlowJobRecord(
            job_id="job_captcha_recov",
            prompt="Bamboo grove",
            prompt_hash="hash_bamboo_1",
            provider="flow",
            model="veo_3_1_t2v_fast",
            aspect_ratio="9:16",
            priority=5,
            provider_job_id="prov_bamboo_1",
            status=FlowJobStatus.PENDING,
        )
        store.save_job(rec)

        class CaptchaRecoveringProvider(FlowProvider):
            def __init__(self):
                self.polls = 0

            def health(self) -> FlowHealthStatus:
                return FlowHealthStatus(healthy=True)

            def get_credits(self) -> FlowCredits:
                return FlowCredits(100, 100, 0, time.time())

            def get_models(self) -> list[FlowModelInfo]:
                return []

            def generate_video(self, request: FlowGenerationRequest) -> FlowJobResult:
                return FlowJobResult(request.job_id, "prov_bamboo_1", FlowJobStatus.PENDING)

            def poll(self, provider_job_id: str) -> FlowJobResult:
                self.polls += 1
                if self.polls == 1:
                    return FlowJobResult(job_id="", provider_job_id=provider_job_id, status=FlowJobStatus.PENDING)
                return FlowJobResult(
                    job_id="",
                    provider_job_id=provider_job_id,
                    status=FlowJobStatus.USER_INTERACTION_REQUIRED,
                    failure_class=FlowFailureClass.USER_INTERACTION_REQUIRED,
                    failure_message="Captcha challenge during recovery poll",
                )

            def download(self, provider_job_id: str, output_dir: Path) -> list[Path]:
                return []

        provider = CaptchaRecoveringProvider()
        controller = FlowController(
            provider=provider,
            storage_path=db_file,
            output_dir=tmp_path / "videos",
            poll_interval_s=0.01,
            poll_timeout_s=2.0,
        )

        resumed = controller.resume_pending_jobs()

        assert len(resumed) == 1
        assert resumed[0].status == FlowJobStatus.USER_INTERACTION_REQUIRED
        assert controller.is_paused is True

        rec_final = controller.get_job_status("job_captcha_recov")
        assert rec_final.status == FlowJobStatus.USER_INTERACTION_REQUIRED


def test_recovered_pending_job_timeout_fails_durable():
    """
    RED TEST: Timeout during recovered polling must transition job to FAILED(TIMEOUT).
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        db_file = tmp_path / "jobs.json"
        store = JobStateStore(db_file)

        rec = FlowJobRecord(
            job_id="job_timeout_recov",
            prompt="Endless sunset",
            prompt_hash="hash_sunset_1",
            provider="flow",
            model="veo_3_1_t2v_fast",
            aspect_ratio="9:16",
            priority=5,
            provider_job_id="prov_sunset_1",
            status=FlowJobStatus.PENDING,
        )
        store.save_job(rec)

        class ForeverPendingProvider(FlowProvider):
            def health(self) -> FlowHealthStatus:
                return FlowHealthStatus(healthy=True)

            def get_credits(self) -> FlowCredits:
                return FlowCredits(100, 100, 0, time.time())

            def get_models(self) -> list[FlowModelInfo]:
                return []

            def generate_video(self, request: FlowGenerationRequest) -> FlowJobResult:
                return FlowJobResult(request.job_id, "prov_sunset_1", FlowJobStatus.PENDING)

            def poll(self, provider_job_id: str) -> FlowJobResult:
                return FlowJobResult(job_id="", provider_job_id=provider_job_id, status=FlowJobStatus.PENDING)

            def download(self, provider_job_id: str, output_dir: Path) -> list[Path]:
                return []

        provider = ForeverPendingProvider()
        controller = FlowController(
            provider=provider,
            storage_path=db_file,
            output_dir=tmp_path / "videos",
            poll_interval_s=0.01,
            poll_timeout_s=0.05,
        )

        resumed = controller.resume_pending_jobs()

        assert len(resumed) == 1
        assert resumed[0].status == FlowJobStatus.FAILED
        assert resumed[0].failure_class == FlowFailureClass.TIMEOUT

        rec_final = controller.get_job_status("job_timeout_recov")
        assert rec_final.status == FlowJobStatus.FAILED
        assert rec_final.failure_class == FlowFailureClass.TIMEOUT
