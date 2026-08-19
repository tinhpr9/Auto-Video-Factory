"""
Tests for JobQueue ordering, priority handling, and concurrency.
"""
from pathlib import Path
from auto_video_factory.flow_provider.models import (
    FlowGenerationRequest,
    FlowModel,
    FlowAspectRatio,
)
from auto_video_factory.flow_provider.queue import JobQueue


def test_job_queue_priority_ordering():
    queue = JobQueue()
    
    req_low = FlowGenerationRequest(
        job_id="job_low",
        prompt="low priority prompt",
        model=FlowModel.VEO_3_1_FAST,
        aspect_ratio=FlowAspectRatio.PORTRAIT_9_16,
        count=1,
        output_dir=Path("./output"),
        priority=1,
    )
    req_high = FlowGenerationRequest(
        job_id="job_high",
        prompt="high priority prompt",
        model=FlowModel.VEO_3_1_FAST,
        aspect_ratio=FlowAspectRatio.PORTRAIT_9_16,
        count=1,
        output_dir=Path("./output"),
        priority=10,
    )
    req_mid = FlowGenerationRequest(
        job_id="job_mid",
        prompt="medium priority prompt",
        model=FlowModel.VEO_3_1_FAST,
        aspect_ratio=FlowAspectRatio.PORTRAIT_9_16,
        count=1,
        output_dir=Path("./output"),
        priority=5,
    )
    
    # Enqueue in mixed order
    queue.push(req_low)
    queue.push(req_high)
    queue.push(req_mid)
    
    assert queue.size() == 3
    
    # Dequeue must return in priority order: high (10) -> mid (5) -> low (1)
    first = queue.pop()
    second = queue.pop()
    third = queue.pop()
    
    assert first.job_id == "job_high"
    assert second.job_id == "job_mid"
    assert third.job_id == "job_low"
    assert queue.is_empty() is True


def test_job_queue_fifo_within_same_priority():
    queue = JobQueue()
    
    req1 = FlowGenerationRequest(
        job_id="job_1",
        prompt="prompt 1",
        model=FlowModel.VEO_3_1_FAST,
        aspect_ratio=FlowAspectRatio.PORTRAIT_9_16,
        count=1,
        output_dir=Path("./output"),
        priority=5,
    )
    req2 = FlowGenerationRequest(
        job_id="job_2",
        prompt="prompt 2",
        model=FlowModel.VEO_3_1_FAST,
        aspect_ratio=FlowAspectRatio.PORTRAIT_9_16,
        count=1,
        output_dir=Path("./output"),
        priority=5,
    )
    
    queue.push(req1)
    queue.push(req2)
    
    assert queue.pop().job_id == "job_1"
    assert queue.pop().job_id == "job_2"
