"""Frontier prose persona for local drafters — fail closed, no fallback."""

from __future__ import annotations

import json
import re
from typing import Any

from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput
from systems.pipeline.core.prompts import PromptBuilder
from universal_logging import get_logger
from writing_pipeline.events import WritingPersonaRefused

from .assemble import _fence
from .independence import load_writing_models
from .prompt_architect import _NAME, _NUMBER
from .prompt_store import load_prompts

_BULLET = re.compile(r"(?m)^\s*[-*•]\s+\S")
_NUMBERED = re.compile(r"(?m)^\s*\d+\.\s+\S")
_NUMBERED_PAREN = re.compile(r"(?m)^\s*\d+\)\s+\S")
_LETTERED = re.compile(r"(?m)^\s*[a-zA-Z]\.\s+\S")
_HEADING = re.compile(r"(?m)^\s*#+\s+\S")
_BLOCKS_WORD_RE = re.compile(r"[a-z][a-z'-]*")
_LOGGER = get_logger(__name__)
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


def _persona_allowed_tokens(
    *,
    brief_block: str,
    documents_block: str,
    architect_brief_block: str,
) -> set[str]:
    """Allowlist nouns and numbers taken only from persona prompt blocks."""
    allowed: set[str] = set()
    for chunk in (brief_block, documents_block, architect_brief_block):
        allowed |= {token.lower() for token in _NAME.findall(chunk)}
        allowed |= set(_NUMBER.findall(chunk))
    return allowed


def _has_list_structure(text: str) -> bool:
    """Return True when persona text uses bullets, numbers, or headings."""
    patterns = (_BULLET, _NUMBERED, _NUMBERED_PAREN, _LETTERED, _HEADING)
    return any(pattern.search(text) for pattern in patterns)


def _is_sentence_initial_at(text: str, match: re.Match[str]) -> bool:
    """True when the match begins a sentence (start of text or after .!? )."""
    prefix = text[: match.start()].rstrip()
    if not prefix:
        return True
    return bool(re.search(r"[.!?]\s*$", prefix))


def _blocks_word_set(blocks_lower: str) -> set[str]:
    """Lowercase words extracted from persona prompt blocks (whole tokens only)."""
    return set(_BLOCKS_WORD_RE.findall(blocks_lower))


def _capital_token_allowed(
    text: str,
    match: re.Match[str],
    *,
    allowed: set[str],
    blocks_words: set[str],
) -> bool:
    """Enforce positional sentence-start rules vs mid-sentence allowlist."""
    token = match.group(0)
    lower = token.lower()
    if lower in allowed:
        return True
    if not _is_sentence_initial_at(text, match):
        return False
    if lower in _CAPITAL_STOPWORDS:
        return True
    return lower in blocks_words


def _validate_persona_prose(
    raw_text: str,
    *,
    brief_block: str,
    documents_block: str,
    architect_brief_block: str,
) -> tuple[str, bool]:
    """Validate persona prose against structure rules and block allowlists."""
    text = raw_text.strip()
    if not text:
        return "", False
    if _has_list_structure(text):
        return text, False
    blocks_lower = "\n".join(
        (brief_block, documents_block, architect_brief_block)
    ).lower()
    blocks_words = _blocks_word_set(blocks_lower)
    allowed = _persona_allowed_tokens(
        brief_block=brief_block,
        documents_block=documents_block,
        architect_brief_block=architect_brief_block,
    )
    for match in _NAME.finditer(text):
        if not _capital_token_allowed(
            text, match, allowed=allowed, blocks_words=blocks_words
        ):
            return text, False
    for token in _NUMBER.findall(text):
        if token not in allowed:
            return text, False
    return text, True


class WritingLocalPersonaAuthorHandler(BaseHandler):
    """Emit persona_prose for local drafters; invalid output fails the branch."""

    step_type = "writing_local_persona_author_v1"

    def __init__(self, caller: Any | None = None) -> None:
        """Optional ``caller`` injects model output for offline tests."""
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
        """Build a fail-closed payload and publish an observation event when possible."""
        try:
            self._publish_bus_event(context, WritingPersonaRefused(reason=reason))
        except Exception as exc:  # noqa: BLE001
            _LOGGER.debug(
                "persona refuse bus publish failed reason=%s: %s",
                reason,
                exc,
            )
        payload: dict[str, Any] = {
            "ok": False,
            "refused": reason,
            "persona_refused": reason,
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
            return self._refuse(
                context, "persona_author_failed", error=type(exc).__name__
            )

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
        arch = outputs.get("prompt_architect")
        arch_json = (
            arch.get("json") if isinstance(arch, dict) else getattr(arch, "json", None)
        )
        architect_block = str((arch_json or {}).get("architect_brief_block") or "")
        brief_block = str(assemble_json.get("brief_block") or "")
        documents_block = str(assemble_json.get("documents_block") or "")
        prompt = PromptBuilder().render(
            template,
            {
                "brief": brief_block,
                "documents": documents_block,
                "architect_brief": architect_block,
            },
        )
        models = load_writing_models()
        model_id = models.get("local_persona_author") or ""
        if not model_id:
            return self._refuse(context, "persona_model_missing")
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
        prose, valid = _validate_persona_prose(
            raw,
            brief_block=brief_block,
            documents_block=documents_block,
            architect_brief_block=architect_block,
        )
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
