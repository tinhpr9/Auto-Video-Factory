"""
Persistent storage for Flow job records, crash recovery, and duplicate prevention.
Includes multi-process file locking, dynamic disk merge, and corruption protection.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
import threading
import time
from pathlib import Path
from typing import Optional

try:
    import fcntl
except ImportError:
    fcntl = None  # Non-POSIX fallback

from .models import FlowJobRecord, FlowJobStatus

log = logging.getLogger(__name__)


class JobStateStore:
    """
    Thread-safe, process-safe, and atomic persistent JSON storage for FlowJobRecords.
    """

    def __init__(self, storage_path: Path):
        self.storage_path = Path(storage_path)
        self.lock_path = self.storage_path.with_suffix(".lock")
        self._lock = threading.Lock()
        self._jobs: dict[str, FlowJobRecord] = {}
        self._init_and_load()

    def _acquire_file_lock(self):
        """Cross-process advisory file lock handler."""
        if fcntl:
            self.lock_path.parent.mkdir(parents=True, exist_ok=True)
            self._lock_file = open(self.lock_path, "a")
            fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_EX)

    def _release_file_lock(self):
        if fcntl and hasattr(self, "_lock_file") and self._lock_file:
            try:
                fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_UN)
                self._lock_file.close()
            except Exception:
                pass
            self._lock_file = None

    def _reload_from_disk_unlocked(self):
        """Reload and merge latest on-disk state while holding lock."""
        if self.storage_path.exists():
            try:
                raw = self.storage_path.read_text(encoding="utf-8")
                if raw.strip():
                    data = json.loads(raw)
                    disk_jobs: dict[str, FlowJobRecord] = {}
                    for item in data.get("jobs", []):
                        record = FlowJobRecord.from_dict(item)
                        disk_jobs[record.job_id] = record
                    self._jobs = disk_jobs
            except Exception as e:
                log.error("Corrupted jobs store detected in %s: %s. Preserving backup.", self.storage_path, e)
                bak_path = self.storage_path.with_suffix(".json.corrupt.bak")
                try:
                    shutil.copy2(self.storage_path, bak_path)
                except Exception:
                    pass
                self._jobs = {}
                self._save_unlocked()
        else:
            self._jobs = {}

    def _init_and_load(self):
        with self._lock:
            self._acquire_file_lock()
            try:
                if self.storage_path.exists():
                    self._reload_from_disk_unlocked()
                else:
                    self.storage_path.parent.mkdir(parents=True, exist_ok=True)
                    self._save_unlocked()
            finally:
                self._release_file_lock()

    def _save_unlocked(self):
        """Atomic write via temporary file replacement under lock."""
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "version": 1,
            "jobs": [job.to_dict() for job in self._jobs.values()],
        }
        json_str = json.dumps(data, indent=2, ensure_ascii=False)

        temp_dir = self.storage_path.parent
        with tempfile.NamedTemporaryFile("w", dir=temp_dir, delete=False, encoding="utf-8") as tf:
            tf.write(json_str)
            temp_name = tf.name

        os.replace(temp_name, self.storage_path)

    def save_job(self, record: FlowJobRecord):
        with self._lock:
            self._acquire_file_lock()
            try:
                self._reload_from_disk_unlocked()
                self._jobs[record.job_id] = record
                self._save_unlocked()
            finally:
                self._release_file_lock()

    def get_job(self, job_id: str) -> Optional[FlowJobRecord]:
        with self._lock:
            self._acquire_file_lock()
            try:
                self._reload_from_disk_unlocked()
                return self._jobs.get(job_id)
            finally:
                self._release_file_lock()

    def get_active_job_by_prompt_hash(self, prompt_hash: str) -> Optional[FlowJobRecord]:
        """Find active (QUEUED, RETRY_WAIT, SUBMITTED, PENDING, GENERATING) job with the same prompt hash."""
        active_statuses = {
            FlowJobStatus.QUEUED,
            FlowJobStatus.RETRY_WAIT,
            FlowJobStatus.SUBMITTED,
            FlowJobStatus.PENDING,
            FlowJobStatus.GENERATING,
        }
        with self._lock:
            self._acquire_file_lock()
            try:
                self._reload_from_disk_unlocked()
                for job in self._jobs.values():
                    if job.prompt_hash == prompt_hash and job.status in active_statuses:
                        return job
                return None
            finally:
                self._release_file_lock()

    def has_unresolved_interaction_required(self) -> bool:
        """Check if any job on disk currently requires user interaction (e.g. CAPTCHA pause)."""
        with self._lock:
            self._acquire_file_lock()
            try:
                self._reload_from_disk_unlocked()
                for job in self._jobs.values():
                    if job.status == FlowJobStatus.USER_INTERACTION_REQUIRED:
                        return True
                return False
            finally:
                self._release_file_lock()

    def list_active_jobs(self) -> list[FlowJobRecord]:
        active_statuses = {
            FlowJobStatus.QUEUED,
            FlowJobStatus.RETRY_WAIT,
            FlowJobStatus.SUBMITTED,
            FlowJobStatus.PENDING,
            FlowJobStatus.GENERATING,
        }
        with self._lock:
            self._acquire_file_lock()
            try:
                self._reload_from_disk_unlocked()
                return [job for job in self._jobs.values() if job.status in active_statuses]
            finally:
                self._release_file_lock()

    def list_all_jobs(self) -> list[FlowJobRecord]:
        with self._lock:
            self._acquire_file_lock()
            try:
                self._reload_from_disk_unlocked()
                return list(self._jobs.values())
            finally:
                self._release_file_lock()
