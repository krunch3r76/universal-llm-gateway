"""Extract inbound-message obligations before drafting."""

from __future__ import annotations

import json
import re
from typing import Any

from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput
from systems.pipeline.core.prompts import PromptBuilder

from .assemble import _fence
from .independence import load_writing_models

_APPLY_CTA = re.compile(
    r"\bapply\s+here\b|https?://[^\s]+/apply\b",
    re.I,
)
_QUESTION = re.compile(r"\?\s*$")
_REQUEST = re.compile(
    r"\b(?:please|could you|can you|would you|let me know|send|provide|confirm)\b",
    re.I,
)
_DEADLINE = re.compile(r"\b(?:deadline|due by|by \w+day|before \w+)\b", re.I)
_ATTACHMENT = re.compile(r"\b(?:attach(?:ed|ment)?|enclosed|see attached)\b", re.I)
_PLEASANTRY = re.compile(
    r"^(?:hi|hello|thanks|thank you|best,|regards,|cheers,)\s*\.?\s*$",
    re.I,
)
_REPLY_CLOSING = re.compile(
    r"^(?:please\s+)?let\s+(?:me|us)\s+know"
    r"(?:\s+(?:if\s+that\s+works|what\s+works|either\s+way|soon|when\s+you\s+can))?"
    r"[.!,\s]*$",
    re.I,
)
_TRAILING_SIGNATURE = re.compile(r"[\s—–-]+[\w'.-]+\s*$")
_COURTESY_TOKENS = re.compile(
    r"\b(?:thanks?|thank\s+you|please)\b[.!,\s]*",
    re.I,
)
_GREETING_PREFIX = re.compile(
    r"^(?:"
    r"hi(?:\s+[\w'.-]+)?\s*[,—–-]\s*|"
    r"hello\s*[,—–-]\s*|"
    r"thanks?\s*,?\s*|"
    r"thank you\s*,?\s*"
    r")",
    re.I,
)


def _step(payload: dict[str, Any]) -> StepOutput:
    return StepOutput(raw=json.dumps(payload), json=payload)


def _prompt_template() -> str:
    from .prompt_store import load_prompts

    return str((load_prompts().get("source_obligations") or {}).get("template") or "")


def _format_block(obligations: list[dict[str, Any]]) -> str:
    if not obligations:
        return ""
    lines = ["<source_obligations>"]
    for row in obligations:
        oid = row.get("id")
        lines.append(
            f'{oid} kind={row.get("kind")} obligation={row.get("obligation")} '
            f'quoted="{_fence(str(row.get("quoted_span") or ""))}" '
            f'reading="{_fence(str(row.get("semantic_reading") or ""))}"'
        )
    lines.append("</source_obligations>")
    return "\n".join(lines)


_SIGNATURE_ONLY = re.compile(r"^[\s—–-]+[\w'.-]+\s*$")


def _split_spans(source_message: str) -> list[str]:
    chunks = re.split(r"(?<=[.!?])\s+|\n+", source_message.strip())
    spans = [chunk.strip() for chunk in chunks if chunk.strip()]
    merged: list[str] = []
    for span in spans:
        if merged and _SIGNATURE_ONLY.match(span):
            merged[-1] = f"{merged[-1]} {span}".strip()
        else:
            merged.append(span)
    return merged


def _strip_greeting_prefix(span: str) -> str:
    text = span.strip()
    while text:
        match = _GREETING_PREFIX.match(text)
        if not match:
            break
        text = text[match.end() :].strip()
    return text


def _is_reply_closing_pleasantry(span: str) -> bool:
    """True when span is only a reply-invitation sign-off without substantive ask."""
    text = _strip_greeting_prefix(span).strip()
    text = _TRAILING_SIGNATURE.sub("", text).strip()
    while True:
        stripped = _COURTESY_TOKENS.sub("", text).strip()
        if stripped == text:
            break
        text = stripped
    if not text:
        return True
    return bool(_REPLY_CLOSING.match(text))


def _classify_span(span: str) -> tuple[str, str, str]:
    """Return kind, obligation, and one-line semantic_reading for a span."""
    core = _strip_greeting_prefix(span) or span.strip()
    if _APPLY_CTA.search(core) or _APPLY_CTA.search(span):
        return (
            "call_to_action",
            "act_outside_message",
            "sender wants an application submitted through the link; "
            "a reply email is not the application",
        )
    if _QUESTION.search(core):
        return "question", "answer", f"answer the question: {core[:120]}"
    if _is_reply_closing_pleasantry(span):
        return (
            "pleasantry",
            "none",
            "reply-closing courtesy without a required reply action",
        )
    if _REQUEST.search(core):
        return "request", "answer", f"respond to the request: {core[:120]}"
    if _DEADLINE.search(core):
        return "deadline", "acknowledge", f"acknowledge deadline: {core[:120]}"
    if _ATTACHMENT.search(core):
        return "attachment", "acknowledge", f"acknowledge attachment reference: {core[:120]}"
    if _PLEASANTRY.match(core) or (not core.strip() and _PLEASANTRY.match(span.strip())):
        return (
            "pleasantry",
            "none",
            "greeting or courtesy without a required reply action",
        )
    return "statement", "acknowledge", f"acknowledge or reflect: {core[:120]}"


def fallback_obligations(source_message: str) -> list[dict[str, Any]]:
    """Deterministic obligation ledger when the model call fails or returns junk."""
    spans = _split_spans(source_message)
    if not spans and source_message.strip():
        spans = [source_message.strip()]
    rows: list[dict[str, Any]] = []
    for index, span in enumerate(spans, start=1):
        kind, obligation, reading = _classify_span(span)
        rows.append(
            {
                "id": f"O{index}",
                "quoted_span": span,
                "kind": kind,
                "semantic_reading": reading,
                "obligation": obligation,
            }
        )
    return rows


def _parse_model_obligations(raw: str, source_message: str) -> list[dict[str, Any]] | None:
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
    items = parsed.get("obligations") if isinstance(parsed, dict) else parsed
    if not isinstance(items, list):
        return None
    rows: list[dict[str, Any]] = []
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            continue
        quoted = str(item.get("quoted_span") or "")
        if quoted and quoted not in source_message:
            quoted = quoted.strip()
        kind = str(item.get("kind") or "statement")
        obligation = str(item.get("obligation") or "none")
        if quoted and _is_reply_closing_pleasantry(quoted):
            kind = "pleasantry"
            obligation = "none"
        rows.append(
            {
                "id": str(item.get("id") or f"O{index}"),
                "quoted_span": quoted or source_message[:200],
                "kind": kind,
                "semantic_reading": str(item.get("semantic_reading") or ""),
                "obligation": obligation,
            }
        )
    return rows or None


class WritingSourceObligationsHandler(BaseHandler):
    """Read ``intake.source_message`` and emit a numbered obligation ledger."""

    step_type = "writing_source_obligations_v1"

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
        source_message = str(intake.get("source_message") or "").strip()
        if not source_message:
            return _step(
                {
                    "ok": True,
                    "obligations": [],
                    "source_obligations_block": "",
                }
            )
        template = _prompt_template()
        prompt = PromptBuilder().render(
            template,
            {"source_message": _fence(source_message)},
        )
        models = load_writing_models()
        model_id = models.get("prompt_architect") or ""
        obligations: list[dict[str, Any]]
        fallback_used = False
        try:
            if self._caller is not None:
                content = await self._caller()
            else:
                result = await self._call_model(
                    model_id,
                    prompt,
                    step,
                    context,
                    temperature=0.1,
                )
                content = result.content
            parsed = _parse_model_obligations(
                content if isinstance(content, str) else str(content or ""),
                source_message,
            )
            if parsed is None:
                obligations = fallback_obligations(source_message)
                fallback_used = True
            else:
                obligations = parsed
        except Exception:  # noqa: BLE001
            obligations = fallback_obligations(source_message)
            fallback_used = True
        block = _format_block(obligations)
        return _step(
            {
                "ok": True,
                "obligations": obligations,
                "source_obligations_block": block,
                "fallback_used": fallback_used,
            }
        )
