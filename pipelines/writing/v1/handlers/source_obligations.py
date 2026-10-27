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
_REPLY_CLOSING_LET_KNOW = re.compile(
    r"^(?:please\s+)?let\s+(?:me|us)\s+know\s*$",
    re.IGNORECASE,
)
_ACK_SINGLE_WORDS = frozenset({
    "noted", "agreed", "understood", "received", "approved", "confirmed",
    "acknowledged", "done", "sure", "perfect", "great", "awesome", "copy", "copied",
})
_NON_SIGNATURE_TOKENS = frozenset({
    "today", "tomorrow", "tonight", "asap", "eod", "eow", "now", "soon", "later",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    "mon", "tue", "tues", "wed", "thu", "thur", "thurs", "fri", "sat", "sun",
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december",
    "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec",
    "yes", "no", "ok", "okay",
    "i", "a",
    "sounds", "got", "it",
    "call", "me", "thing", "do", "all", "set",
}) | _ACK_SINGLE_WORDS
_NAME_WORD = r"[A-Z][\w'.-]*"
_NAME_ONE_OR_TWO = rf"{_NAME_WORD}(?:[ \t]+{_NAME_WORD})?"
_TRAILING_SIGNATURE = re.compile(
    rf"(?:^|\s)[—–-]+\s*({_NAME_ONE_OR_TWO}|"
    rf"(?i:thanks?),?\s*{_NAME_ONE_OR_TWO}|"
    rf"{_NAME_ONE_OR_TWO},?\s*(?i:thank\s+you|thanks?)[!.]?)\s*$",
)
_COURTESY_TOKENS = re.compile(
    r"\b(?:"
    r"thank\s+you(?:\s+so\s+much|\s+very\s+much)?"
    r"|thanks?(?:\s+so\s+much|\s+very\s+much|\s+a\s+lot|\s+again)?"
    r"|please|many\s+thanks|appreciate\s+it"
    r")\b",
    re.IGNORECASE,
)
_EDGE_PUNCT = re.compile(r"^[,!.;:\s—–-]+|[,!.;:\s—–-]+$")
_GREETING_PREFIX = re.compile(
    r"^(?:"
    r"hi(?:\s+[\w'.-]+)?\s*[,—–-]\s*|"
    r"hello\s*[,—–-]\s*|"
    r"thank\s+you\s*,?\s*|"
    r"thanks?\b\s*,?\s*"
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


_SIGNATURE_ONLY = re.compile(
    rf"^[—–-]+\s*({_NAME_ONE_OR_TWO}|"
    rf"(?i:thanks?),?\s*{_NAME_ONE_OR_TWO}|"
    rf"{_NAME_ONE_OR_TWO},?\s*(?i:thank\s+you|thanks?)[!.]?)\s*$",
)
_COMMA_COURTESY_SIGNOFF = re.compile(
    rf"^(?i:thanks?|thank\s+you)\s*,?\s*({_NAME_ONE_OR_TWO})[!.]?\s*$",
)
_ACK_TWO_WORD_PHRASES = frozenset({
    "sounds good", "looks good", "all good", "got it", "will do", "on it",
    "all set", "sure thing", "makes sense", "works great",
})
_ACK_TWO_WORD_PREFIXES = frozenset({
    "looks", "sounds", "great", "perfect", "works", "makes", "all",
})
_ACK_CALL_ACTION = re.compile(r"^call\s+(?:me|[A-Z][\w'.-]*)$", re.IGNORECASE)


def _is_acknowledgement_tail(text: str) -> bool:
    """True when ``text`` is a 1–2 word acknowledgement or action reply, not a sign-off name."""
    stripped = _EDGE_PUNCT.sub("", text.strip()).strip()
    if not stripped:
        return False
    collapsed = " ".join(stripped.split())
    lower = collapsed.lower()
    if " " not in collapsed:
        return lower in _ACK_SINGLE_WORDS
    if lower in _ACK_TWO_WORD_PHRASES:
        return True
    words = lower.split()
    if len(words) == 2 and words[0] in _ACK_TWO_WORD_PREFIXES:
        return True
    return bool(_ACK_CALL_ACTION.match(collapsed))


def _name_tokens(text: str) -> list[str]:
    """Split a 1–2 word capitalized name span into normalized lookup tokens."""
    return [
        part.rstrip("'.-").lower()
        for part in re.split(r"[ \t]+", text.strip())
        if part.strip()
    ]


def _token_is_plausible_signature_name(token: str) -> bool:
    """True when ``token`` looks like a sign-off name, not a keyword or courtesy word."""
    normalized = token.rstrip("'.-").lower()
    if len(normalized) <= 1:
        return False
    if normalized in _NON_SIGNATURE_TOKENS:
        return False
    if (
        _REQUEST.search(token)
        or _DEADLINE.search(token)
        or _ATTACHMENT.search(token)
    ):
        return False
    return True


def _name_tokens_are_signature(text: str) -> bool:
    """True when every token in ``text`` is a plausible sign-off name, not temporal noise."""
    tokens = _name_tokens(text)
    if not tokens or len(tokens) > 2:
        return False
    parts = [part for part in re.split(r"[ \t]+", text.strip()) if part.strip()]
    for part in parts:
        bare = part.rstrip(".!")
        if not re.match(r"^[A-Z][\w'.-]*$", bare):
            return False
        if not _token_is_plausible_signature_name(part):
            return False
    return True


def _signature_fragment_is_name(fragment: str) -> bool:
    """True when a dash-signature capture group is a name or courtesy+name tail."""
    text = fragment.strip()
    if re.match(r"(?i)^thanks?\s*,\s*", text):
        name_part = re.sub(r"(?i)^thanks?\s*,\s*", "", text).strip()
        if _is_acknowledgement_tail(name_part):
            return False
        return _name_tokens_are_signature(name_part)
    if re.match(r"(?i)^thank\s+you\s*,\s*", text):
        name_part = re.sub(r"(?i)^thank\s+you\s*,\s*", "", text).strip()
        if _is_acknowledgement_tail(name_part):
            return False
        return _name_tokens_are_signature(name_part)
    if re.search(r"(?i)(?:thank\s+you|thanks?)[!.]?\s*$", text):
        name_part = re.sub(
            r"(?i),?\s*(?:thank\s+you|thanks?)[!.]?\s*$", "", text
        ).strip()
        if _is_acknowledgement_tail(name_part):
            return False
        return _name_tokens_are_signature(name_part)
    if _is_acknowledgement_tail(text):
        return False
    return _name_tokens_are_signature(text)


def _strip_trailing_signature(text: str) -> str:
    """Remove a trailing dash name-signature when tokens are not temporal/urgency."""
    match = _TRAILING_SIGNATURE.search(text)
    if not match:
        return text
    if not _signature_fragment_is_name(match.group(1)):
        return text
    return text[: match.start()].rstrip()


def _is_comma_courtesy_signoff(span: str) -> bool:
    """True when ``span`` is solely ``Thanks, Name`` / ``Thank you, Name`` (one line)."""
    normalized = " ".join(span.split())
    match = _COMMA_COURTESY_SIGNOFF.match(normalized)
    if not match:
        return False
    tail = match.group(1)
    if _is_acknowledgement_tail(tail):
        return False
    return _name_tokens_are_signature(tail)


def _is_dash_courtesy_signoff(span: str) -> bool:
    """True when ``span`` is a dash lead-in followed only by courtesy tokens."""
    match = re.match(r"^[—–-]+\s*(.+)$", span.strip())
    if not match:
        return False
    remainder = match.group(1).strip()
    if _signature_fragment_is_name(remainder):
        return False
    text = _COURTESY_TOKENS.sub("", remainder).strip()
    text = _EDGE_PUNCT.sub("", text).strip()
    return not text


def _fragment_is_signature_only(fragment: str) -> bool:
    """True when a post-``?`` tail fragment is only dash-signature or courtesy+name."""
    frag = fragment.strip()
    if not frag:
        return True
    sig = _SIGNATURE_ONLY.match(frag)
    if sig and _signature_fragment_is_name(sig.group(1)):
        return True
    dash = re.match(r"^[—–-]+\s*(.+)$", frag)
    if dash:
        candidate = f"- {dash.group(1).strip()}"
        sig = _SIGNATURE_ONLY.match(candidate)
        if sig and _signature_fragment_is_name(sig.group(1)):
            return True
    return _is_comma_courtesy_signoff(frag)


def _strip_trailing_signature_and_courtesy(text: str) -> str:
    """Strip trailing signatures and courtesy so a question ``?`` can sit at the end."""
    out = _strip_trailing_signature(text.strip())
    if "?" not in out:
        return out
    q_idx = out.rfind("?")
    head = out[: q_idx + 1]
    tail = out[q_idx + 1 :].strip()
    while tail:
        prev = tail
        tail = _strip_trailing_signature(tail).strip()
        tail = _COURTESY_TOKENS.sub("", tail).strip()
        if _fragment_is_signature_only(tail):
            tail = ""
            continue
        dash_tail = re.match(r"^[—–-]+\s*(.+)$", tail)
        if dash_tail:
            fragment = dash_tail.group(1).strip()
            if _fragment_is_signature_only(f"- {fragment}"):
                tail = ""
                continue
            fragment = _COURTESY_TOKENS.sub("", fragment).strip()
            if not fragment or _fragment_is_signature_only(f"- {fragment}"):
                tail = ""
                continue
        tail = _EDGE_PUNCT.sub("", tail).strip()
        if tail == prev:
            break
    return head if not tail else f"{head} {tail}".strip()


def _is_direct_request(core: str) -> bool:
    """True when ``core`` is a direct ask, not third-party future narration (``Bob will send``)."""
    if not _REQUEST.search(core):
        return False
    if re.match(
        r"^[A-Z][\w'.-]*(?:\s+[A-Z][\w'.-]*)?\s+will\s+\S",
        core.strip(),
    ):
        return False
    return True


def _is_substantive_question_tail(tail: str) -> bool:
    """True when text after ``?`` is substantive (e.g. ``- call me``), not a signature."""
    stripped = tail.strip()
    if not stripped or _fragment_is_signature_only(stripped):
        return False
    dash = re.match(r"^[—–-]+\s*(.+)$", stripped)
    if dash:
        return not _signature_fragment_is_name(dash.group(1).strip())
    return True


def _question_semantic_core(core: str) -> str:
    """Core text for question ``semantic_reading``, preserving non-signature post-``?`` tails."""
    q_idx = core.rfind("?")
    if q_idx != -1 and _is_substantive_question_tail(core[q_idx + 1 :]):
        return core.strip()
    return _strip_trailing_signature_and_courtesy(core)


def _span_is_question(core: str) -> bool:
    """True when ``core`` is a question, ignoring trailing signature or courtesy."""
    if _QUESTION.search(_strip_trailing_signature_and_courtesy(core)):
        return True
    q_idx = core.rfind("?")
    if q_idx == -1:
        return False
    tail = core[q_idx + 1 :].strip()
    return bool(tail) and _is_substantive_question_tail(tail)


def _span_starts_with_bullet(line: str) -> bool:
    stripped = line.lstrip()
    if stripped.startswith(("-", "–", "—")):
        return True
    return bool(re.match(r"^[-*•]\s+", stripped))


def _is_trailing_signature_span(span: str, spans: list[str], index: int) -> bool:
    """True when ``span`` is a final-line name signature, not a list item."""
    stripped = span.strip()
    if _is_comma_courtesy_signoff(stripped):
        return index == len(spans)
    if _is_dash_courtesy_signoff(stripped):
        if index != len(spans):
            return False
        if index <= 1:
            return True
        prev = spans[index - 2].strip()
        if prev.endswith(":"):
            return False
        return not _span_starts_with_bullet(prev)
    sig = _SIGNATURE_ONLY.match(stripped)
    if not sig or not _signature_fragment_is_name(sig.group(1)):
        return False
    if index != len(spans):
        return False
    if index <= 1:
        return True
    prev = spans[index - 2].strip()
    if prev.endswith(":"):
        return False
    return not _span_starts_with_bullet(prev)


def _quoted_is_trailing_signature_in_message(quoted: str, source_message: str) -> bool:
    """True when ``quoted`` is a trailing dash signature in ``source_message``."""
    quoted_stripped = quoted.strip()
    src = source_message.strip()
    if not quoted_stripped or not src.endswith(quoted_stripped):
        return False
    if _is_comma_courtesy_signoff(quoted_stripped):
        return True
    sig = _SIGNATURE_ONLY.match(quoted_stripped)
    if not sig:
        return False
    fragment = sig.group(1).strip()
    if not _signature_fragment_is_name(fragment):
        return False
    prefix = src[: src.rfind(quoted_stripped)].rstrip()
    if not prefix:
        return True
    prev_line = prefix.splitlines()[-1].strip()
    if prev_line.endswith(":"):
        return False
    return not _span_starts_with_bullet(prev_line)


def _split_spans(source_message: str) -> list[str]:
    chunks = re.split(r"(?<=[.!?])\s+|\n+", source_message.strip())
    spans = [chunk.strip() for chunk in chunks if chunk.strip()]
    merged: list[str] = []
    for span in spans:
        sig_match = _SIGNATURE_ONLY.match(span)
        fragment = sig_match.group(1).strip() if sig_match else ""
        if (
            merged
            and sig_match
            and _signature_fragment_is_name(fragment)
            and not merged[-1].rstrip().endswith(":")
            and not merged[-1].lstrip().startswith(("-", "–", "—"))
        ):
            merged[-1] = f"{merged[-1]} {span}".strip()
            continue
        dash_tail = re.match(r"^[—–-]+\s*(.+)$", span)
        if (
            merged
            and dash_tail
            and not _signature_fragment_is_name(dash_tail.group(1).strip())
            and merged[-1].rstrip().endswith("?")
        ):
            merged[-1] = f"{merged[-1]} {span}".strip()
            continue
        merged.append(span)
    return merged


def _strip_greeting_prefix(span: str) -> str:
    text = span.strip()
    if _is_comma_courtesy_signoff(text):
        return text
    while text:
        match = _GREETING_PREFIX.match(text)
        if not match:
            break
        text = text[match.end() :].strip()
    return text


def _is_courtesy_only_span(core: str) -> bool:
    """True when ``core`` is only courtesy tokens, edge punctuation, or a dash lead-in."""
    stripped_core = core.strip()
    if _SIGNATURE_ONLY.match(stripped_core):
        return False
    text = _strip_trailing_signature(stripped_core).strip()
    while True:
        stripped = _COURTESY_TOKENS.sub("", text).strip()
        if stripped == text:
            break
        text = stripped
    while True:
        stripped = _EDGE_PUNCT.sub("", text).strip()
        if stripped == text:
            break
        text = stripped
    return not text


def _is_reply_closing_pleasantry(span: str) -> bool:
    """True when span is only a bare let-me/us-know sign-off without substantive ask."""
    text = _strip_greeting_prefix(span).strip()
    text = _strip_trailing_signature(text).strip()
    while True:
        stripped = _COURTESY_TOKENS.sub("", text).strip()
        if stripped == text:
            break
        text = stripped
    while True:
        stripped = _EDGE_PUNCT.sub("", text).strip()
        if stripped == text:
            break
        text = stripped
    if not text:
        return False
    return bool(_REPLY_CLOSING_LET_KNOW.match(text))


def _classify_span(span: str) -> tuple[str, str, str]:
    """Return kind, obligation, and one-line semantic_reading for a span."""
    stripped = span.strip()
    if _is_comma_courtesy_signoff(stripped) or _is_dash_courtesy_signoff(stripped):
        return (
            "pleasantry",
            "none",
            "greeting or courtesy without a required reply action",
        )
    core = _strip_greeting_prefix(span) or stripped
    if _APPLY_CTA.search(core) or _APPLY_CTA.search(span):
        return (
            "call_to_action",
            "act_outside_message",
            "sender wants an application submitted through the link; "
            "a reply email is not the application",
        )
    if _span_is_question(core):
        question_core = _question_semantic_core(core)
        return "question", "answer", f"answer the question: {question_core[:120]}"
    if _is_reply_closing_pleasantry(span):
        return (
            "pleasantry",
            "none",
            "reply-closing courtesy without a required reply action",
        )
    if _is_direct_request(core):
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
    if _is_courtesy_only_span(core):
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
        if _is_trailing_signature_span(span, spans, index):
            kind, obligation, reading = (
                "pleasantry",
                "none",
                "greeting or courtesy without a required reply action",
            )
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
        quoted_core = _strip_greeting_prefix(quoted) or quoted.strip()
        if (
            quoted
            and _span_is_question(quoted_core)
            and (kind == "statement" or obligation == "acknowledge")
            and obligation != "act_outside_message"
            and kind != "call_to_action"
            and not _APPLY_CTA.search(quoted_core)
            and not _APPLY_CTA.search(quoted)
        ):
            kind = "question"
            obligation = "answer"
        elif quoted and _is_reply_closing_pleasantry(quoted):
            kind = "pleasantry"
            obligation = "none"
        elif quoted and _quoted_is_trailing_signature_in_message(quoted, source_message):
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
