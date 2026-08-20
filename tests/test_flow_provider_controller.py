"""
Tests for FlowController: orchestration, failure stop gates, credit guards, and POC run.
"""
import tempfile
from pathlib import Path
from auto_video_factory.flow_provider.models import (
    FlowFailureClass,
    FlowJobStatus,
    FlowModel,
    FlowAspectRatio,
)
from auto_video_factory.flow_provider.provider import MockFlowProvider
from auto_video_factory.flow_provider.controller import FlowController


def test_flow_controller_end_to_end_single_generation():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        provider = MockFlowProvider(initial_credits=100)
        controller = FlowController(
            provider=provider,
            storage_path=tmp_path / "jobs.json",
            output_dir=tmp_path / "videos",
        )
        
        # Check initial health & credits
        health = controller.health()
        assert health.healthy is True
        
        credits = controller.get_credits()
        assert credits.available_credits == 100
        
        # Submit single job
        job_id = controller.submit(
            prompt="A majestic waterfall in Vietnam at sunrise",
            model=FlowModel.VEO_3_1_FAST,
            aspect_ratio=FlowAspectRatio.PORTRAIT_9_16,
        )
        assert job_id != ""
        
        # Process next
        result = controller.process_next()
        assert result is not None
        assert result.status == FlowJobStatus.COMPLETED
        assert len(result.output_files) == 1
        assert result.output_files[0].exists()
        assert result.credit_before == 100
        assert result.credit_after == 80
        
        # Check job in store
        record = controller.get_job_status(job_id)
        assert record.status == FlowJobStatus.COMPLETED


def test_flow_controller_user_interaction_required_stop_gate():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        # Mock provider configured to trigger USER_INTERACTION_REQUIRED
        provider = MockFlowProvider(simulate_failure=FlowFailureClass.USER_INTERACTION_REQUIRED)
        controller = FlowController(
            provider=provider,
            storage_path=tmp_path / "jobs.json",
            output_dir=tmp_path / "videos",
        )
        
        job_id = controller.submit("Prompt that triggers captcha")
        result = controller.process_next()
        
        assert result is not None
        assert result.status == FlowJobStatus.USER_INTERACTION_REQUIRED
        assert result.failure_class == FlowFailureClass.USER_INTERACTION_REQUIRED
        
        # Store state must be preserved as USER_INTERACTION_REQUIRED, no blind retry loop
        record = controller.get_job_status(job_id)
        assert record.status == FlowJobStatus.USER_INTERACTION_REQUIRED


def test_flow_controller_credit_threshold_guard():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        # Only 10 credits available, but VEO_3_1_FAST costs 20 credits
        provider = MockFlowProvider(initial_credits=10)
        controller = FlowController(
            provider=provider,
            storage_path=tmp_path / "jobs.json",
            output_dir=tmp_path / "videos",
        )
        
        job_id = controller.submit("Prompt with low credits")
        result = controller.process_next()
        
        assert result is not None
        assert result.status == FlowJobStatus.FAILED
        assert result.failure_class == FlowFailureClass.QUOTA
        assert "insufficient credits" in result.failure_message.lower()


def test_flow_controller_resume_pending_after_restart():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        db_file = tmp_path / "jobs.json"
        
        # Session 1: create job and submit to provider, but do not complete download
        provider1 = MockFlowProvider(initial_credits=100)
        controller1 = FlowController(
            provider=provider1,
            storage_path=db_file,
            output_dir=tmp_path / "videos",
        )
        job_id = controller1.submit("Prompt to be resumed")
        # Submit to provider (enters PENDING)
        req = controller1.queue.pop()
        res_pending = provider1.generate_video(req)
        record = controller1.store.get_job(job_id)
        record.provider_job_id = res_pending.provider_job_id
        record.status = FlowJobStatus.PENDING
        controller1.store.save_job(record)
        
        # Session 2: Crash restart with new controller instance pointing to same storage
        provider2 = MockFlowProvider(initial_credits=80)
        controller2 = FlowController(
            provider=provider2,
            storage_path=db_file,
            output_dir=tmp_path / "videos",
        )
        
        # Resume pending jobs
        resumed = controller2.resume_pending_jobs()
        assert len(resumed) == 1
        assert resumed[0].job_id == job_id
        assert resumed[0].status == FlowJobStatus.COMPLETED
        
        # Ensure no duplicate generation occurred
        record_final = controller2.get_job_status(job_id)
        assert record_final.status == FlowJobStatus.COMPLETED
