"""Frontier prose persona for local drafters — fail closed, no fallback."""

from __future__ import annotations

import json
import re
from typing import Any

from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput
from systems.pipeline.core.prompts import PromptBuilder

from .assemble import _fence
from .independence import load_writing_models
from .prompt_architect import _NAME, _NUMBER, _allowed_tokens
from .prompt_store import load_prompts

_BULLET = re.compile(r"(?m)^\s*[-*•]\s+\S")
_NUMBERED = re.compile(r"(?m)^\s*\d+\.\s+\S")


def _step(payload: dict[str, Any]) -> StepOutput:
    return StepOutput(raw=json.dumps(payload), json=payload)


def _persona_template() -> str:
    row = load_prompts().get("local_persona_author") or {}
    return str(row.get("template") or "")


def _validate_persona_prose(
    raw_text: str,
    pin_ledger: list[dict[str, Any]],
    intake: dict[str, Any],
    obligations: list[dict[str, Any]] | None = None,
) -> tuple[str, bool]:
    text = raw_text.strip()
    if not text:
        return "", False
    if _BULLET.search(text) or _NUMBERED.search(text):
        return text, False
    allowed = _allowed_tokens(intake, pin_ledger, obligations)
    for token in _NAME.findall(text):
        if token.lower() not in allowed:
            return text, False
    for token in _NUMBER.findall(text):
        if token not in allowed and token.lower() not in allowed:
            return text, False
    return text, True


class WritingLocalPersonaAuthorHandler(BaseHandler):
    """Emit persona_prose for local drafters; invalid output fails the branch."""

    step_type = "writing_local_persona_author_v1"

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
        arch = outputs.get("prompt_architect")
        arch_json = (
            arch.get("json")
            if isinstance(arch, dict)
            else getattr(arch, "json", None)
        )
        architect_block = str((arch_json or {}).get("architect_brief_block") or "")
        source_block = str(assemble_json.get("source_message_block") or "")
        obligations_block = str((src_json or {}).get("source_obligations_block") or "")
        blocks = {
            "brief_block": assemble_json.get("brief_block") or "",
            "documents_block": assemble_json.get("documents_block") or "",
        }
        template = _persona_template()
        prompt = PromptBuilder().render(
            template,
            {
                "verbatim_notes": assemble_json.get("verbatim_notes_block") or "",
                "brief": blocks["brief_block"],
                "documents": blocks["documents_block"],
                "source_message": source_block,
                "source_obligations": obligations_block,
                "architect_brief": architect_block,
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
            return _step(
                {
                    "ok": False,
                    "refused": "persona_author_failed",
                    "error": type(exc).__name__,
                    "_skipped": True,
                }
            )
        raw = content if isinstance(content, str) else str(content or "")
        prose, valid = _validate_persona_prose(raw, pin_ledger, intake, obligations)
        if not valid:
            return _step(
                {
                    "ok": False,
                    "refused": "persona_validation_failed",
                    "persona_prose": prose,
                    "_skipped": True,
                }
            )
        return _step(
            {
                "ok": True,
                "persona_prose": prose,
                "persona_prose_block": f"<persona_prose>\n{_fence(prose)}\n</persona_prose>",
            }
        )
