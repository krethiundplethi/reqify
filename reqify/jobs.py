from __future__ import annotations

import os
import sys
import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from typing import Callable


JobFn = Callable[[], object]


@dataclass
class Job:
    id: str
    label: str
    status: str = "pending"
    result: object | None = None
    error: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


_jobs: dict[str, Job] = {}
_jobs_lock = threading.Lock()


def debug_enabled() -> bool:
    return os.environ.get("REQIFY_DEBUG", "").strip().lower() in {"1", "true", "yes", "on"}


def start_job(label: str, fn: JobFn) -> str:
    cleanup_jobs()
    job = Job(id=uuid.uuid4().hex, label=label)
    with _jobs_lock:
        _jobs[job.id] = job
    thread = threading.Thread(target=run_job, args=(job.id, fn), name=f"reqify-job-{job.id[:8]}", daemon=True)
    thread.start()
    return job.id


def run_job(job_id: str, fn: JobFn) -> None:
    set_job_state(job_id, "running")
    if debug_enabled():
        print(f"Reqify job started: {job_id}", file=sys.stderr)
    try:
        result = fn()
    except Exception as exc:
        if debug_enabled():
            traceback.print_exc(file=sys.stderr)
        set_job_state(job_id, "error", error=str(exc))
        return
    set_job_state(job_id, "done", result=result)
    if debug_enabled():
        print(f"Reqify job finished: {job_id}", file=sys.stderr)


def set_job_state(job_id: str, status: str, result: object | None = None, error: str = "") -> None:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None:
            return
        job.status = status
        job.result = result
        job.error = error
        job.updated_at = time.time()


def job_payload(job_id: str) -> dict[str, object] | None:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None:
            return None
        return {
            "id": job.id,
            "label": job.label,
            "status": job.status,
            "result": job.result if job.status == "done" else None,
            "error": job.error if job.status == "error" else "",
        }


def cleanup_jobs(max_age_seconds: int = 3600) -> None:
    cutoff = time.time() - max_age_seconds
    with _jobs_lock:
        stale_ids = [job_id for job_id, job in _jobs.items() if job.status in {"done", "error"} and job.updated_at < cutoff]
        for job_id in stale_ids:
            del _jobs[job_id]
