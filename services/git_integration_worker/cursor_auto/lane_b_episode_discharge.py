"""Auto-owned Lane-B branch discharge on terminals that skip SDK closeout."""

from __future__ import annotations

from pathlib import Path

from universal_logging import get_logger

from services.git_integration_worker.config import load_config
from services.git_integration_worker.cursor_auto.queue import AutoJob, get_queue
from services.git_integration_worker.cursor_sdk_branch_discharge import (
    DischargeResult,
    discharge,
)
from services.git_integration_worker.cursor_sdk_worktree_registry import (
    lookup_lane_worktree,
)

logger = get_logger(__name__)


def same_thread_successor_exists(job: AutoJob) -> bool:
    """True when another job on ``job.thread_id`` is queued or in-flight."""
    queue = get_queue()
    incumbent = queue.incumbent_for_thread(job.thread_id, exclude_job_id=job.job_id)
    if incumbent is not None:
        return True
    candidate = queue.supersede_candidate_for_thread(job.thread_id)
    return (
        candidate is not None
        and candidate.job_id != job.job_id
        and candidate.status == "queued"
    )


def _source_repo() -> Path:
    return load_config().source_repo.resolve()


def maybe_discharge_failed_episode(
    job: AutoJob,
    *,
    dispatch_id: str | None,
    summary: str,
) -> DischargeResult | None:
    """Discard a Lane-B tree when Auto fails after mint and no successor inherits."""
    del dispatch_id  # mint is keyed by thread_id via registry
    record = lookup_lane_worktree(thread_id=job.thread_id)
    if record is None:
        return None
    if same_thread_successor_exists(job):
        logger.info(
            "cursor-auto lane_b discharge skipped inherit job=%s thread=%s",
            job.job_id,
            job.thread_id,
        )
        return None
    reason = f"auto_status_failed:{summary[:240]}"
    result = discharge(
        repo=_source_repo(),
        branch_name=record.branch_name,
        verb="discard",
        reason=reason,
    )
    logger.info(
        "cursor-auto lane_b episode discard job=%s thread=%s branch=%s "
        "discharged=%s",
        job.job_id,
        job.thread_id,
        record.branch_name,
        result.discharged,
    )
    return result


__all__ = [
    "maybe_discharge_failed_episode",
    "same_thread_successor_exists",
]
