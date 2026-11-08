"""Frontier prose persona for local drafters — fail closed, no fallback."""

from __future__ import annotations

import json
import re
from typing import Any

from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput
from systems.pipeline.core.prompts import PromptBuilder
from writing_pipeline.events import WritingPersonaRefused

from .assemble import _fence
from .independence import load_writing_models
from .prompt_architect import _NAME, _NUMBER, _allowed_tokens
from .prompt_store import load_prompts

_BULLET = re.compile(r"(?m)^\s*[-*•]\s+\S")
_NUMBERED = re.compile(r"(?m)^\s*\d+\.\s+\S")
_NUMBERED_PAREN = re.compile(r"(?m)^\s*\d+\)\s+\S")
_LETTERED = re.compile(r"(?m)^\s*[a-zA-Z]\.\s+\S")
_HEADING = re.compile(r"(?m)^\s*#+\s+\S")
_SENTENCE_START = re.compile(r"(?:^|[.!?]\s+)([A-Z][a-z]{2,}\b)")
_CAPITAL_STOPWORDS = frozenset(
    {
        "you",
        "the",
        "when",
        "she",
        "his",
        "and",
        "but",
        "for",
        "with",
        "this",
        "that",
        "they",
        "their",
        "there",
        "then",
        "than",
        "from",
        "into",
        "your",
        "our",
        "who",
        "what",
        "where",
        "while",
        "also",
        "just",
        "only",
        "still",
        "even",
        "each",
        "every",
        "some",
        "such",
        "these",
        "those",
        "not",
        "can",
        "will",
        "would",
        "could",
        "should",
        "have",
        "has",
        "had",
        "are",
        "was",
        "were",
        "been",
        "does",
        "did",
    }
)


def _step(payload: dict[str, Any]) -> StepOutput:
    """Wrap a JSON dict as a pipeline step output."""
    return StepOutput(raw=json.dumps(payload), json=payload)


def _persona_template() -> str:
    """Load the local_persona_author user template or return empty when missing."""
    row = load_prompts().get("local_persona_author")
    if not isinstance(row, dict):
        return ""
    return str(row.get("template") or "").strip()


def _has_list_structure(text: str) -> bool:
    """Return True when persona text uses bullets, numbers, or headings."""
    patterns = (_BULLET, _NUMBERED, _NUMBERED_PAREN, _LETTERED, _HEADING)
    return any(pattern.search(text) for pattern in patterns)


def _sentence_initial_tokens(text: str) -> set[str]:
    """Collect capitalised tokens that open a sentence."""
    found: set[str] = set()
    for match in _SENTENCE_START.finditer(text):
        found.add(match.group(1))
    if text.strip():
        lead = re.match(r"\s*([A-Z][a-z]{2,})\b", text)
        if lead:
            found.add(lead.group(1))
    return found


def _capital_token_allowed(
    token: str,
    *,
    sentence_starts: set[str],
    allowed: set[str],
) -> bool:
    """True when a capitalised token may appear (allowed, stopword, or sentence-open)."""
    lower = token.lower()
    if lower in allowed:
        return True
    if token in sentence_starts or lower in _CAPITAL_STOPWORDS:
        return True
    return False


def _validate_persona_prose(
    raw_text: str,
    pin_ledger: list[dict[str, Any]],
    intake: dict[str, Any],
    obligations: list[dict[str, Any]] | None = None,
) -> tuple[str, bool]:
    """Validate persona prose against allowlists and structure rules."""
    text = raw_text.strip()
    if not text:
        return "", False
    if _has_list_structure(text):
        return text, False
    allowed = _allowed_tokens(intake, pin_ledger, obligations)
    sentence_starts = _sentence_initial_tokens(text)
    for match in _NAME.finditer(text):
        token = match.group(0)
        if _capital_token_allowed(token, sentence_starts=sentence_starts, allowed=allowed):
            continue
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

    def _refuse(
        self,
        context: Any,
        reason: str,
        *,
        rejected_text: str | None = None,
        error: str | None = None,
    ) -> StepOutput:
        """Build a fail-closed payload, publish a bus event, and skip local drafters."""
        self._publish_bus_event(context, WritingPersonaRefused(reason=reason))
        payload: dict[str, Any] = {
            "ok": False,
            "refused": reason,
            "persona_refused": reason,
            "_skipped": True,
        }
        if rejected_text:
            payload["persona_rejected_text"] = rejected_text
        if error:
            payload["error"] = error
        return _step(payload)

    async def execute(self, step: Any, context: Any) -> StepOutput:
        """Run persona authoring; any failure returns ok false with persona_refused."""
        try:
            return await self._execute_impl(step, context)
        except Exception as exc:  # noqa: BLE001
            return self._refuse(context, "persona_author_failed", error=type(exc).__name__)

    async def _execute_impl(self, step: Any, context: Any) -> StepOutput:
        """Inner execute body separated for a single exception boundary."""
        outputs = getattr(context, "outputs", {}) or {}
        assemble = outputs.get("assemble")
        assemble_json = (
            assemble.get("json")
            if isinstance(assemble, dict)
            else getattr(assemble, "json", None)
        )
        if not isinstance(assemble_json, dict) or assemble_json.get("refused"):
            return _step({"ok": False, "skipped": True, "_skipped": True})
        template = _persona_template()
        if not template:
            return self._refuse(context, "persona_prompt_missing")
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
        obligations_block = str((src_json or {}).get("source_obligations_block") or "")
        blocks = {
            "brief_block": assemble_json.get("brief_block") or "",
            "documents_block": assemble_json.get("documents_block") or "",
        }
        prompt = PromptBuilder().render(
            template,
            {
                "brief": blocks["brief_block"],
                "documents": blocks["documents_block"],
                "source_obligations": obligations_block,
                "architect_brief": architect_block,
            },
        )
        models = load_writing_models()
        model_id = models.get("local_persona_author") or models.get("prompt_architect") or ""
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
            return self._refuse(
                context,
                "persona_author_failed",
                error=type(exc).__name__,
            )
        raw = content if isinstance(content, str) else str(content or "")
        prose, valid = _validate_persona_prose(raw, pin_ledger, intake, obligations)
        if not valid:
            return self._refuse(
                context,
                "persona_validation_failed",
                rejected_text=prose or raw,
            )
        return _step(
            {
                "ok": True,
                "persona_prose": prose,
                "persona_prose_block": f"<persona_prose>\n{_fence(prose)}\n</persona_prose>",
            }
        )
