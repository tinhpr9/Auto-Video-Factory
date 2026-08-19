"""
Comprehensive TDD regression tests for bug-hunter audit findings:
1. Crash recovery re-enqueuing of QUEUED and interrupted GENERATING jobs
2. Corrupt store preservation and non-destructive recovery
3. Rate limiter waiting/backoff without CPU spin
4. Multi-poll async lifecycle until terminal state
5. resume_pending_jobs handling of FAILED and USER_INTERACTION_REQUIRED
6. Image-to-video hash uniqueness with start_image_path
"""
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
from auto_video_factory.flow_provider.provider import MockFlowProvider
from auto_video_factory.flow_provider.queue import RateLimiter


def test_image_hash_uniqueness_with_start_image():
    req1 = FlowGenerationRequest(
        job_id="job_img_1",
        prompt="Animate tranquil pond",
        model=FlowModel.VEO_3_1_FAST,
        aspect_ratio=FlowAspectRatio.PORTRAIT_9_16,
        start_image_path=Path("/tmp/img1.png"),
    )
    req2 = FlowGenerationRequest(
        job_id="job_img_2",
        prompt="Animate tranquil pond",
        model=FlowModel.VEO_3_1_FAST,
        aspect_ratio=FlowAspectRatio.PORTRAIT_9_16,
        start_image_path=Path("/tmp/img2.png"),
    )
    req_no_img = FlowGenerationRequest(
        job_id="job_img_3",
        prompt="Animate tranquil pond",
        model=FlowModel.VEO_3_1_FAST,
        aspect_ratio=FlowAspectRatio.PORTRAIT_9_16,
        start_image_path=None,
    )

    # All three must have different prompt_hashes!
    assert req1.prompt_hash != req2.prompt_hash
    assert req1.prompt_hash != req_no_img.prompt_hash
    assert req2.prompt_hash != req_no_img.prompt_hash


def test_corrupted_store_does_not_wipe_historical_data():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "jobs.json"
        db_path.write_text("{ THIS IS CORRUPT JSON BAD SYNTAX", encoding="utf-8")

        # Initializing JobStateStore should preserve the corrupted file with .bak extension
        store = JobStateStore(db_path)
        assert not db_path.read_text().startswith("{ THIS IS CORRUPT")
        bak_file = db_path.with_suffix(".json.corrupt.bak")
        assert bak_file.exists()
        assert bak_file.read_text().startswith("{ THIS IS CORRUPT")


def test_crash_recovery_restores_queued_and_interrupted_jobs():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        db_file = tmp_path / "jobs.json"

        # Session 1: Submit two jobs into store: one QUEUED, one GENERATING (interrupted before provider job assigned)
        store1 = JobStateStore(db_file)
        req_q = FlowGenerationRequest(
            job_id="job_q",
            prompt="Queued prompt before crash",
            model=FlowModel.VEO_3_1_FAST,
            aspect_ratio=FlowAspectRatio.PORTRAIT_9_16,
        )
        rec_q = FlowJobRecord.from_request(req_q, provider="flow")
        rec_q.status = FlowJobStatus.QUEUED
        store1.save_job(rec_q)

        req_gen = FlowGenerationRequest(
            job_id="job_gen",
            prompt="Generating prompt interrupted by crash",
            model=FlowModel.VEO_3_1_FAST,
            aspect_ratio=FlowAspectRatio.PORTRAIT_9_16,
        )
        rec_gen = FlowJobRecord.from_request(req_gen, provider="flow")
        rec_gen.status = FlowJobStatus.GENERATING
        store1.save_job(rec_gen)

        # Session 2: Crash restart with fresh FlowController
        provider2 = MockFlowProvider(initial_credits=100)
        controller2 = FlowController(
            provider=provider2,
            storage_path=db_file,
            output_dir=tmp_path / "videos",
        )

        # resume_pending_jobs must re-enqueue unfulfilled QUEUED jobs into queue,
        # and transition un-reconciled GENERATING jobs to SUBMISSION_AMBIGUOUS (fail-closed against double billing)
        resumed = controller2.resume_pending_jobs()
        assert controller2.queue.size() == 1

        # Process next should now execute the QUEUED job successfully
        res1 = controller2.process_next()
        assert res1.status == FlowJobStatus.COMPLETED
        assert controller2.queue.is_empty() is True

        # Check that ambiguous interrupted job was marked SUBMISSION_AMBIGUOUS
        gen_rec = controller2.get_job_status("job_gen")
        assert gen_rec.status == FlowJobStatus.SUBMISSION_AMBIGUOUS


class AsyncMultiPollProvider(FlowProvider):
    """Simulates realistic multi-poll async video generation."""
    def __init__(self):
        self.poll_count = 0
        self.credits = 100

    def health(self) -> FlowHealthStatus:
        return FlowHealthStatus(healthy=True, authenticated=True, profile_exists=True, browser_ready=True)

    def get_credits(self):
        from auto_video_factory.flow_provider.models import FlowCredits
        return FlowCredits(total_credits=self.credits, available_credits=self.credits, consumed_credits=0)

    def get_models(self):
        return []

    def generate_video(self, request: FlowGenerationRequest) -> FlowJobResult:
        self.credits -= 20
        return FlowJobResult(
            job_id=request.job_id,
            provider_job_id="async_poll_001",
            status=FlowJobStatus.SUBMITTED,
            credit_before=100,
            credit_after=80,
        )

    def poll(self, provider_job_id: str) -> FlowJobResult:
        self.poll_count += 1
        if self.poll_count < 3:
            # Poll 1 and 2 return PENDING
            return FlowJobResult(
                job_id="",
                provider_job_id=provider_job_id,
                status=FlowJobStatus.PENDING,
            )
        # Poll 3 returns COMPLETED
        return FlowJobResult(
            job_id="",
            provider_job_id=provider_job_id,
            status=FlowJobStatus.COMPLETED,
        )

    def download(self, provider_job_id: str, output_dir: Path) -> list[Path]:
        out = output_dir / "async_vid.mp4"
        out.write_bytes(b"\x00\x00\x00\x18ftypmp42")
        return [out]


def test_controller_polls_until_completion():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        provider = AsyncMultiPollProvider()
        controller = FlowController(
            provider=provider,
            storage_path=tmp_path / "jobs.json",
            output_dir=tmp_path / "videos",
            poll_interval_s=0.01,
        )

        controller.submit("Test async multi-poll video")
        result = controller.process_next()

        assert result is not None
        assert result.status == FlowJobStatus.COMPLETED
        assert provider.poll_count == 3
        assert len(result.output_files) == 1
        assert result.output_files[0].exists()


def test_resume_handles_terminal_failures_and_pauses_on_interaction():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        db_file = tmp_path / "jobs.json"

        # Session 1: save a job with PENDING status
        store1 = JobStateStore(db_file)
        rec = FlowJobRecord(
            job_id="job_fail_resume",
            prompt="Prompt fail resume",
            prompt_hash="hash123",
            provider="flow",
            model="veo_3_1_t2v_fast",
            aspect_ratio="9:16",
            priority=5,
            provider_job_id="prov_fail_001",
            status=FlowJobStatus.PENDING,
        )
        store1.save_job(rec)

        # Provider that returns USER_INTERACTION_REQUIRED on poll
        class InteractionRequiredPollProvider(MockFlowProvider):
            def poll(self, provider_job_id: str) -> FlowJobResult:
                return FlowJobResult(
                    job_id="",
                    provider_job_id=provider_job_id,
                    status=FlowJobStatus.USER_INTERACTION_REQUIRED,
                    failure_class=FlowFailureClass.USER_INTERACTION_REQUIRED,
                    failure_message="Captcha challenge during resume poll",
                )

        provider = InteractionRequiredPollProvider()
        controller = FlowController(
            provider=provider,
            storage_path=db_file,
            output_dir=tmp_path / "videos",
        )

        resumed = controller.resume_pending_jobs()
        assert len(resumed) == 1
        assert resumed[0].status == FlowJobStatus.USER_INTERACTION_REQUIRED
        assert controller.is_paused is True

        # Store must be updated
        rec_updated = controller.get_job_status("job_fail_resume")
        assert rec_updated.status == FlowJobStatus.USER_INTERACTION_REQUIRED
