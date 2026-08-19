"""
FlowController: High-level orchestrator coordinating Queue, Persistence, RateLimiting,
RetryPolicy, and Provider execution.
"""
from __future__ import annotations

import logging
import threading
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
        poll_interval_s: float = 2.0,
        poll_timeout_s: float = 300.0,
    ):
        self.provider = provider
        self.storage_path = Path(storage_path)
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.store = JobStateStore(self.storage_path)
        self.queue = JobQueue()
        self.rate_limiter = rate_limiter or RateLimiter(max_operations=10, window_seconds=60.0)
        self.retry_policy = retry_policy or RetryPolicy(max_retries=3, base_delay_s=1.0)
        self.poll_interval_s = poll_interval_s
        self.poll_timeout_s = poll_timeout_s
        self.is_paused: bool = False
        self._mutex = threading.Lock()

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
        start_image_path: Optional[Path] = None,
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
            start_image_path=start_image_path,
        )

        with self._mutex:
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
        with self._mutex:
            if self.is_paused:
                log.warning("FlowController is paused (e.g. pending USER_INTERACTION_REQUIRED)")
                return None

            if self.queue.is_empty():
                return None

            req = self.queue.pop()

        record = self.store.get_job(req.job_id)
        if not record:
            record = FlowJobRecord.from_request(req, provider="flow")

        # Rate limiter check with non-spinning wait
        if not self.rate_limiter.wait_and_acquire(timeout_s=5.0):
            log.warning("Rate limit capacity not available for job %s, requeuing with delay", req.job_id)
            time.sleep(1.0)
            with self._mutex:
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

        # Safety Gate: USER_INTERACTION_REQUIRED (CAPTCHA or interactive challenge)
        if res.status == FlowJobStatus.USER_INTERACTION_REQUIRED or res.failure_class == FlowFailureClass.USER_INTERACTION_REQUIRED:
            log.error("USER_INTERACTION_REQUIRED triggered for job %s. Halting automation without bypass.", req.job_id)
            with self._mutex:
                self.is_paused = True
            record.status = FlowJobStatus.USER_INTERACTION_REQUIRED
            record.failure_class = FlowFailureClass.USER_INTERACTION_REQUIRED
            record.failure_message = res.failure_message or "User interaction required"
            record.updated_at = time.time()
            self.store.save_job(record)
            return res

        # Handle Immediate Generation Failure
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
                with self._mutex:
                    self.queue.push(req)
            return res

        # SUBMITTED / PENDING -> Poll loop until terminal state or timeout
        provider_job_id = res.provider_job_id
        record.provider_job_id = provider_job_id
        record.status = FlowJobStatus.PENDING
        record.updated_at = time.time()
        self.store.save_job(record)

        poll_start = time.time()
        while time.time() - poll_start < self.poll_timeout_s:
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

            elif polled_res.status == FlowJobStatus.USER_INTERACTION_REQUIRED or polled_res.failure_class == FlowFailureClass.USER_INTERACTION_REQUIRED:
                log.error("USER_INTERACTION_REQUIRED during poll for job %s. Halting.", req.job_id)
                with self._mutex:
                    self.is_paused = True
                record.status = FlowJobStatus.USER_INTERACTION_REQUIRED
                record.failure_class = FlowFailureClass.USER_INTERACTION_REQUIRED
                record.failure_message = polled_res.failure_message
                record.updated_at = time.time()
                self.store.save_job(record)
                return polled_res

            elif polled_res.status == FlowJobStatus.FAILED:
                record.status = FlowJobStatus.FAILED
                record.failure_class = polled_res.failure_class or FlowFailureClass.UNKNOWN
                record.failure_message = polled_res.failure_message
                record.updated_at = time.time()
                self.store.save_job(record)
                return polled_res

            # Still PENDING/GENERATING -> wait for next poll
            time.sleep(self.poll_interval_s)

        # Polling timeout exceeded
        log.error("Generation poll timed out after %.1fs for job %s", self.poll_timeout_s, req.job_id)
        record.status = FlowJobStatus.FAILED
        record.failure_class = FlowFailureClass.TIMEOUT
        record.failure_message = f"Generation poll timed out after {self.poll_timeout_s}s"
        record.updated_at = time.time()
        self.store.save_job(record)

        return FlowJobResult(
            job_id=req.job_id,
            provider_job_id=provider_job_id,
            status=FlowJobStatus.FAILED,
            failure_class=FlowFailureClass.TIMEOUT,
            failure_message=f"Generation poll timed out after {self.poll_timeout_s}s",
        )

    def resume_pending_jobs(self) -> list[FlowJobResult]:
        """
        On crash/restart:
        1. Scans store for unfulfilled QUEUED / interrupted GENERATING jobs and restores them into self.queue.
        2. Scans store for active PENDING jobs with provider_job_id and resolves them.
        """
        results = []
        with self._mutex:
            active_jobs = self.store.list_active_jobs()
            for job in active_jobs:
                if job.status == FlowJobStatus.QUEUED or (job.status == FlowJobStatus.GENERATING and not job.provider_job_id):
                    # Restore unfulfilled job into queue
                    req = FlowGenerationRequest(
                        job_id=job.job_id,
                        prompt=job.prompt,
                        model=FlowModel(job.model),
                        aspect_ratio=FlowAspectRatio(job.aspect_ratio),
                        output_dir=self.output_dir,
                        priority=job.priority,
                    )
                    log.info("Restoring unfulfilled job %s into queue", job.job_id)
                    self.queue.push(req)

                elif job.status in (FlowJobStatus.PENDING, FlowJobStatus.GENERATING) and job.provider_job_id:
                    log.info("Resuming active job %s (provider_job_id: %s)", job.job_id, job.provider_job_id)
                    polled = self.provider.poll(job.provider_job_id)

                    if polled.status == FlowJobStatus.COMPLETED:
                        files = self.provider.download(job.provider_job_id, self.output_dir)
                        job.status = FlowJobStatus.COMPLETED
                        job.output_paths = files
                        job.updated_at = time.time()
                        self.store.save_job(job)
                        polled.output_files = files
                        results.append(polled)

                    elif polled.status == FlowJobStatus.USER_INTERACTION_REQUIRED or polled.failure_class == FlowFailureClass.USER_INTERACTION_REQUIRED:
                        self.is_paused = True
                        job.status = FlowJobStatus.USER_INTERACTION_REQUIRED
                        job.failure_class = FlowFailureClass.USER_INTERACTION_REQUIRED
                        job.failure_message = polled.failure_message
                        job.updated_at = time.time()
                        self.store.save_job(job)
                        results.append(polled)

                    elif polled.status == FlowJobStatus.FAILED:
                        job.status = FlowJobStatus.FAILED
                        job.failure_class = polled.failure_class or FlowFailureClass.UNKNOWN
                        job.failure_message = polled.failure_message
                        job.updated_at = time.time()
                        self.store.save_job(job)
                        results.append(polled)

        return results
