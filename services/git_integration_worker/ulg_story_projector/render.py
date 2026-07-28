"""Plain-English sentence rendering for ULG story wire events."""

from __future__ import annotations

from typing import Any

from systems.frontier_consult.story_wire import (
    ASKED_BY_UNRESOLVED,
    PURPOSE_UNSTATED,
)

from .allowlist import SignalMapping, StoryClass, mapping_for

_MISSING_ASKED_BY = "asked-by not recorded"


def _humanize_agent(agent: str | None) -> str:
    if not agent or not agent.strip():
        return _MISSING_ASKED_BY
    value = agent.strip()
    if value.startswith("(") and "unresolved" in value.lower():
        return _MISSING_ASKED_BY
    known = {
        "web-anthropic": "Claude-web",
        "cursor-auto": "cursor-auto",
    }
    if value in known:
        return known[value]
    if value.startswith("web-"):
        slug = value.removeprefix("web-").replace("-", " ")
        return slug.title()
    return value


def _purpose_text(payload: dict[str, Any]) -> tuple[str, bool]:
    raw = payload.get("purpose")
    if raw is None or not str(raw).strip():
        return PURPOSE_UNSTATED, True
    text = str(raw).strip()
    if text == PURPOSE_UNSTATED:
        return text, True
    return text, False


def _asked_by_text(payload: dict[str, Any]) -> tuple[str, bool]:
    raw = payload.get("asked_by")
    if raw is None or not str(raw).strip():
        return _MISSING_ASKED_BY, True
    text = str(raw).strip()
    if text == ASKED_BY_UNRESOLVED:
        return _MISSING_ASKED_BY, True
    return _humanize_agent(text), False


def _story_id(payload: dict[str, Any]) -> str:
    value = payload.get("story_id")
    return str(value).strip() if value else "-"


def _dispatch_id(payload: dict[str, Any]) -> str:
    for key in ("dispatch_id", "request_id"):
        value = payload.get(key)
        if value:
            return str(value)
    return "-"


def _seat_phrase(asked_by: str) -> str:
    if asked_by == _MISSING_ASKED_BY:
        return f"({asked_by})"
    return f"{asked_by} (operator seat)"


def _effective_class(
    mapping: SignalMapping,
    *,
    purpose_thin: bool,
    asked_by_missing: bool,
) -> StoryClass:
    if purpose_thin or asked_by_missing:
        return "attention"
    return mapping.story_class


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

    purpose, purpose_thin = _purpose_text(payload)
    asked_by, asked_by_missing = _asked_by_text(payload)
    story_class = _effective_class(
        mapping,
        purpose_thin=purpose_thin,
        asked_by_missing=asked_by_missing,
    )
    seat = _seat_phrase(asked_by)
    tail = provenance_tail(seq=seq, payload=payload)

    if signal == "frontier.sdk.closeout.relayed":
        receipt = str(payload.get("receipt_path") or "receipt path not recorded")
        sentence = (
            f"cursor-sdk finished {purpose} for {seat} — receipt at {receipt}."
        )
    elif signal == "frontier.sdk.worker.dispatched":
        thread = str(payload.get("thread_id") or "unknown thread")
        sentence = (
            f"{seat} dispatched cursor-sdk to {purpose} on thread {thread}."
        )
    elif signal == "frontier.sdk.worker.completed":
        duration = payload.get("duration_s")
        duration_bit = f" in {duration}s" if duration is not None else ""
        sentence = (
            f"cursor-sdk {mapping.verb} {purpose} for {seat}{duration_bit}."
        )
    elif signal == "frontier.sdk.worker.failed":
        detail = str(payload.get("error") or payload.get("detail_summary") or "error not recorded")
        sentence = (
            f"Attention: cursor-sdk {mapping.verb} {purpose} for {seat} — {detail}."
        )
    elif signal == "frontier.sdk.worker.timeout":
        timeout_s = payload.get("timeout_s")
        timeout_bit = f" after {timeout_s}s" if timeout_s is not None else ""
        sentence = (
            f"Attention: cursor-sdk {mapping.verb} {purpose} for {seat}{timeout_bit}."
        )
    elif signal == "frontier.sdk.worker.orphaned":
        sentence = (
            f"Attention: cursor-sdk {mapping.verb} {purpose} for {seat}."
        )
    elif signal == "frontier.sdk.auto.auth_gate_blocked":
        thread = str(payload.get("thread_id") or "unknown thread")
        failures = payload.get("failure_count")
        budget = payload.get("budget")
        detail = ""
        if failures is not None and budget is not None:
            detail = f" ({failures}/{budget} failures)"
        sentence = (
            f"Attention: cursor-auto {mapping.verb} {thread}{detail} — "
            f"asked by {asked_by}, purpose {purpose}."
        )
    elif signal == "frontier.sdk.auto.empty_directive_scope_blocked":
        thread = str(payload.get("thread_id") or "unknown thread")
        contract = str(payload.get("contract") or "unknown contract")
        sentence = (
            f"Attention: cursor-auto {mapping.verb} {thread} "
            f"(contract {contract}) — asked by {asked_by}, purpose {purpose}."
        )
    elif signal == "frontier.sdk.auto.thread_status_refused":
        thread = str(payload.get("thread_id") or "unknown thread")
        status = str(payload.get("status") or "unknown status")
        sentence = (
            f"Attention: cursor-auto {mapping.verb} {thread} "
            f"(status {status}) — asked by {asked_by}, purpose {purpose}."
        )
    else:
        return None

    if story_class == "routine" and sentence.startswith("Attention:"):
        sentence = sentence.removeprefix("Attention: ").strip()
        if not sentence.endswith("."):
            sentence += "."
    elif story_class == "attention" and not sentence.startswith("Attention:"):
        sentence = f"Attention: {sentence.rstrip('.')}."

    return f"{sentence} {tail}"
