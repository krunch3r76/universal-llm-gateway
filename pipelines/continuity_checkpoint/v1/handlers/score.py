"""Journal a seat-authored charter scoreboard tip during CHECKPOINT."""

from __future__ import annotations

import json
import logging
from typing import Any, override

from implement_admission.conductor_score_io import forward_mutate_tip_at
from implement_admission.conductor_score_locus import charter_locus
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput

logger = logging.getLogger(__name__)


def journal_charter_tip(
    *,
    thread: str,
    tip_body: str,
    seat: str,
    dispatch_id: str | None,
    delta: str,
    files_root: Any = None,
) -> dict[str, Any]:
    """Write a seat-authored charter tip through the journaled kernel. No fold."""
    locus = charter_locus(thread, files_root=files_root)
    result = forward_mutate_tip_at(
        locus,
        next_body=tip_body,
        seat=seat,
        dispatch_id=dispatch_id,
        reason="checkpoint",
        rows=(),
        delta=delta,
    )
    return {
        "tip_uri": locus.tip_uri,
        "tip_sha": result.tip_sha,
        "rejected_reason": result.rejected_reason,
        "journal_uri": locus.journal_uri,
        "skipped": False,
    }


class ContinuityCheckpointScoreHandler(BaseHandler):
    """Optional journaled write of a seat-authored charter scoreboard tip."""

    step_type = "continuity_checkpoint_score_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        options: dict[str, Any] = getattr(context, "options", {}) or {}
        tip_body = str(options.get("scoreboard_tip") or "").strip()
        if not tip_body:
            payload = {"skipped": True, "tip_sha": None, "tip_uri": None}
            return StepOutput(raw=json.dumps(payload), json=payload)

        thread = str(options.get("thread") or context.dispatch_thread_id or "")
        from_agent = str(options.get("from_agent") or "continuity")
        execution_id = str(context.execution_id or "")
        delta = str(options.get("scoreboard_delta") or "checkpoint")
        payload = journal_charter_tip(
            thread=thread,
            tip_body=tip_body,
            seat=from_agent,
            dispatch_id=execution_id or None,
            delta=delta,
        )
        return StepOutput(raw=json.dumps(payload), json=payload)
