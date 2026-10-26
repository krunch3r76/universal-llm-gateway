"""Audit draft coverage of inbound message obligations."""

from __future__ import annotations

import json
from typing import Any

from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput

from ._obligation_coverage import (
    actionable_obligations,
    coverage_map_for_draft,
    missing_obligations_block,
)


def _step(payload: dict[str, Any]) -> StepOutput:
    return StepOutput(raw=json.dumps(payload), json=payload)


def _draft_text(draft_json: dict[str, Any] | None) -> str:
    if not isinstance(draft_json, dict):
        return ""
    inner = draft_json.get("draft")
    return inner if isinstance(inner, str) else ""


class WritingObligationCoverageHandler(BaseHandler):
    """Score the selected draft against the source obligation ledger."""

    step_type = "writing_obligation_coverage_v1"

    def __init__(self, caller: Any | None = None) -> None:
        super().__init__()
        self._caller = caller

    async def execute(self, step: Any, context: Any) -> StepOutput:
        del step
        outputs = getattr(context, "outputs", {}) or {}
        assemble = outputs.get("assemble")
        assemble_json = (
            assemble.get("json")
            if isinstance(assemble, dict)
            else getattr(assemble, "json", None)
        )
        if not isinstance(assemble_json, dict) or assemble_json.get("refused"):
            return _step({"ok": False, "skipped": True, "_skipped": True})
        src = outputs.get("source_obligations")
        src_json = (
            src.get("json")
            if isinstance(src, dict)
            else getattr(src, "json", None)
        )
        obligations = list((src_json or {}).get("obligations") or [])
        actionable = actionable_obligations(obligations)
        draft_out = outputs.get("draft")
        draft_json = (
            draft_out.get("json")
            if isinstance(draft_out, dict)
            else getattr(draft_out, "json", None)
        )
        draft_text = _draft_text(draft_json if isinstance(draft_json, dict) else None)
        if not actionable or not draft_text.strip():
            return _step(
                {
                    "ok": True,
                    "coverage_map": [],
                    "missing_count": 0,
                    "missing_obligations_block": "",
                }
            )
        if self._caller is not None:
            await self._caller()
        coverage_map = coverage_map_for_draft(draft_text, actionable)
        missing_count = sum(
            1 for row in coverage_map if row.get("status") == "missing"
        )
        block = missing_obligations_block(obligations, coverage_map)
        return _step(
            {
                "ok": True,
                "coverage_map": coverage_map,
                "missing_count": missing_count,
                "missing_obligations_block": block,
            }
        )
