"""Prose-rhythm reviser with review-finding application and disposition tracking."""

from __future__ import annotations

import json
import re
from typing import Any

from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput
from systems.pipeline.core.prompts import PromptBuilder

from .independence import load_writing_models
from .provenance_check import _resolve_binding
from .seat_dispatch import _prompts
from .seat_wait import findings_actionable, sanitize_plain_text_draft


def _step(payload: dict[str, Any], *, model_id: str | None = None) -> StepOutput:
    return StepOutput(raw=json.dumps(payload), json=payload, model_id=model_id)


def _review_json(outputs: dict[str, Any]) -> dict[str, Any]:
    for key in ("fact_ceiling_audit", "review"):
        out = outputs.get(key)
        if isinstance(out, dict):
            payload = out.get("json")
            if isinstance(payload, dict):
                return payload
        payload = getattr(out, "json", None)
        if isinstance(payload, dict):
            return payload
    return {}


def _format_review_findings(review: dict[str, Any]) -> str:
    if str(review.get("verdict") or "") != "revise":
        return ""
    findings = findings_actionable(review.get("findings"))
    if not findings:
        return ""
    return json.dumps(findings, ensure_ascii=False, indent=2)


def _normalize_draft(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip())


def _finding_applied(draft_v2: str, fix: str, *, draft_changed: bool) -> bool:
    fix_text = fix.strip()
    if not fix_text:
        return draft_changed
    lower = draft_v2.lower()
    if fix_text.lower() in lower:
        return True
    for fragment in re.split(r"[.;]\s+", fix_text):
        piece = fragment.strip()
        if len(piece) >= 12 and piece.lower() in lower:
            return True
    return draft_changed


def revision_dispositions(
    draft_v1: str, draft_v2: str, findings: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Name each finding applied or declined with a short reason."""
    changed = _normalize_draft(draft_v1) != _normalize_draft(draft_v2)
    rows: list[dict[str, Any]] = []
    for row in findings:
        if not isinstance(row, dict):
            continue
        fix = str(row.get("fix") or "")
        applied = _finding_applied(draft_v2, fix, draft_changed=changed)
        rows.append(
            {
                "ref": row.get("ref"),
                "type": row.get("type"),
                "status": "applied" if applied else "declined",
                "reason": None if applied else "fix_not_reflected_in_draft_v2",
            }
        )
    return rows


def _insert_before_closing(draft: str, addition: str) -> str:
    text = addition.strip()
    if not text:
        return draft
    if not text.endswith((".", "!", "?")):
        text = text.rstrip(".") + "."
    parts = draft.rsplit("\n\n", 1)
    if len(parts) == 2 and re.match(
        r"^(?:best|sincerely|thanks|thank you|regards|cheers)",
        parts[1].strip(),
        re.I,
    ):
        return f"{parts[0].rstrip()}\n\n{text}\n\n{parts[1]}"
    return f"{draft.rstrip()}\n\n{text}"


def apply_actionable_findings(draft_v1: str, findings: list[dict[str, Any]]) -> str:
    """Deterministic fallback when the reviser model returns unchanged text."""
    body = draft_v1
    for row in findings:
        if not isinstance(row, dict):
            continue
        fix = str(row.get("fix") or "").strip()
        if not fix:
            continue
        if fix.lower() in body.lower():
            continue
        body = _insert_before_closing(body, fix)
    return body


class WritingProseRhythmHandler(BaseHandler):
    """Run the reviser model; apply review findings when verdict is revise."""

    step_type = "writing_prose_rhythm_v1"

    def __init__(self, caller: Any | None = None) -> None:
        super().__init__()
        self._caller = caller

    async def execute(self, step: Any, context: Any) -> StepOutput:
        outputs = getattr(context, "outputs", {}) or {}
        draft_v1 = str(_resolve_binding(step, context, "draft") or "")
        if not draft_v1.strip():
            draft_out = outputs.get("draft")
            draft_json = (
                draft_out.get("json")
                if isinstance(draft_out, dict)
                else getattr(draft_out, "json", None)
            )
            if isinstance(draft_json, dict):
                draft_v1 = str(draft_json.get("draft") or "")

        review = _review_json(outputs)
        findings = findings_actionable(review.get("findings"))
        review_block = _format_review_findings(review)

        row = _prompts().get("prose_rhythm") or {}
        fields = {
            "draft": draft_v1,
            "documents": str(_resolve_binding(step, context, "documents") or ""),
            "brief": str(_resolve_binding(step, context, "brief") or ""),
            "missing_obligations": str(
                _resolve_binding(step, context, "missing_obligations") or ""
            ),
            "source_obligations": str(
                _resolve_binding(step, context, "source_obligations") or ""
            ),
            "review_findings": review_block,
        }
        prompt = PromptBuilder().render(str(row.get("template") or ""), fields)
        system = str(row.get("system_prompt") or "")
        models = load_writing_models()
        model_id = str(models.get("reviser") or "")
        temperature = 0.3
        gen = getattr(step, "generation_parameters", None) or {}
        if isinstance(gen, dict) and gen.get("temperature") is not None:
            temperature = float(gen["temperature"])

        try:
            if self._caller is not None:
                content = await self._caller()
            else:
                result = await self._call_model(
                    model_id,
                    prompt,
                    step,
                    context,
                    system_prompt=system,
                    temperature=temperature,
                )
                content = result.content
        except Exception as exc:  # noqa: BLE001
            return StepOutput(
                raw="",
                json=None,
                error=type(exc).__name__,
            )

        draft_v2 = sanitize_plain_text_draft(
            content if isinstance(content, str) else str(content or "")
        )
        if (
            str(review.get("verdict") or "") == "revise"
            and findings
            and _normalize_draft(draft_v2) == _normalize_draft(draft_v1)
        ):
            draft_v2 = apply_actionable_findings(draft_v1, findings)

        dispositions = revision_dispositions(draft_v1, draft_v2, findings)
        payload = {
            "draft": draft_v2,
            "dispositions": dispositions,
            "findings_actionable": findings,
        }
        return StepOutput(raw=draft_v2, json=payload, model_id=model_id)
