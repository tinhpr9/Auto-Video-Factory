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
        self._mutex = threading.Lock()

        # Fail-closed startup check: pause if any unresolved interaction required job exists
        self.is_paused: bool = bool(self.store.has_unresolved_interaction_required())
        if self.is_paused:
            log.warning("FlowController initialized in PAUSED state due to unresolved USER_INTERACTION_REQUIRED on disk.")

    def health(self) -> FlowHealthStatus:
        return self.provider.health()

    def get_credits(self) -> FlowCredits:
        return self.provider.get_credits()

    def get_models(self) -> list[FlowModelInfo]:
        return self.provider.get_models()

    def get_job_status(self, job_id: str) -> Optional[FlowJobRecord]:
        return self.store.get_job(job_id)

    def _is_safety_paused(self) -> bool:
        with self._mutex:
            if self.is_paused:
                return True
        if self.store.has_unresolved_interaction_required():
            with self._mutex:
                self.is_paused = True
            return True
        return False

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
        Submit a new generation request. Deduplicates active requests with identical prompt hash,
        and strictly rejects custom job_id collisions for differing requests.
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
            # 1. Custom job_id collision check
            existing_id_job = self.store.get_job(assigned_id)
            if existing_id_job is not None:
                if existing_id_job.prompt_hash == req.prompt_hash:
                    log.info("Idempotent resubmission of job_id %s", assigned_id)
                    return existing_id_job.job_id
                raise ValueError(
                    f"Job ID collision: '{assigned_id}' already exists with a different request specification."
                )

            # 2. Duplicate active request check
            existing_active = self.store.get_active_job_by_prompt_hash(req.prompt_hash)
            if existing_active is not None:
                log.info("Deduplicated request %s (matches active job %s)", assigned_id, existing_active.job_id)
                return existing_active.job_id

            # 3. Persist and enqueue
            record = FlowJobRecord.from_request(req, provider="flow")
            self.store.save_job(record)
            self.queue.push(req)
            return assigned_id

    def process_next(self) -> Optional[FlowJobResult]:
        """
        Process the next highest-priority job in the queue.
        """
        if self._is_safety_paused():
            log.warning("FlowController is paused (e.g. pending USER_INTERACTION_REQUIRED)")
            return None

        with self._mutex:
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

        # Safety Gate Check immediately before provider call
        if self._is_safety_paused():
            log.warning("FlowController safety pause active; requeuing job %s", req.job_id)
            with self._mutex:
                self.queue.push(req)
            return None

        start_time = time.time()
        record.attempt_count += 1
        record.status = FlowJobStatus.GENERATING
        record.updated_at = time.time()
        self.store.save_job(record)

        # Generate via provider with exception boundary
        try:
            res = self.provider.generate_video(req)
        except Exception as exc:
            log.exception("Provider raised unexpected exception during generate_video for job %s: %s", req.job_id, exc)
            failure_cls = FlowFailureClass.NETWORK if isinstance(exc, (ConnectionError, TimeoutError)) else FlowFailureClass.UNKNOWN
            res = FlowJobResult(
                job_id=req.job_id,
                provider_job_id="",
                status=FlowJobStatus.FAILED,
                failure_class=failure_cls,
                failure_message=f"Provider exception: {exc}",
            )

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
            # Check if retryable
            if self.retry_policy.should_retry(res.failure_class, record.attempt_count):
                delay = self.retry_policy.get_delay(record.attempt_count)
                log.info("Scheduling retry %d for job %s after %.2fs", record.attempt_count, req.job_id, delay)
                record.status = FlowJobStatus.RETRY_WAIT
                record.failure_class = res.failure_class
                record.failure_message = res.failure_message
                record.updated_at = time.time()
                self.store.save_job(record)

                time.sleep(delay)
                record.status = FlowJobStatus.QUEUED
                record.updated_at = time.time()
                self.store.save_job(record)
                with self._mutex:
                    self.queue.push(req)
                return FlowJobResult(
                    job_id=req.job_id,
                    provider_job_id="",
                    status=FlowJobStatus.QUEUED,
                    failure_class=res.failure_class,
                    failure_message=res.failure_message,
                )
            else:
                record.status = FlowJobStatus.FAILED
                record.failure_class = res.failure_class or FlowFailureClass.UNKNOWN
                record.failure_message = res.failure_message
                record.updated_at = time.time()
                self.store.save_job(record)
                return res

        # SUBMITTED / PENDING -> Poll loop until terminal state or timeout
        provider_job_id = res.provider_job_id
        record.provider_job_id = provider_job_id
        record.status = FlowJobStatus.PENDING
        record.updated_at = time.time()
        self.store.save_job(record)

        poll_start = time.time()
        poll_failures = 0

        while time.time() - poll_start < self.poll_timeout_s:
            if self._is_safety_paused():
                log.warning("Safety pause encountered during polling loop for job %s", req.job_id)
                return None

            try:
                polled_res = self.provider.poll(provider_job_id)
            except Exception as exc:
                log.exception("Provider exception during poll for %s: %s", provider_job_id, exc)
                polled_res = FlowJobResult(
                    job_id=req.job_id,
                    provider_job_id=provider_job_id,
                    status=FlowJobStatus.FAILED,
                    failure_class=FlowFailureClass.NETWORK if isinstance(exc, (ConnectionError, TimeoutError)) else FlowFailureClass.UNKNOWN,
                    failure_message=f"Polling exception: {exc}",
                )

            if polled_res.status == FlowJobStatus.COMPLETED:
                try:
                    files = self.provider.download(provider_job_id, self.output_dir)
                except Exception as exc:
                    log.exception("Download exception for %s: %s", provider_job_id, exc)
                    files = []

                # Validate downloaded artifacts
                valid_files = [f for f in files if f.exists() and f.is_file() and f.stat().st_size > 0]
                if not valid_files:
                    log.error("Download failed or produced zero valid artifacts for %s", provider_job_id)
                    record.status = FlowJobStatus.FAILED
                    record.failure_class = FlowFailureClass.DOWNLOAD_FAILED
                    record.failure_message = "Download produced empty or invalid video artifacts"
                    record.updated_at = time.time()
                    self.store.save_job(record)
                    return FlowJobResult(
                        job_id=req.job_id,
                        provider_job_id=provider_job_id,
                        status=FlowJobStatus.FAILED,
                        failure_class=FlowFailureClass.DOWNLOAD_FAILED,
                        failure_message="Download produced empty or invalid video artifacts",
                    )

                record.status = FlowJobStatus.COMPLETED
                record.output_paths = valid_files
                record.updated_at = time.time()
                self.store.save_job(record)

                polled_res.output_files = valid_files
                polled_res.credit_before = res.credit_before
                polled_res.credit_after = res.credit_after
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
                # If retryable polling failure (e.g. transient network), retry polling without regenerating!
                if self.retry_policy.is_retryable(polled_res.failure_class) and poll_failures < self.retry_policy.max_retries:
                    poll_failures += 1
                    delay = self.retry_policy.get_delay(poll_failures)
                    log.warning("Transient poll error (%s) for %s; retrying poll in %.2fs", polled_res.failure_class, provider_job_id, delay)
                    time.sleep(delay)
                    continue

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
        1. Restores unfulfilled QUEUED or RETRY_WAIT jobs into self.queue.
        2. Handles ambiguous GENERATING jobs without provider_job_id via reconciliation or fail-closed mark.
        3. Polls and resolves active PENDING jobs.
        """
        results = []
        active_jobs = self.store.list_active_jobs()

        for job in active_jobs:
            if job.status in (FlowJobStatus.QUEUED, FlowJobStatus.RETRY_WAIT):
                req = FlowGenerationRequest(
                    job_id=job.job_id,
                    prompt=job.prompt,
                    model=FlowModel(job.model),
                    aspect_ratio=FlowAspectRatio(job.aspect_ratio),
                    count=job.count,
                    output_dir=self.output_dir,
                    priority=job.priority,
                    start_image_path=job.start_image_path,
                    client_request_id=job.client_request_id,
                )
                log.info("Restoring unfulfilled job %s into queue", job.job_id)
                with self._mutex:
                    self.queue.push(req)

            elif job.status == FlowJobStatus.GENERATING and not job.provider_job_id:
                # Ambiguous submission crash window: attempt reconciliation
                recon = self.provider.reconcile_by_client_id(job.client_request_id or job.job_id)
                if recon and recon.provider_job_id:
                    log.info("Reconciled ambiguous job %s to provider_job_id %s", job.job_id, recon.provider_job_id)
                    job.provider_job_id = recon.provider_job_id
                    job.status = FlowJobStatus.PENDING
                    self.store.save_job(job)
                else:
                    log.error("Ambiguous crash for job %s; marking SUBMISSION_AMBIGUOUS without auto-regenerating", job.job_id)
                    job.status = FlowJobStatus.SUBMISSION_AMBIGUOUS
                    job.failure_class = FlowFailureClass.UNKNOWN
                    job.failure_message = "Process crashed during generation submission; manual reconciliation required to prevent duplicate billing."
                    job.updated_at = time.time()
                    self.store.save_job(job)

            elif job.status in (FlowJobStatus.PENDING, FlowJobStatus.GENERATING) and job.provider_job_id:
                log.info("Resuming active job %s (provider_job_id: %s)", job.job_id, job.provider_job_id)
                polled = self.provider.poll(job.provider_job_id)

                if polled.status == FlowJobStatus.COMPLETED:
                    try:
                        files = self.provider.download(job.provider_job_id, self.output_dir)
                    except Exception:
                        files = []

                    valid_files = [f for f in files if f.exists() and f.is_file() and f.stat().st_size > 0]
                    if valid_files:
                        job.status = FlowJobStatus.COMPLETED
                        job.output_paths = valid_files
                        job.updated_at = time.time()
                        self.store.save_job(job)
                        polled.output_files = valid_files
                        results.append(polled)
                    else:
                        job.status = FlowJobStatus.FAILED
                        job.failure_class = FlowFailureClass.DOWNLOAD_FAILED
                        job.failure_message = "Download produced empty artifacts"
                        job.updated_at = time.time()
                        self.store.save_job(job)
                        polled.status = FlowJobStatus.FAILED
                        polled.failure_class = FlowFailureClass.DOWNLOAD_FAILED
                        polled.failure_message = "Download produced empty artifacts"
                        results.append(polled)

                elif polled.status == FlowJobStatus.USER_INTERACTION_REQUIRED or polled.failure_class == FlowFailureClass.USER_INTERACTION_REQUIRED:
                    with self._mutex:
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
