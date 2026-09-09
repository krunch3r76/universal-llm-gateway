"""Extract ``ContinuityMessagesEnvelope`` from Cursor agent-transcripts JSONL."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from continuity_tape.events import continuity_messages_extracted
from continuity_tape.messages import (
    ContinuityMessagesEnvelope,
    EnvelopeMeta,
    Tools,
    seal_messages_sha256,
)

_SOURCE = "cursor-jsonl"


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                records.append(json.loads(stripped))
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"jsonl_path {path} line {line_no}: invalid JSON ({exc})"
                ) from exc
    return records


def _extract_user_text(content: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            text = block.get("text", "")
            if isinstance(text, str) and text.strip():
                parts.append(text)
    return "\n\n".join(parts).strip()


def _extract_assistant_blocks(
    content: list[dict[str, Any]],
    *,
    tools: Tools,
) -> str:
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            text = block.get("text", "")
            if isinstance(text, str) and text.strip():
                parts.append(text)
        elif btype == "tool_use" and tools == "marker":
            name = block.get("name", "<unknown>")
            parts.append(f"[tool call: {name}]")
    return "\n\n".join(parts).strip()


def _walk_turns(
    records: list[dict[str, Any]],
    *,
    tools: Tools,
) -> list[dict[str, str]]:
    turns: list[dict[str, str]] = []
    current: dict[str, list[str]] | None = None
    for record in records:
        role = record.get("role")
        message = record.get("message") or {}
        content = message.get("content")
        if not isinstance(content, list):
            continue
        if role == "user":
            user_text = _extract_user_text(content)
            if not user_text:
                continue
            if current is not None:
                turns.append(
                    {
                        "user": "\n\n".join(current["user"]).strip(),
                        "assistant": "\n\n".join(current["assistant"]).strip(),
                    }
                )
            current = {"user": [user_text], "assistant": []}
        elif role == "assistant":
            if current is None:
                current = {"user": ["(no user message)"], "assistant": []}
            asst = _extract_assistant_blocks(content, tools=tools)
            if asst:
                current["assistant"].append(asst)
    if current is not None:
        turns.append(
            {
                "user": "\n\n".join(current["user"]).strip(),
                "assistant": "\n\n".join(current["assistant"]).strip(),
            }
        )
    return turns


def _turns_to_messages(
    turns: list[dict[str, str]],
) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    for idx, turn in enumerate(turns, start=1):
        user_content = turn["user"] or None
        asst_content = turn["assistant"] or None
        messages.append(
            {
                "role": "user",
                "content": user_content,
                "turn_index": idx,
                "source": _SOURCE,
            }
        )
        messages.append(
            {
                "role": "assistant",
                "content": asst_content,
                "turn_index": idx,
                "source": _SOURCE,
            }
        )
    return messages


def extract_turns_from_jsonl(
    path: Path,
    *,
    tools: Tools = "none",
    transcript_id: str | None = None,
    session_id: str | None = None,
    observed_at: str | None = None,
) -> ContinuityMessagesEnvelope:
    """Parse a Cursor JSONL into a continuity messages envelope."""
    raw_bytes = path.read_bytes()
    source_sha256 = hashlib.sha256(raw_bytes).hexdigest()
    records = _read_jsonl(path)
    turns = _walk_turns(records, tools=tools)
    messages = _turns_to_messages(turns)
    now = observed_at or datetime.now(tz=UTC).isoformat()
    sha = seal_messages_sha256(messages)
    meta = EnvelopeMeta(
        surface="cursor",
        tools=tools,
        tools_available=False,
        extras=False,
        turn_count=len(turns),
        message_count=len(messages),
        truncated=False,
        messages_sha256=sha,
        transcript_id=transcript_id,
        session_id=session_id,
        observed_at=now,
        source_sha256=source_sha256,
    )
    envelope = ContinuityMessagesEnvelope(messages=messages, index=[], meta=meta)
    continuity_messages_extracted(
        surface="cursor",
        transcript_id=transcript_id or "",
        message_count=len(messages),
        turn_count=len(turns),
        user_turns=len(turns),
        tools=tools,
        truncated=False,
        source=_SOURCE,
    )
    return envelope


__all__ = ["extract_turns_from_jsonl"]
