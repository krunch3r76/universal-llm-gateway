"""Turn→message mapper and harvest envelope builder for ``ulg.continuity.messages/1``."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Literal

from continuity_tape.messages import (
    ContinuityMessagesEnvelope,
    EnvelopeMeta,
    envelope_wire_dict,
    messages_sha256,
    seal_message_projection,
)
from continuity_tape.seal_reader import load_sealed_envelope_from_path

from chat_harvest.models import ChatTurn

CHROME_REPLY_PREFIXES = ("Claude responded:",)


def normalize_turn_text(text: str) -> str:
    """Strip whitespace, normalize newlines, remove chrome reply prefixes."""
    normalized = (text or "").strip().replace("\r\n", "\n")
    for prefix in CHROME_REPLY_PREFIXES:
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix) :].strip()
            break
    return normalized


def author_to_role(author: str) -> Literal["user", "assistant"]:
    label = (author or "").lower()
    if label in {"user", "human"}:
        return "user"
    return "assistant"


def message_digest(content: str) -> str:
    normalized = normalize_turn_text(content)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _turn_row(turn: ChatTurn | Mapping[str, Any]) -> tuple[str, int, str]:
    if isinstance(turn, ChatTurn):
        return turn.author, turn.ordinal, turn.text
    return (
        str(turn.get("author") or ""),
        int(turn.get("ordinal") or 0),
        str(turn.get("text") or ""),
    )


def collapse_consecutive_duplicates(
    turns: Sequence[ChatTurn | Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Drop empty rows and collapse consecutive same-role preview/body pairs."""
    ordered = sorted(turns, key=lambda t: _turn_row(t)[1])
    out: list[dict[str, Any]] = []
    for turn in ordered:
        author, ordinal, text = _turn_row(turn)
        norm = normalize_turn_text(text)
        if not norm:
            continue
        if out and out[-1]["author"] == author:
            prev_norm = normalize_turn_text(str(out[-1].get("text") or ""))
            if (
                prev_norm == norm
                or prev_norm.endswith(norm)
                or norm.endswith(prev_norm)
            ):
                shorter = norm if len(norm) < len(prev_norm) else prev_norm
                out[-1] = {
                    "author": author,
                    "ordinal": out[-1]["ordinal"],
                    "text": shorter,
                }
                continue
        out.append({"author": author, "ordinal": ordinal, "text": text})
    return out


def turns_to_messages(
    turns: Sequence[ChatTurn | Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Map scrape rows to SEAL_KEYS message rows with ``turn_index``."""
    collapsed = collapse_consecutive_duplicates(turns)
    messages: list[dict[str, Any]] = []
    turn_index = 0
    for row in collapsed:
        role = author_to_role(str(row.get("author") or ""))
        content = normalize_turn_text(str(row.get("text") or ""))
        if role == "user" or turn_index == 0:
            turn_index += 1
        messages.append(
            {"role": role, "content": content, "turn_index": turn_index}
        )
    return messages


def ensure_turn_index(messages: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Project to SEAL_KEYS; assign ``turn_index`` when absent."""
    if all(msg.get("turn_index") is not None for msg in messages):
        return [seal_message_projection(m) for m in messages]
    return turns_to_messages(
        [
            {
                "author": "user" if m.get("role") == "user" else "assistant",
                "ordinal": i + 1,
                "text": str(m.get("content") or ""),
            }
            for i, m in enumerate(messages)
        ]
    )


def message_index(messages: Sequence[Mapping[str, Any]]) -> list[list[object]]:
    """Build alignment rows ``[position, role, digest]`` (1-based position)."""
    return [
        [i + 1, msg.get("role"), message_digest(str(msg.get("content") or ""))]
        for i, msg in enumerate(messages)
    ]


def _surface_for_site(site: str) -> Literal["claude_ai", "grok"]:
    if site == "grok":
        return "grok"
    return "claude_ai"


def build_harvest_envelope(
    *,
    site: str,
    conversation_id: str,
    url: str,
    messages: Sequence[Mapping[str, Any]],
    harvested_at: str,
    streaming: bool,
) -> ContinuityMessagesEnvelope:
    """Build a ``ContinuityMessagesEnvelope`` for chat harvest persist."""
    rows = [seal_message_projection(m) for m in messages]
    turn_count = max((int(m.get("turn_index") or 0) for m in rows), default=0)
    return ContinuityMessagesEnvelope(
        messages=rows,
        meta=EnvelopeMeta(
            surface=_surface_for_site(site),
            tools="none",
            tools_available=False,
            extras=False,
            turn_count=turn_count,
            message_count=len(rows),
            truncated=False,
            messages_sha256=messages_sha256(rows),
            chat_url=url,
            observed_at=harvested_at,
            streaming=streaming,
            coverage="full",
            sources=[
                {
                    "kind": "chat_harvest",
                    "site": site,
                    "conversation_id": conversation_id,
                }
            ],
        ),
        open_line=None,
    )


def load_harvest_envelope(path: Path) -> ContinuityMessagesEnvelope:
    """Load a persisted harvest envelope from disk."""
    return load_sealed_envelope_from_path(path)


def envelope_json_text(envelope: ContinuityMessagesEnvelope) -> str:
    """Serialize envelope to indented JSON with trailing newline."""
    return (
        json.dumps(envelope_wire_dict(envelope), ensure_ascii=False, indent=2) + "\n"
    )


__all__ = [
    "CHROME_REPLY_PREFIXES",
    "author_to_role",
    "build_harvest_envelope",
    "collapse_consecutive_duplicates",
    "ensure_turn_index",
    "envelope_json_text",
    "load_harvest_envelope",
    "message_digest",
    "message_index",
    "normalize_turn_text",
    "turns_to_messages",
]
