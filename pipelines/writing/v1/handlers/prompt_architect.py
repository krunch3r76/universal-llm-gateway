"""Clean-room prompt architect with a deterministic fallback brief.

Assemble is the only caller. A model brief that invents nouns or numbers
is rejected. Filing channels require numbered paragraphs and forbid a
caption, opener, or jurat. The handler does not send or write a draft.
"""

from __future__ import annotations

import json
import re
from typing import Any

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
    from .prompt_store import load_prompts

    row = load_prompts().get("prompt_architect") or {}
    return str(row.get("template") or "")


def _allowed_tokens(
    intake: dict[str, Any],
    pin_ledger: list[dict[str, Any]],
    obligations: list[dict[str, Any]] | None = None,
) -> set[str]:
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
    source_message = str(intake.get("source_message") or "")
    if source_message:
        allowed |= set(_NAME.findall(source_message))
        allowed |= set(_NUMBER.findall(source_message))
        allowed |= {token.lower() for token in _NAME.findall(source_message)}
        allowed |= set(_NUMBER.findall(source_message))
    for row in obligations or []:
        oid = str(row.get("id") or "")
        if oid:
            allowed.add(oid.lower())
        for key in ("quoted_span", "semantic_reading"):
            text = str(row.get(key) or "")
            allowed |= set(_NAME.findall(text))
            allowed |= set(_NUMBER.findall(text))
    return {token.lower() for token in allowed}


def _validate_architect_brief(
    raw_text: str,
    pin_ledger: list[dict[str, Any]],
    intake: dict[str, Any],
    obligations: list[dict[str, Any]] | None = None,
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
    allowed = _allowed_tokens(intake, pin_ledger, obligations)
    for token in _NAME.findall(text):
        if token.lower() not in allowed:
            return text, False
    for token in _NUMBER.findall(text):
        if token not in allowed and token.lower() not in allowed:
            return text, False
    return text, True


def _filing_exhibit_rule(intake: dict[str, Any]) -> str:
    if str(intake.get("channel") or "") != "filing":
        return ""
    return (
        "negative_constraints: require (Exhibit N) on every exhibit-backed fact; "
        "no listicle paragraphs without exhibit tags. "
        "Use numbered paragraphs (1. 2. 3.) grouped from the attested claims. "
        "Do not emit a caption, an opener, or a jurat."
    )


def _obligation_map_line(obligations: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for row in obligations:
        if str(row.get("obligation") or "none") == "none":
            continue
        oid = str(row.get("id") or "")
        if not oid:
            continue
        mode = (
            "act_then_confirm"
            if str(row.get("obligation")) == "act_outside_message"
            else "answer_in_message"
        )
        parts.append(f"{oid}: {mode}")
    if not parts:
        return ""
    return "obligation_map: " + "; ".join(parts)


def _fallback_brief(
    blocks: dict[str, str],
    intake: dict[str, Any],
    obligations: list[dict[str, Any]] | None = None,
) -> str:
    max_chars = intake.get("max_chars")
    budget = str(max_chars) if max_chars else "none"
    filing_rule = _filing_exhibit_rule(intake)
    constraints = (
        filing_rule or "negative_constraints: no new facts; attested pins only"
    )
    lines = [
        "lead_pin_id: P1",
        "beat_order: verbatim order",
        f"audience_posture: {_fence(str(intake.get('channel') or 'email'))}",
        constraints,
        f"char_budget: {budget}",
    ]
    map_line = _obligation_map_line(list(obligations or []))
    if map_line:
        lines.append(map_line)
    lines.extend(
        [
            blocks.get("verbatim_notes_block") or "",
            blocks.get("brief_block") or "",
        ]
    )
    return "\n".join(lines)


class WritingPromptArchitectHandler(BaseHandler):
    """Build the architect brief from assemble blocks, with a local fallback.

    The executor calls ``execute`` once. A model reply that names new nouns
    or numbers is discarded and the deterministic fallback is returned.
    Filing channels add the numbered-paragraph rule. No bus post and no file
    write.
    """

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
        src = outputs.get("source_obligations")
        src_json = (
            src.get("json")
            if isinstance(src, dict)
            else getattr(src, "json", None)
        )
        obligations = list((src_json or {}).get("obligations") or [])
        source_block = str(assemble_json.get("source_message_block") or "")
        obligations_block = str((src_json or {}).get("source_obligations_block") or "")
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
                "source_message": source_block,
                "source_obligations": obligations_block,
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
                )
                content = result.content
        except Exception as exc:  # noqa: BLE001
            brief = _fallback_brief(blocks, intake, obligations)
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
        brief, valid = _validate_architect_brief(raw, pin_ledger, intake, obligations)
        if not valid:
            brief = _fallback_brief(blocks, intake, obligations)
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
