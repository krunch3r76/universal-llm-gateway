"""Serialize / deserialize ``AutoJob`` rows for the durable job ledger."""

from __future__ import annotations

import json
import sqlite3
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from services.git_integration_worker.cursor_auto.queue import AutoJob


def job_record(job: AutoJob) -> dict[str, Any]:
    """Flatten an in-memory AutoJob into the ledger ``record_json`` payload."""
    return {
        "job_id": job.job_id,
        "thread_id": job.thread_id,
        "turn_number": job.turn_number,
        "subject": job.subject,
        "body": job.body,
        "from_agent": job.from_agent,
        "to_agent": job.to_agent,
        "desired_model": job.desired_model,
        "desired_effort": job.desired_effort,
        "escalation": job.escalation,
        "contract": job.contract,
        "require_attended": job.require_attended,
        "request_id": job.request_id,
        "enqueued_at_mono": job.enqueued_at,
        "superseded_by": job.superseded_by,
        "supersedes": job.supersedes,
        "superseded_dispatch_id": job.superseded_dispatch_id,
        "continuity_hop": job.continuity_hop,
        "continuity_matched_token": job.continuity_matched_token,
        "wire_dropped_fields": list(job.wire_dropped_fields),
        "lane": job.lane,
        "execution_mode": job.execution_mode,
    }


def job_from_row(row: sqlite3.Row) -> AutoJob:
    """Rebuild an AutoJob from a ``cursor_auto_jobs`` SQLite row."""
    from services.git_integration_worker.cursor_auto.queue import AutoJob

    data = json.loads(row["record_json"] or "{}")
    if not isinstance(data, dict):
        data = {}
    return AutoJob(
        job_id=row["job_id"],
        thread_id=row["thread_id"],
        turn_number=int(row["turn_number"] or data.get("turn_number") or 0),
        subject=str(data.get("subject") or ""),
        body=str(data.get("body") or ""),
        from_agent=str(data.get("from_agent") or ""),
        to_agent=str(data.get("to_agent") or "cursor"),
        desired_model=str(data.get("desired_model") or "auto"),
        desired_effort=str(data.get("desired_effort") or "medium"),
        escalation=data.get("escalation") or None,
        contract=str(data.get("contract") or "answer"),
        require_attended=bool(data.get("require_attended", False)),
        request_id=row["request_id"] or data.get("request_id"),
        enqueued_at=float(data.get("enqueued_at_mono") or 0.0),
        status=row["status"],
        superseded_by=data.get("superseded_by"),
        supersedes=data.get("supersedes"),
        superseded_dispatch_id=data.get("superseded_dispatch_id"),
        continuity_hop=bool(data.get("continuity_hop", False)),
        continuity_matched_token=data.get("continuity_matched_token"),
        wire_dropped_fields=tuple(data.get("wire_dropped_fields") or ()),
        lane=data.get("lane") or None,
        execution_mode=str(data.get("execution_mode") or "serial"),
    )
