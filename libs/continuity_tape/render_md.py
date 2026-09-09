"""Render verbatim markdown from a ``ContinuityMessagesEnvelope``."""

from __future__ import annotations

from continuity_tape.messages import ContinuityMessagesEnvelope

_DEFAULT_ASSISTANT_LABEL = "Assistant"
_TURN_TOPIC_MAX = 60
_EMPTY_USER = "(empty)"
_EMPTY_ASSISTANT = "(no assistant output)"


def _topic_hint(user_text: str) -> str:
    flat = " ".join(user_text.split())
    if len(flat) <= _TURN_TOPIC_MAX:
        return flat or "(no user text)"
    return flat[:_TURN_TOPIC_MAX].rstrip() + "…"


def _turns_from_envelope(
    envelope: ContinuityMessagesEnvelope,
) -> list[dict[str, str | None]]:
    by_turn: dict[int, dict[str, str | None]] = {}
    for msg in envelope.messages:
        turn_index = int(msg.get("turn_index") or 0)
        if turn_index <= 0:
            continue
        slot = by_turn.setdefault(turn_index, {"user": None, "assistant": None})
        role = msg.get("role")
        content = msg.get("content")
        if role == "user":
            slot["user"] = content if isinstance(content, str) else None
        elif role == "assistant":
            slot["assistant"] = content if isinstance(content, str) else None
    return [by_turn[i] for i in sorted(by_turn)]


def render_verbatim_md(
    envelope: ContinuityMessagesEnvelope,
    session_id: str,
    assistant_label: str | None = None,
) -> tuple[str, int]:
    """Build the verbatim layer from a sealed or live envelope.

    Returns ``(verbatim_md, turn_count)``. Sentinels ``(empty)`` /
    ``(no assistant output)`` appear only in derived md; seal JSON uses
    ``null`` for missing halves.
    """
    label = assistant_label or _DEFAULT_ASSISTANT_LABEL
    turns = _turns_from_envelope(envelope)
    lines: list[str] = [f"# Transcript: {session_id}", ""]
    for idx, turn in enumerate(turns, start=1):
        user_text = turn["user"] or ""
        topic = _topic_hint(user_text)
        lines.append(f"## Turn {idx} — {topic}")
        lines.append("")
        lines.append("### User")
        lines.append("")
        lines.append(user_text or _EMPTY_USER)
        lines.append("")
        lines.append(f"### {label}")
        lines.append("")
        lines.append((turn["assistant"] or "") or _EMPTY_ASSISTANT)
        lines.append("")
    return "\n".join(lines), len(turns)


__all__ = ["render_verbatim_md"]
