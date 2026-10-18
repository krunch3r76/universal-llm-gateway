"""Clean-room prompt architect with deterministic fallback."""

from __future__ import annotations

import json
import re
from typing import Any

import yaml
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput
from systems.pipeline.core.prompts import PromptBuilder

from .assemble import _fence
from .independence import load_writing_models

_NUMBER = re.compile(r"\b\d+(?:\.\d+)?\b")
_NAME = re.compile(r"\b[A-Z][a-z]{2,}\b")


def _step(payload: dict[str, Any]) -> StepOutput:
    return StepOutput(raw=json.dumps(payload), json=payload)


def _architect_template() -> str:
    from pathlib import Path

    prompts_path = Path(__file__).resolve().parents[1] / "prompts.yaml"
    data = yaml.safe_load(prompts_path.read_text(encoding="utf-8")) or {}
    return str((data.get("prompts") or {}).get("prompt_architect", {}).get("template") or "")


def _allowed_tokens(intake: dict[str, Any], pin_ledger: list[dict[str, Any]]) -> set[str]:
    allowed: set[str] = set()
    for pin in pin_ledger:
        allowed |= set(_NAME.findall(str(pin.get("excerpt") or "")))
        allowed |= set(_NUMBER.findall(str(pin.get("excerpt") or "")))
    for key in ("recipient", "objective", "channel", "signer", "voice_anchor"):
        val = intake.get(key)
        if val:
            allowed |= set(_NAME.findall(str(val)))
            allowed |= set(_NUMBER.findall(str(val)))
    for note in intake.get("verbatim_notes") or []:
        allowed |= set(_NAME.findall(str(note)))
        allowed |= set(_NUMBER.findall(str(note)))
    return {token.lower() for token in allowed}


def _validate_architect_brief(
    raw_text: str,
    pin_ledger: list[dict[str, Any]],
    intake: dict[str, Any],
) -> tuple[str, bool]:
    text = raw_text.strip()
    if not text:
        return "", False
    required = (
        "lead_pin_id",
        "beat_order",
        "audience_posture",
        "negative_constraints",
        "char_budget",
    )
    lowered = text.lower()
    if not all(key in lowered for key in required):
        return text, False
    allowed = _allowed_tokens(intake, pin_ledger)
    for token in _NAME.findall(text):
        if token.lower() not in allowed:
            return text, False
    for token in _NUMBER.findall(text):
        if token not in allowed and token.lower() not in allowed:
            return text, False
    return text, True


def _fallback_brief(blocks: dict[str, str], intake: dict[str, Any]) -> str:
    max_chars = intake.get("max_chars")
    budget = str(max_chars) if max_chars else "none"
    return "\n".join(
        [
            "lead_pin_id: P1",
            "beat_order: verbatim order",
            f"audience_posture: { _fence(str(intake.get('channel') or 'email')) }",
            "negative_constraints: no new facts; attested pins only",
            f"char_budget: {budget}",
            blocks.get("verbatim_notes_block") or "",
            blocks.get("brief_block") or "",
        ]
    )


class WritingPromptArchitectHandler(BaseHandler):
    step_type = "writing_prompt_architect_v1"

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
        intake = dict(assemble_json.get("intake") or {})
        pin_ledger = list(assemble_json.get("pin_ledger") or [])
        blocks = {
            "verbatim_notes_block": assemble_json.get("verbatim_notes_block") or "",
            "brief_block": assemble_json.get("brief_block") or "",
            "documents_block": assemble_json.get("documents_block") or "",
        }
        template = _architect_template()
        prompt = PromptBuilder().render(
            template,
            {
                "verbatim_notes": blocks["verbatim_notes_block"],
                "brief": blocks["brief_block"],
            },
        )
        models = load_writing_models()
        model_id = models.get("prompt_architect") or ""
        try:
            if self._caller is not None:
                content = await self._caller()
            else:
                result = await self._call_model(
                    model_id,
                    prompt,
                    step,
                    context,
                    temperature=0.2,
                    max_tokens=2048,
                )
                content = result.content
        except Exception as exc:  # noqa: BLE001
            brief = _fallback_brief(blocks, intake)
            return _step(
                {
                    "ok": True,
                    "fallback_used": True,
                    "fallback_reason": type(exc).__name__,
                    "architect_brief": brief,
                    "architect_brief_block": f"<architect_brief>\n{_fence(brief)}\n</architect_brief>",
                }
            )
        raw = content if isinstance(content, str) else str(content or "")
        brief, valid = _validate_architect_brief(raw, pin_ledger, intake)
        if not valid:
            brief = _fallback_brief(blocks, intake)
            return _step(
                {
                    "ok": True,
                    "fallback_used": True,
                    "fallback_reason": "validation_failed",
                    "architect_brief": brief,
                    "architect_brief_block": f"<architect_brief>\n{_fence(brief)}\n</architect_brief>",
                }
            )
        return _step(
            {
                "ok": True,
                "fallback_used": False,
                "architect_brief": brief,
                "architect_brief_block": f"<architect_brief>\n{_fence(brief)}\n</architect_brief>",
            }
        )
