"""Guarded apply handler — three-tier action classification with safety guards.

Implements Kumiho Dream State safety architecture:
- AUTO_APPLY: low relevance + low entrenchment + low cascade → soft deprecate
- RECOMMEND: LLM flagged for deprecation but doesn't meet AUTO_APPLY thresholds
- PROTECT: committed assertions and high-cascade entities are immune

In dry-run mode (default): classifies all actions but executes none.
"""

from __future__ import annotations

import json
import logging
from typing import Any, override

from systems.pipeline.core.execution.map_reduce.map_output_collection import (
    MapOutputCollection,
)
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput
from transport_utils import DEFAULT_CORTEX_URL, make_async_client

logger = logging.getLogger(__name__)


def _classify_action(
    assessment: dict[str, Any],
    assertion_meta: dict[str, Any],
    thresholds: dict[str, float],
) -> tuple[str, str]:
    """Classify an assessment into AUTO_APPLY, RECOMMEND, or PROTECT.

    Returns (tier, reason) tuple.
    """
    review_status = assertion_meta.get("review_status", "")
    impact = assertion_meta.get("impact_cascade_count", 0)
    entrenchment = assertion_meta.get("entrenchment_score", 1.0)
    relevance = assessment.get("relevance_score", 1.0)
    cascade_limit = thresholds.get("cascade_below", 5)

    if review_status == "committed":
        return "PROTECT", "review_status=committed (human-validated)"

    if impact >= cascade_limit:
        return "PROTECT", f"impact_cascade={impact} >= {cascade_limit}"

    if (
        assessment.get("should_deprecate", False)
        and relevance < thresholds.get("relevance_below", 0.2)
        and entrenchment < thresholds.get("entrenchment_below", 0.3)
        and impact < cascade_limit
    ):
        return "AUTO_APPLY", "all thresholds met"

    return "RECOMMEND", "flagged but does not meet AUTO_APPLY thresholds"


async def _apply_deprecation(assertion_id: int) -> bool:
    """Soft-deprecate an assertion via cortex-api PATCH."""
    try:
        async with make_async_client(DEFAULT_CORTEX_URL, timeout=10.0) as client:
            resp = await client.patch(
                f"/assertions/{assertion_id}",
                json={"review_status": "rejected"},
            )
            resp.raise_for_status()
            return True
    except Exception:
        logger.error("Failed to deprecate assertion %d", assertion_id)
        return False


class GuardedApplyHandler(BaseHandler):
    step_type = "dream_state_apply_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        assess_output = context.get_output("assess_batch")
        collect_output = context.get_output("collect_assertions")
        dry_run: bool = context.get_option("dry_run", True)
        thresholds: dict[str, Any] = context.get_option("auto_apply_threshold", {})

        assertion_meta: dict[int, dict[str, Any]] = {}
        if collect_output and collect_output.json:
            for batch in collect_output.json.get("batches", []):
                for a in batch:
                    assertion_meta[a["assertion_id"]] = a

        all_assessments: list[dict[str, Any]] = []
        if isinstance(assess_output, MapOutputCollection):
            for out in assess_output.all_outputs():
                if out and out.json:
                    all_assessments.extend(out.json.get("assessments", []))
        elif assess_output is not None and assess_output.json:
            all_assessments.extend(assess_output.json.get("assessments", []))

        actions_taken: list[dict[str, Any]] = []
        actions_skipped: list[dict[str, Any]] = []
        actions_protected: list[dict[str, Any]] = []
        error_count = 0

        for assessment in all_assessments:
            aid = assessment.get("assertion_id", 0)
            meta = assertion_meta.get(aid, {})
            tier, reason = _classify_action(assessment, meta, thresholds)

            entry: dict[str, Any] = {
                "assertion_id": aid,
                "claim_snippet": meta.get("claim", "")[:80],
                "tier": tier,
                "tier_reason": reason,
                "relevance_score": assessment.get("relevance_score"),
                "should_deprecate": assessment.get("should_deprecate", False),
                "deprecation_reason": assessment.get("deprecation_reason"),
                "enrichment_suggestions": assessment.get("enrichment_suggestions", {}),
                "relationships": assessment.get("relationships", []),
            }

            if tier == "PROTECT":
                entry["protection_reason"] = reason
                actions_protected.append(entry)
            elif tier == "AUTO_APPLY":
                if dry_run:
                    entry["dry_run"] = True
                    actions_taken.append(entry)
                else:
                    if await _apply_deprecation(aid):
                        actions_taken.append(entry)
                    else:
                        error_count += 1
            else:
                actions_skipped.append(entry)

        result: dict[str, Any] = {
            "actions_taken": actions_taken,
            "actions_skipped": actions_skipped,
            "actions_protected": actions_protected,
            "error_count": error_count,
            "dry_run": dry_run,
        }

        logger.info(
            "Dream state apply: %d taken, %d skipped, %d protected, "
            "%d errors (dry_run=%s)",
            len(actions_taken),
            len(actions_skipped),
            len(actions_protected),
            error_count,
            dry_run,
        )
        return StepOutput(raw=json.dumps(result, default=str), json=result)
