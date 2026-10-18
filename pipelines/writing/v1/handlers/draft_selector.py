"""Deterministic multi-weight draft selector (no model)."""

from __future__ import annotations

import json
import re
from typing import Any

from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput

from .independence import load_writing_models, model_family
from .seat_wait import sanitize_plain_text_draft

_DRAFTER_STEPS: tuple[tuple[str, str], ...] = (
    ("drafter_openai", "drafter_openai"),
    ("drafter_google", "drafter_google"),
    ("drafter_xai", "drafter_xai"),
    ("drafter_local_hermes", "drafter_local_hermes"),
    ("drafter_local_ernie", "drafter_local_ernie"),
    ("drafter_anthropic", "drafter_anthropic"),
)

_TOKEN = re.compile(r"[a-z0-9]+")


def _step(payload: dict[str, Any], *, model_id: str | None = None) -> StepOutput:
    return StepOutput(raw=json.dumps(payload), json=payload, model_id=model_id)


def _normalize_tokens(text: str) -> set[str]:
    return set(_TOKEN.findall(text.lower()))


def _pin_tokens(pin: dict[str, Any]) -> set[str]:
    excerpt = str(pin.get("excerpt") or "")
    return _normalize_tokens(excerpt)


def _score_draft(draft_text: str, attested_pins: list[dict[str, Any]]) -> int:
    draft_tokens = _normalize_tokens(draft_text)
    score = 0
    for pin in attested_pins:
        anchors = _pin_tokens(pin)
        if anchors and anchors <= draft_tokens:
            score += 1
        elif anchors & draft_tokens:
            score += 1
    return score


def _raw_text(step: Any) -> str:
    if step is None:
        return ""
    raw = step.get("raw") if isinstance(step, dict) else getattr(step, "raw", None)
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    if not isinstance(raw, str):
        return ""
    text = raw.strip()
    if text.startswith("{") and '"draft"' in text:
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict) and isinstance(parsed.get("draft"), str):
                return parsed["draft"]
        except json.JSONDecodeError:
            pass
    return text


class WritingDraftSelectorHandler(BaseHandler):
    """Pick the highest attested-pin coverage candidate; stable tie order."""

    step_type = "writing_draft_selector_v1"

    async def execute(self, step: Any, context: Any) -> StepOutput:
        del step
        outputs = getattr(context, "outputs", {}) or {}
        assemble = outputs.get("assemble")
        assemble_json = (
            assemble.get("json")
            if isinstance(assemble, dict)
            else getattr(assemble, "json", None)
        )
        ledger: list[dict[str, Any]] = []
        if isinstance(assemble_json, dict):
            ledger = list(assemble_json.get("pin_ledger") or [])
        attested = [pin for pin in ledger if pin.get("attested") is True]

        aliases = load_writing_models()
        candidates: list[dict[str, Any]] = []
        for step_name, role in _DRAFTER_STEPS:
            out = outputs.get(step_name)
            err = out.get("error") if isinstance(out, dict) else getattr(out, "error", None)
            if err:
                continue
            text = sanitize_plain_text_draft(_raw_text(out))
            if not text.strip():
                continue
            model_id = (
                out.get("model_id")
                if isinstance(out, dict)
                else getattr(out, "model_id", None)
            ) or aliases.get(role) or ""
            candidates.append(
                {
                    "role": role,
                    "step": step_name,
                    "draft": text,
                    "model_id": model_id,
                    "family": model_family(str(model_id)),
                    "score": _score_draft(text, attested),
                }
            )

        if not candidates:
            return _step(
                {
                    "ok": False,
                    "refused": "no_draft_candidates",
                    "error": "all drafters failed or empty",
                }
            )

        best_score = max(row["score"] for row in candidates)
        tied = [row for row in candidates if row["score"] == best_score]
        winner = tied[0]
        return _step(
            {
                "ok": True,
                "refused": None,
                "draft": winner["draft"],
                "selected_role": winner["role"],
                "selected_step": winner["step"],
                "candidates": candidates,
                "coverage_score": winner["score"],
            },
            model_id=str(winner["model_id"]),
        )
