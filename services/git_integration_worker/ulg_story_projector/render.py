"""Plain-English sentence rendering for ULG story wire events."""

from __future__ import annotations

from typing import Any, Literal

from systems.frontier_consult.story_wire import (
    ASKED_BY_UNRESOLVED,
    PURPOSE_UNSTATED,
)

from .allowlist import SignalMapping, StoryClass, mapping_for

EnvelopeMode = Literal["pre_envelope", "caller_omitted", "full"]
_ENVELOPE_KEYS = ("purpose", "asked_by", "story_id")

# Bind 3 render-time length discipline — presentation only; payload keeps full text.
PURPOSE_RENDER_MAX = 80

_DANGLING_PURPOSE_TAIL = frozenset(
    {
        "a",
        "an",
        "and",
        "as",
        "at",
        "but",
        "by",
        "for",
        "if",
        "in",
        "into",
        "must",
        "not",
        "of",
        "on",
        "or",
        "so",
        "that",
        "the",
        "to",
        "when",
        "while",
        "with",
    },
)


def _humanize_agent(agent: str) -> str:
    known = {
        "web-anthropic": "Claude-web",
        "cursor-auto": "cursor-auto",
    }
    if agent in known:
        return known[agent]
    if agent.startswith("web-"):
        slug = agent.removeprefix("web-").replace("-", " ")
        return slug.title()
    return agent


def envelope_mode(payload: dict[str, Any]) -> EnvelopeMode:
    """Distinguish pre-envelope (keys absent) from caller-omitted (sentinels)."""
    if not all(key in payload for key in _ENVELOPE_KEYS):
        return "pre_envelope"
    purpose = str(payload.get("purpose") or "").strip()
    asked_by = str(payload.get("asked_by") or "").strip()
    if not purpose or purpose == PURPOSE_UNSTATED:
        return "caller_omitted"
    if (
        not asked_by
        or asked_by == ASKED_BY_UNRESOLVED
        or asked_by.startswith("(unresolved")
    ):
        return "caller_omitted"
    return "full"


def _clean_truncated_purpose(text: str) -> str:
    """Drop broken tail punctuation and dangling words after a cut."""
    cleaned = text.rstrip(" ,;:")
    if cleaned.count("`") % 2 == 1:
        idx = cleaned.rfind("`")
        if idx > 0:
            cleaned = cleaned[:idx].rstrip(" ,;:")
    if cleaned.count('"') % 2 == 1:
        idx = cleaned.rfind('"')
        if idx > 0:
            cleaned = cleaned[:idx].rstrip(" ,;:")

    words = cleaned.split()
    while len(words) > 1 and words[-1].lower().rstrip(".,;:`\"") in _DANGLING_PURPOSE_TAIL:
        words.pop()
    return " ".join(words).rstrip(".,;:")


def truncate_purpose_for_render(
    purpose: str,
    *,
    max_chars: int = PURPOSE_RENDER_MAX,
) -> str:
    """Cap purpose at render time; word/sentence boundary, no ellipsis."""
    text = purpose.strip()
    if len(text) <= max_chars:
        return text

    window = text[:max_chars]
    cutoff_floor = max(max_chars // 3, 20)
    for marker in (". ", "! ", "? ", "; "):
        idx = window.rfind(marker)
        if idx >= cutoff_floor:
            candidate = _clean_truncated_purpose(text[: idx + len(marker.rstrip())])
            if candidate:
                return candidate

    last_space = window.rfind(" ")
    if last_space > 0:
        candidate = _clean_truncated_purpose(text[:last_space])
    else:
        candidate = _clean_truncated_purpose(window)

    return candidate or text[:max_chars].rstrip()


def _purpose_clause(payload: dict[str, Any], mode: EnvelopeMode) -> str:
    if mode == "pre_envelope":
        return "a task"
    purpose = str(payload.get("purpose") or "").strip()
    if mode == "caller_omitted" or purpose == PURPOSE_UNSTATED or not purpose:
        return "a task (purpose not stated)"
    return truncate_purpose_for_render(purpose)


def _seat_clause(payload: dict[str, Any], mode: EnvelopeMode) -> str:
    if mode == "pre_envelope":
        return "an operator seat"
    asked_by = str(payload.get("asked_by") or "").strip()
    if (
        mode == "caller_omitted"
        or not asked_by
        or asked_by == ASKED_BY_UNRESOLVED
        or asked_by.startswith("(unresolved")
    ):
        return "an operator seat (asked-by not recorded)"
    return f"{_humanize_agent(asked_by)} (operator seat)"


def _seat_subject(payload: dict[str, Any], mode: EnvelopeMode) -> str:
    """Seat phrase when it leads the sentence."""
    clause = _seat_clause(payload, mode)
    if clause.startswith("an "):
        return "An" + clause[2:]
    return clause


def _story_id(payload: dict[str, Any]) -> str:
    value = payload.get("story_id")
    return str(value).strip() if value else "-"


def _dispatch_id(payload: dict[str, Any]) -> str:
    for key in ("dispatch_id", "request_id"):
        value = payload.get(key)
        if value:
            return str(value)
    return "-"


def _format_duration(duration_s: Any) -> str:
    if duration_s is None:
        return ""
    try:
        val = float(duration_s)
    except (TypeError, ValueError):
        return f" in {duration_s}s"
    if val >= 100:
        rounded = round(val)
    else:
        rounded = round(val, 1)
    if rounded == int(rounded):
        return f" in {int(rounded)}s"
    return f" in {rounded}s"


def _effective_class(mapping: SignalMapping, mode: EnvelopeMode) -> StoryClass:
    if mapping.story_class == "attention":
        return "attention"
    if mode == "caller_omitted":
        return "attention"
    if mode == "pre_envelope":
        return "routine"
    return mapping.story_class


def _apply_class_prefix(sentence: str, story_class: StoryClass) -> str:
    body = sentence.removeprefix("Attention: ").strip()
    if story_class == "attention" and not sentence.startswith("Attention:"):
        return f"Attention: {body.rstrip('.')}."
    if story_class != "attention" and sentence.startswith("Attention:"):
        body = body.rstrip(".")
        return f"{body}."
    if not body.endswith("."):
        return f"{body}."
    return body


def provenance_tail(
    *,
    seq: int,
    payload: dict[str, Any],
) -> str:
    return (
        f"[seq:{seq} story:{_story_id(payload)} dispatch:{_dispatch_id(payload)}]"
    )


def render_gap_line(
    *,
    seq: int,
    lost_from_seq: int,
    lost_through_seq: int,
    wall_from: str,
    wall_through: str,
) -> str:
    body = (
        f"Attention: retention gap — events seq {lost_from_seq}–{lost_through_seq} "
        f"({wall_from} to {wall_through}) fell off the seven-day window before "
        "the projector could catch up."
    )
    return f"{body} [seq:{seq} story:- dispatch:-]"


def render_parse_failure(
    *,
    seq: int,
    signal: str,
    reason: str,
) -> str:
    return (
        f"Attention: could not render {signal} at seq {seq} "
        f"({reason}); line skipped. [seq:{seq} story:- dispatch:-]"
    )


def render_event_line(
    *,
    seq: int,
    signal: str,
    payload: dict[str, Any],
) -> str | None:
    mapping = mapping_for(signal)
    if mapping is None:
        return None

    mode = envelope_mode(payload)
    story_class = _effective_class(mapping, mode)
    purpose = _purpose_clause(payload, mode)
    seat = _seat_clause(payload, mode)
    tail = provenance_tail(seq=seq, payload=payload)

    if signal == "frontier.sdk.closeout.relayed":
        receipt = str(payload.get("receipt_path") or "receipt path not recorded")
        sentence = f"cursor-sdk finished {purpose} for {seat} — receipt at {receipt}."
    elif signal == "frontier.sdk.worker.dispatched":
        thread = str(payload.get("thread_id") or "unknown thread")
        seat_subject = _seat_subject(payload, mode)
        if mode == "pre_envelope":
            sentence = f"{seat_subject} dispatched cursor-sdk on thread {thread}."
        elif mode == "caller_omitted":
            sentence = (
                f"{seat} dispatched cursor-sdk {purpose} on thread {thread}."
            )
        else:
            sentence = f"{seat} dispatched cursor-sdk to {purpose} on thread {thread}."
    elif signal == "frontier.sdk.worker.completed":
        duration_bit = _format_duration(payload.get("duration_s"))
        sentence = f"cursor-sdk {mapping.verb} {purpose} for {seat}{duration_bit}."
    elif signal == "frontier.sdk.worker.failed":
        detail = str(
            payload.get("error")
            or payload.get("detail_summary")
            or "error not recorded",
        )
        sentence = f"cursor-sdk {mapping.verb} {purpose} for {seat} — {detail}."
    elif signal == "frontier.sdk.worker.timeout":
        timeout_bit = _format_duration(payload.get("timeout_s"))
        sentence = f"cursor-sdk {mapping.verb} {purpose} for {seat}{timeout_bit}."
    elif signal == "frontier.sdk.worker.orphaned":
        sentence = f"cursor-sdk {mapping.verb} {purpose} for {seat}."
    elif signal == "frontier.sdk.auto.auth_gate_blocked":
        thread = str(payload.get("thread_id") or "unknown thread")
        failures = payload.get("failure_count")
        budget = payload.get("budget")
        detail = ""
        if failures is not None and budget is not None:
            detail = f" ({failures}/{budget} failures)"
        if mode == "pre_envelope":
            sentence = f"cursor-auto {mapping.verb} {thread}{detail}."
        else:
            sentence = (
                f"cursor-auto {mapping.verb} {thread}{detail} — "
                f"asked by {seat}, {purpose}."
            )
    elif signal == "frontier.sdk.auto.empty_directive_scope_blocked":
        thread = str(payload.get("thread_id") or "unknown thread")
        contract = str(payload.get("contract") or "unknown contract")
        if mode == "pre_envelope":
            sentence = (
                f"cursor-auto {mapping.verb} {thread} (contract {contract})."
            )
        else:
            sentence = (
                f"cursor-auto {mapping.verb} {thread} (contract {contract}) — "
                f"asked by {seat}, {purpose}."
            )
    elif signal == "frontier.sdk.auto.thread_status_refused":
        thread = str(payload.get("thread_id") or "unknown thread")
        status = str(payload.get("status") or "unknown status")
        if mode == "pre_envelope":
            sentence = f"cursor-auto {mapping.verb} {thread} (status {status})."
        else:
            sentence = (
                f"cursor-auto {mapping.verb} {thread} (status {status}) — "
                f"asked by {seat}, {purpose}."
            )
    else:
        return None

    sentence = _apply_class_prefix(sentence, story_class)
    return f"{sentence} {tail}"
