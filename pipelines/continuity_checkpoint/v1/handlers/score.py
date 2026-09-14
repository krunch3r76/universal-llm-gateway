"""CHECKPOINT score step — charter journal and work-item fold (projection)."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, override

from implement_admission.conductor_score_io import forward_mutate_tip_at
from implement_admission.conductor_score_locus import charter_locus
from implement_admission.conductor_witness import fold_scoreboard
from implement_admission.conductor_witness_defaults import (
    DefaultWitnessCortex,
    fold_deps_for_admit,
)
from implement_admission.conductor_witness_types import FoldDeps
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput

logger = logging.getLogger(__name__)

_REPO = Path("/mnt/torus/projects/universal-llm-gateway")


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


def project_work_item_fold(
    slug: str,
    *,
    deps: FoldDeps | None = None,
    files_root: Path | None = None,
) -> dict[str, Any]:
    """Project work-item Status from witnesses. Does not journal (phase A)."""
    fold_deps = deps
    if fold_deps is None:
        fold_deps = fold_deps_for_admit(
            f"todo:{slug}",
            cortex=DefaultWitnessCortex(),
            repo=_REPO,
        )
    try:
        fold = fold_scoreboard(
            slug, deps=fold_deps, files_root=files_root, write_journal=False
        )
    except Exception as exc:  # noqa: BLE001 — checkpoint must not fail on fold
        logger.warning("work-item fold failed for %s: %s", slug, exc)
        return {"skipped": True, "error": str(exc)[:200]}
    if fold is None:
        return {"skipped": True, "reason": "no_tip"}
    return {
        "skipped": False,
        "slug": slug,
        "row_status": fold.row_status,
        "entry_gate": fold.entry_gate,
        "tip_sha": fold.tip_sha,
        "journal_applied": fold.journal_applied,
    }


class ContinuityCheckpointScoreHandler(BaseHandler):
    """Optional charter journal and/or work-item witness projection."""

    step_type = "continuity_checkpoint_score_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        options: dict[str, Any] = getattr(context, "options", {}) or {}
        tip_body = str(options.get("scoreboard_tip") or "").strip()
        slug = str(options.get("scoreboard_slug") or "").strip()
        if not tip_body and not slug:
            payload = {"skipped": True, "tip_sha": None, "tip_uri": None}
            return StepOutput(raw=json.dumps(payload), json=payload)

        thread = str(options.get("thread") or context.dispatch_thread_id or "")
        from_agent = str(options.get("from_agent") or "continuity")
        execution_id = str(context.execution_id or "")
        delta = str(options.get("scoreboard_delta") or "checkpoint")
        payload: dict[str, Any] = {"skipped": False}
        if tip_body:
            payload.update(
                journal_charter_tip(
                    thread=thread,
                    tip_body=tip_body,
                    seat=from_agent,
                    dispatch_id=execution_id or None,
                    delta=delta,
                )
            )
        if slug:
            payload["fold"] = project_work_item_fold(slug)
        return StepOutput(raw=json.dumps(payload, default=str), json=payload)
