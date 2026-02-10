"""
Job storage singleton for managing background jobs.
"""

from datetime import datetime

from .job import Job, JobStatus


class JobStore:
    """
    In-memory job storage.

    Async-safe: Single-threaded async (no await in operations) means no
    concurrent modification possible.
    """

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}

    async def add(self, job: Job) -> None:
        """Add job to store."""
        self._jobs[job.job_id] = job

    async def get(self, job_id: str) -> Job | None:
        """Get job by ID."""
        return self._jobs.get(job_id)

    async def remove(self, job_id: str) -> bool:
        """Remove job from store."""
        if job_id in self._jobs:
            del self._jobs[job_id]
            return True
        return False

    async def list_all(self) -> list[Job]:
        """List all jobs."""
        return list(self._jobs.values())

    async def cleanup_completed(self, max_age_seconds: int = 3600) -> int:
        """Remove completed jobs older than max_age."""
        now = datetime.now()
        removed = 0

        to_remove = []
        for job_id, job in self._jobs.items():
            if job.completed_at and job.status in (
                JobStatus.COMPLETED,
                JobStatus.FAILED,
                JobStatus.CANCELLED,
            ):
                age = (now - job.completed_at).total_seconds()
                if age > max_age_seconds:
                    to_remove.append(job_id)

        for job_id in to_remove:
            del self._jobs[job_id]
            removed += 1

        return removed


_job_store: JobStore | None = None


def get_job_store() -> JobStore:
    """Get the global job store instance."""
    global _job_store  # noqa: PLW0603
    if _job_store is None:
        _job_store = JobStore()
    return _job_store
