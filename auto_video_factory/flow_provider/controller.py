"""
FlowController: High-level orchestrator coordinating Queue, Persistence, RateLimiting,
RetryPolicy, and Provider execution.
"""
from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path
from typing import Optional

from .contract import FlowProvider
from .models import (
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
from .persistence import JobStateStore
from .queue import JobQueue, RateLimiter, RetryPolicy

log = logging.getLogger(__name__)


class FlowController:
    """
    Central controller for Flow operations.
    Exposes a unified interface for Antigraviny/Codex and background agents.
    """

    def __init__(
        self,
        provider: FlowProvider,
        storage_path: Path,
        output_dir: Path,
        rate_limiter: Optional[RateLimiter] = None,
        retry_policy: Optional[RetryPolicy] = None,
    ):
        self.provider = provider
        self.storage_path = Path(storage_path)
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.store = JobStateStore(self.storage_path)
        self.queue = JobQueue()
        self.rate_limiter = rate_limiter or RateLimiter(max_operations=10, window_seconds=60.0)
        self.retry_policy = retry_policy or RetryPolicy(max_retries=3, base_delay_s=1.0)
        self.is_paused: bool = False

    def health(self) -> FlowHealthStatus:
        return self.provider.health()

    def get_credits(self) -> FlowCredits:
        return self.provider.get_credits()

    def get_models(self) -> list[FlowModelInfo]:
        return self.provider.get_models()

    def get_job_status(self, job_id: str) -> Optional[FlowJobRecord]:
        return self.store.get_job(job_id)

    def submit(
        self,
        prompt: str,
        model: FlowModel = FlowModel.VEO_3_1_FAST,
        aspect_ratio: FlowAspectRatio = FlowAspectRatio.PORTRAIT_9_16,
        count: int = 1,
        priority: int = 5,
        job_id: Optional[str] = None,
    ) -> str:
        """
        Submit a new generation request. Deduplicates active requests with identical prompt hash.
        """
        assigned_id = job_id or f"flow_{uuid.uuid4().hex[:12]}"
        req = FlowGenerationRequest(
            job_id=assigned_id,
            prompt=prompt,
            model=model,
            aspect_ratio=aspect_ratio,
            count=count,
            output_dir=self.output_dir,
            priority=priority,
        )

        # Duplicate active request guard
        existing = self.store.get_active_job_by_prompt_hash(req.prompt_hash)
        if existing is not None:
            log.info("Deduplicated request %s (matches active job %s)", assigned_id, existing.job_id)
            return existing.job_id

        # Persist and enqueue
        record = FlowJobRecord.from_request(req, provider="flow")
        self.store.save_job(record)
        self.queue.push(req)
        return assigned_id

    def process_next(self) -> Optional[FlowJobResult]:
        """
        Process the next highest-priority job in the queue.
        """
        if self.is_paused:
            log.warning("FlowController is paused (e.g. pending USER_INTERACTION_REQUIRED)")
            return None

        if self.queue.is_empty():
            return None

        req = self.queue.pop()
        record = self.store.get_job(req.job_id)
        if not record:
            record = FlowJobRecord.from_request(req, provider="flow")

        # Rate limiter permit check
        if not self.rate_limiter.acquire():
            # Rate limited: requeue with backoff
            log.warning("Rate limit exceeded for job %s, requeuing", req.job_id)
            self.queue.push(req)
            return FlowJobResult(
                job_id=req.job_id,
                provider_job_id="",
                status=FlowJobStatus.QUEUED,
                failure_class=FlowFailureClass.RATE_LIMIT,
                failure_message="Rate limit exceeded. Job requeued.",
            )

        start_time = time.time()
        record.attempt_count += 1
        record.status = FlowJobStatus.GENERATING
        record.updated_at = time.time()
        self.store.save_job(record)

        # Generate via provider
        res = self.provider.generate_video(req)

        # Safety Gate: USER_INTERACTION_REQUIRED (CAPTCHA or interactive auth)
        if res.status == FlowJobStatus.USER_INTERACTION_REQUIRED or res.failure_class == FlowFailureClass.USER_INTERACTION_REQUIRED:
            log.error("USER_INTERACTION_REQUIRED triggered for job %s. Halting automation without bypass.", req.job_id)
            self.is_paused = True
            record.status = FlowJobStatus.USER_INTERACTION_REQUIRED
            record.failure_class = FlowFailureClass.USER_INTERACTION_REQUIRED
            record.failure_message = res.failure_message or "User interaction required"
            record.updated_at = time.time()
            self.store.save_job(record)
            return res

        # Handle Generation Failure
        if res.status == FlowJobStatus.FAILED:
            record.status = FlowJobStatus.FAILED
            record.failure_class = res.failure_class or FlowFailureClass.UNKNOWN
            record.failure_message = res.failure_message
            record.updated_at = time.time()
            self.store.save_job(record)

            # Check if retryable
            if self.retry_policy.should_retry(res.failure_class, record.attempt_count):
                delay = self.retry_policy.get_delay(record.attempt_count)
                log.info("Scheduling retry %d for job %s after %.2fs", record.attempt_count, req.job_id, delay)
                time.sleep(delay)
                self.queue.push(req)
            return res

        # SUBMITTED / PENDING -> Poll to completion
        provider_job_id = res.provider_job_id
        record.provider_job_id = provider_job_id
        record.status = FlowJobStatus.PENDING
        record.updated_at = time.time()
        self.store.save_job(record)

        polled_res = self.provider.poll(provider_job_id)
        if polled_res.status == FlowJobStatus.COMPLETED:
            files = self.provider.download(provider_job_id, self.output_dir)
            record.status = FlowJobStatus.COMPLETED
            record.output_paths = files
            record.updated_at = time.time()
            self.store.save_job(record)

            polled_res.output_files = files
            polled_res.elapsed_seconds = time.time() - start_time
            return polled_res
        else:
            return polled_res

    def resume_pending_jobs(self) -> list[FlowJobResult]:
        """
        On crash/restart, scans the store for active PENDING jobs and resumes polling/downloading.
        """
        results = []
        active_jobs = self.store.list_active_jobs()
        for job in active_jobs:
            if job.status == FlowJobStatus.PENDING and job.provider_job_id:
                log.info("Resuming pending job %s (provider_job_id: %s)", job.job_id, job.provider_job_id)
                polled = self.provider.poll(job.provider_job_id)
                if polled.status == FlowJobStatus.COMPLETED:
                    files = self.provider.download(job.provider_job_id, self.output_dir)
                    job.status = FlowJobStatus.COMPLETED
                    job.output_paths = files
                    job.updated_at = time.time()
                    self.store.save_job(job)
                    polled.output_files = files
                    results.append(polled)
        return results
