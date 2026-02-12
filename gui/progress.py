"""
progress.py -- Thread-safe in-memory job store for background pipeline runs.
"""

import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class JobState:
    job_id: str
    status: JobStatus = JobStatus.PENDING
    phase: str = ""
    current: int = 0
    total: int = 0
    error: str = ""
    result_html: str = ""
    result: dict = field(default_factory=dict)
    total_emails: int = 0
    started_at: float = 0.0


class JobStore:
    """Thread-safe, in-memory. One job at a time is sufficient for single-user."""

    def __init__(self):
        self._lock = threading.Lock()
        self._jobs: dict[str, JobState] = {}

    def create_job(self) -> str:
        job_id = uuid.uuid4().hex[:12]
        with self._lock:
            self._jobs[job_id] = JobState(job_id=job_id, started_at=time.time())
        return job_id

    def update_progress(self, job_id: str, phase: str, current: int, total: int):
        with self._lock:
            job = self._jobs.get(job_id)
            if job:
                job.status = JobStatus.RUNNING
                job.phase = phase
                job.current = current
                job.total = total

    def complete_job(self, job_id: str, result_html: str, result: dict | None = None):
        with self._lock:
            job = self._jobs.get(job_id)
            if job:
                job.status = JobStatus.COMPLETED
                job.result_html = result_html
                job.result = result or {}

    def fail_job(self, job_id: str, error: str):
        with self._lock:
            job = self._jobs.get(job_id)
            if job:
                job.status = JobStatus.FAILED
                job.error = error

    def get_job(self, job_id: str) -> JobState | None:
        with self._lock:
            return self._jobs.get(job_id)
