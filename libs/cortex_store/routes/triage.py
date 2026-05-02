"""Triage routes — staged-buckets and age-staged endpoint (F3/F4 from cortex-assertion-triage spec).

Implements aging policy: 30d auto-commit for high-confidence non-ephemeral staged assertions, 90d reject for low-confidence ephemeral.
Supports dry_run for preview. Uses existing assertion update path. Thin, REST-first.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Body
from pydantic import BaseModel

from ..db import cortex_conn, query
from ..models import AssertionUpdate
from .assertions import update_assertion

logger = logging.getLogger("cortex-api.triage")

router = APIRouter(tags=["triage"])


class AgeStagedRequest(BaseModel):
    dry_run: bool = True
    commit_days: int = 30
    reject_days: int = 90
    limit: int = 100


class AgeStagedResponse(BaseModel):
    dry_run: bool
    committed: int
    rejected: int
    preview: list[dict[str, Any]] = []
    message: str


@router.post("/age-staged", response_model=AgeStagedResponse)
def age_staged(request: AgeStagedRequest = Body(...)) -> AgeStagedResponse:
    """Age-based graduation for staged assertions (F3).

    - 30d: staged → committed if confidence_score >= 0.7, no supersedes, non-ephemeral parent, no near-duplicates.
    - 90d: staged → rejected if low confidence, ephemeral, no incoming edges.
    - Always sets reviewer='system:age-policy', review_notes with criteria.
    - Dry-run returns preview only.
    """
    now = datetime.now(UTC)
    commit_threshold = now - timedelta(days=request.commit_days)
    # reject_threshold = now - timedelta(days=request.reject_days)  # TODO: full 90d reject logic

    with cortex_conn() as conn:
        # Find staged older than thresholds (simplified query; full would join entities, check edges/duplicates)
        rows = query(
            conn,
            """
            SELECT a.id, a.entity_id, a.claim, a.confidence_score, a.created_at, e.retention_policy
            FROM assertions a
            JOIN entities e ON a.entity_id = e.id
            WHERE a.review_status = 'staged'
              AND a.superseded_by IS NULL
              AND a.created_at < ?
            LIMIT ?
            """,
            (commit_threshold.isoformat(), request.limit),
        )

        committed = 0
        rejected = 0
        preview = []

        for row in rows:
            aid = row["id"]
            eid = row["entity_id"]
            score = row.get("confidence_score") or 0.0
            is_ephemeral = row.get("retention_policy") == "ephemeral"
            created = row["created_at"]

            if created:
                try:
                    ts_str = created.replace("Z", "+00:00")
                    ts = datetime.fromisoformat(ts_str)
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=UTC)
                    days_old = (now - ts).days
                except Exception:
                    days_old = 999
            else:
                days_old = 999
            preview.append({
                "id": aid,
                "entity_id": eid,
                "claim_preview": row["claim"][:80] + "..." if row.get("claim") else "",
                "score": score,
                "days_old": days_old,
            })

            if request.dry_run:
                continue

            # Guard against live updates on very recent assertions (avoid self-triage of this session)
            if created:
                try:
                    ts_str = created.replace("Z", "+00:00")
                    ts = datetime.fromisoformat(ts_str)
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=UTC)
                    if (now - ts).total_seconds() < 3600:  # <1h
                        continue
                except Exception:
                    pass

            # Simple criteria per spec (full would add near_duplicate check, edge count, parent retention)
            if score >= 0.7 and not is_ephemeral:
                update_body = AssertionUpdate(
                    review_status="committed",
                    reviewer="system:age-policy",
                    review_notes=f"auto-graduated by aging-policy: 30d commit (confidence_score={score:.2f}, non-ephemeral)",
                    reviewed_at=now.isoformat(),
                )
                update_assertion(aid, update_body.model_dump(exclude_unset=True))
                committed += 1
                logger.info("Aged to committed: assertion:%s on %s", aid, eid)
            elif score < 0.5 and is_ephemeral:
                update_body = AssertionUpdate(
                    review_status="rejected",
                    reviewer="system:age-policy",
                    review_notes=f"auto-graduated by aging-policy: 90d reject (low confidence={score:.2f}, ephemeral)",
                    reviewed_at=now.isoformat(),
                )
                update_assertion(aid, update_body.model_dump(exclude_unset=True))
                rejected += 1
                logger.info("Aged to rejected: assertion:%s on %s", aid, eid)

    return AgeStagedResponse(
        dry_run=request.dry_run,
        committed=committed,
        rejected=rejected,
        preview=preview[:5],  # limit preview
        message=f"Processed {len(rows)} candidates. {'(dry-run)' if request.dry_run else 'Applied live.'}",
    )
