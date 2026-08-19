"""
Tests for Job Persistence, Crash/Restart Reload, and Duplicate Prevention.
"""
import tempfile
from pathlib import Path
from auto_video_factory.flow_provider.models import (
    FlowJobRecord,
    FlowJobStatus,
    FlowFailureClass,
    FlowGenerationRequest,
    FlowModel,
    FlowAspectRatio,
)
from auto_video_factory.flow_provider.persistence import JobStateStore


def test_job_persistence_crud_and_reload():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "jobs.json"
        
        # Instance 1: write a job record
        store1 = JobStateStore(db_path)
        req = FlowGenerationRequest(
            job_id="job_persist_1",
            prompt="Ancient dragon over foggy mountains",
            model=FlowModel.VEO_3_1_FAST,
            aspect_ratio=FlowAspectRatio.PORTRAIT_9_16,
            count=1,
            output_dir=Path("./out"),
        )
        record = FlowJobRecord.from_request(req, provider="mock")
        record.provider_job_id = "prov_123"
        record.status = FlowJobStatus.PENDING
        store1.save_job(record)
        
        assert db_path.exists()
        
        # Instance 2 (Simulating Process Restart)
        store2 = JobStateStore(db_path)
        loaded = store2.get_job("job_persist_1")
        assert loaded is not None
        assert loaded.job_id == "job_persist_1"
        assert loaded.provider_job_id == "prov_123"
        assert loaded.prompt_hash == req.prompt_hash
        assert loaded.status == FlowJobStatus.PENDING
        
        # Update status
        loaded.status = FlowJobStatus.COMPLETED
        loaded.output_paths = [Path("./out/dragon.mp4")]
        store2.save_job(loaded)
        
        # Instance 3 (Another restart)
        store3 = JobStateStore(db_path)
        reloaded = store3.get_job("job_persist_1")
        assert reloaded.status == FlowJobStatus.COMPLETED
        assert len(reloaded.output_paths) == 1
        assert str(reloaded.output_paths[0]).endswith("dragon.mp4")


def test_duplicate_generation_prevention():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "jobs.json"
        store = JobStateStore(db_path)
        
        req = FlowGenerationRequest(
            job_id="job_dup_1",
            prompt="Duplicate prompt test",
            model=FlowModel.VEO_3_1_FAST,
            aspect_ratio=FlowAspectRatio.PORTRAIT_9_16,
            count=1,
            output_dir=Path("./out"),
        )
        record = FlowJobRecord.from_request(req, provider="mock")
        record.status = FlowJobStatus.GENERATING
        store.save_job(record)
        
        # Check by prompt hash
        existing = store.get_active_job_by_prompt_hash(req.prompt_hash)
        assert existing is not None
        assert existing.job_id == "job_dup_1"
        
        # Query with different prompt
        other_hash = FlowGenerationRequest(
            job_id="job_dup_2",
            prompt="Completely different prompt",
            model=FlowModel.VEO_3_1_FAST,
            aspect_ratio=FlowAspectRatio.PORTRAIT_9_16,
            count=1,
            output_dir=Path("./out"),
        ).prompt_hash
        assert store.get_active_job_by_prompt_hash(other_hash) is None
