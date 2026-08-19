"""
Persistent storage for Flow job records, crash recovery, and duplicate prevention.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Optional

from .models import FlowJobRecord, FlowJobStatus


class JobStateStore:
    """
    Thread-safe and atomic persistent JSON storage for FlowJobRecords.
    """

    def __init__(self, storage_path: Path):
        self.storage_path = Path(storage_path)
        self._lock = threading.Lock()
        self._jobs: dict[str, FlowJobRecord] = {}
        self._init_and_load()

    def _init_and_load(self):
        with self._lock:
            if self.storage_path.exists():
                try:
                    raw = self.storage_path.read_text(encoding="utf-8")
                    if raw.strip():
                        data = json.loads(raw)
                        for item in data.get("jobs", []):
                            record = FlowJobRecord.from_dict(item)
                            self._jobs[record.job_id] = record
                except Exception:
                    # In case of corrupted store, retain empty or backup
                    pass
            else:
                self.storage_path.parent.mkdir(parents=True, exist_ok=True)
                self._save_unlocked()

    def _save_unlocked(self):
        """Atomic write via temporary file replacement."""
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
            self._jobs[record.job_id] = record
            self._save_unlocked()

    def get_job(self, job_id: str) -> Optional[FlowJobRecord]:
        with self._lock:
            return self._jobs.get(job_id)

    def get_active_job_by_prompt_hash(self, prompt_hash: str) -> Optional[FlowJobRecord]:
        """Find active (QUEUED, SUBMITTED, PENDING, GENERATING) job with the same prompt hash."""
        active_statuses = {
            FlowJobStatus.QUEUED,
            FlowJobStatus.SUBMITTED,
            FlowJobStatus.PENDING,
            FlowJobStatus.GENERATING,
        }
        with self._lock:
            for job in self._jobs.values():
                if job.prompt_hash == prompt_hash and job.status in active_statuses:
                    return job
            return None

    def list_active_jobs(self) -> list[FlowJobRecord]:
        active_statuses = {
            FlowJobStatus.QUEUED,
            FlowJobStatus.SUBMITTED,
            FlowJobStatus.PENDING,
            FlowJobStatus.GENERATING,
        }
        with self._lock:
            return [job for job in self._jobs.values() if job.status in active_statuses]

    def list_all_jobs(self) -> list[FlowJobRecord]:
        with self._lock:
            return list(self._jobs.values())
