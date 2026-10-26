"""Audit draft coverage of inbound message obligations."""

from __future__ import annotations

import json
import re
from typing import Any

from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput
from systems.pipeline.core.prompts import PromptBuilder

from ._obligation_coverage import (
    actionable_obligations,
    apply_audit_overlay,
    coverage_map_for_draft,
    missing_obligations_block,
)
from .assemble import _fence
from .independence import load_writing_models

_VALID_STATUS = frozenset({"addressed", "declined_with_reason", "missing"})


def _step(payload: dict[str, Any]) -> StepOutput:
    return StepOutput(raw=json.dumps(payload), json=payload)


def _draft_text(draft_json: dict[str, Any] | None) -> str:
    if not isinstance(draft_json, dict):
        return ""
    inner = draft_json.get("draft")
    return inner if isinstance(inner, str) else ""


def _prompt_template() -> str:
    from .prompt_store import load_prompts

    return str((load_prompts().get("obligation_coverage") or {}).get("template") or "")


def _parse_model_coverage(raw: str) -> list[dict[str, Any]] | None:
    text = raw.strip()
    if not text:
        return None
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    rows = parsed.get("coverage") if isinstance(parsed, dict) else parsed
    if not isinstance(rows, list):
        return None
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict) or not row.get("id"):
            continue
        status = str(row.get("status") or "")
        if status not in _VALID_STATUS:
            continue
        out.append(
            {
                "id": str(row["id"]),
                "status": status,
                "covering_sentence": str(row.get("covering_sentence") or ""),
            }
        )
    return out or None


def _merge_model_coverage(
    deterministic: list[dict[str, Any]],
    model_rows: list[dict[str, Any]] | None,
    draft_text: str,
    *,
    pin_ledger: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Prefer valid model rows per id; keep deterministic rows for gaps."""
    if not model_rows:
        return deterministic
    by_id = {str(row["id"]): row for row in model_rows if row.get("id")}
    merged: list[dict[str, Any]] = []
    for base in deterministic:
        oid = str(base.get("id") or "")
        model_row = by_id.get(oid)
        if model_row and model_row.get("status") in _VALID_STATUS:
            merged.append(
                apply_audit_overlay(
                    base, model_row, draft_text, pin_ledger=pin_ledger
                )
            )
        else:
            merged.append(dict(base))
    return merged


class WritingObligationCoverageHandler(BaseHandler):
    """Score the selected draft against the source obligation ledger."""

    step_type = "writing_obligation_coverage_v1"

    def __init__(self, caller: Any | None = None) -> None:
        super().__init__()
        self._caller = caller

    async def execute(self, step: Any, context: Any) -> StepOutput:
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
        pin_ledger = list(assemble_json.get("pin_ledger") or [])
        if not actionable or not draft_text.strip():
            return _step(
                {
                    "ok": True,
                    "coverage_map": [],
                    "missing_count": 0,
                    "missing_obligations_block": "",
                }
            )
        deterministic = coverage_map_for_draft(
            draft_text, actionable, pin_ledger=pin_ledger
        )
        model_rows: list[dict[str, Any]] | None = None
        src_block = str((src_json or {}).get("source_obligations_block") or "")
        template = _prompt_template()
        if template.strip():
            prompt = PromptBuilder().render(
                template,
                {
                    "source_obligations": src_block,
                    "draft": _fence(draft_text),
                },
            )
            models = load_writing_models()
            model_id = models.get("reviewer") or ""
            try:
                if self._caller is not None:
                    content = await self._caller()
                elif model_id:
                    result = await self._call_model(
                        model_id,
                        prompt,
                        step,
                        context,
                        temperature=0.1,
                    )
                    content = result.content
                else:
                    content = ""
                model_rows = _parse_model_coverage(
                    content if isinstance(content, str) else str(content or "")
                )
            except Exception:  # noqa: BLE001
                model_rows = None
        coverage_map = _merge_model_coverage(
            deterministic, model_rows, draft_text, pin_ledger=pin_ledger
        )
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
