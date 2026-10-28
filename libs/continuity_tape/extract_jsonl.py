"""Extract ``ContinuityMessagesEnvelope`` from Cursor agent-transcripts JSONL."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from continuity_tape.events import continuity_messages_extracted
from continuity_tape.messages import (
    ContinuityMessagesEnvelope,
    EnvelopeMeta,
    Tools,
    seal_messages_sha256,
)

_SOURCE = "cursor-jsonl"

OPERATOR_TOOL_BODY_NAMES = frozenset({"SendToUser", "SendToAgent", "Task"})

_OPERATOR_INPUT_KEYS = ("text", "message", "content", "body", "prompt", "description")


def _operator_tool_body(raw: Any) -> str | None:
    """Joined operator-tool input body, or ``None`` when no string fields match."""
    if isinstance(raw, str):
        stripped = raw.strip()
        return stripped or None
    if isinstance(raw, dict):
        parts = [
            v.strip()
            for key in _OPERATOR_INPUT_KEYS
            if isinstance(v := raw.get(key), str) and v.strip()
        ]
        if parts:
            return "\n\n".join(parts)
    return None


def _read_jsonl_bytes(
    raw: bytes, *, path_hint: str = "<bytes>"
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    text = raw.decode("utf-8")
    for line_no, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            records.append(json.loads(stripped))
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"jsonl_path {path_hint} line {line_no}: invalid JSON ({exc})"
            ) from exc
    return records


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return _read_jsonl_bytes(path.read_bytes(), path_hint=str(path))


def _extract_user_text(
    content: list[dict[str, Any]], *, strict_dialogue: bool = False
) -> str:
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            text = block.get("text", "")
            if not isinstance(text, str):
                continue
            if strict_dialogue:
                text = re.sub(
                    r"<system-reminder>[\s\S]*?</system-reminder>",
                    "",
                    text,
                ).strip()
                if text:
                    parts.append(text)
            elif text.strip():
                parts.append(text)
    return "\n\n".join(parts).strip()


def _extract_assistant_blocks(
    content: list[dict[str, Any]],
    *,
    tools: Tools,
    render_operator_tool_bodies: bool = False,
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
            if render_operator_tool_bodies and name in OPERATOR_TOOL_BODY_NAMES:
                body = _operator_tool_body(block.get("input"))
                if body:
                    parts.append(body)
    return "\n\n".join(parts).strip()


def _dialogue_content_blocks(content: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop system, tool_result, and non-text blocks from a message content list."""
    blocks: list[dict[str, Any]] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype in ("system", "tool_result") or block.get("role") == "system":
            continue
        blocks.append(block)
    return blocks


def _normalize_envelope_surface(
    surface: str,
) -> Literal["cursor", "claude_ai", "grok", "mixed"]:
    """Map caller surface tokens to ``EnvelopeMeta.surface`` literals."""
    token = (surface or "cursor").replace("-", "_")
    if token == "claude_ai":
        return "claude_ai"
    if token == "cursor":
        return "cursor"
    if token == "grok":
        return "grok"
    return "mixed"


def _walk_turns_legacy(
    records: list[dict[str, Any]],
    *,
    tools: Tools,
    render_operator_tool_bodies: bool = False,
) -> list[dict[str, str]]:
    """Pre-strict master turn walk (``510e1ca6d`` behavior)."""
    turns: list[dict[str, str]] = []
    current: dict[str, list[str]] | None = None
    for record in records:
        role = record.get("role")
        message = record.get("message") or {}
        content = message.get("content")
        if not isinstance(content, list):
            continue
        if role == "user":
            user_text = _extract_user_text(content, strict_dialogue=False)
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
            asst = _extract_assistant_blocks(
                content,
                tools=tools,
                render_operator_tool_bodies=render_operator_tool_bodies,
            )
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


def _walk_turns(
    records: list[dict[str, Any]],
    *,
    tools: Tools,
    render_operator_tool_bodies: bool = False,
    strict_dialogue: bool = False,
) -> list[dict[str, str]]:
    if not strict_dialogue:
        return _walk_turns_legacy(
            records,
            tools=tools,
            render_operator_tool_bodies=render_operator_tool_bodies,
        )
    turns: list[dict[str, str]] = []
    current: dict[str, list[str]] | None = None
    for record in records:
        message = record.get("message") or {}
        rec_type = record.get("type")
        if bool(record.get("isMeta")) or bool(record.get("isSidechain")):
            continue
        if rec_type in ("system", "tool_result", "queue-operation", "attachment"):
            continue
        role = (
            record.get("role")
            or message.get("role")
            or (rec_type if rec_type in ("user", "assistant") else None)
        )
        if role not in ("user", "assistant"):
            continue
        content = message.get("content")
        if isinstance(content, str):
            content = [{"type": "text", "text": content}]
        if not isinstance(content, list):
            continue
        content = _dialogue_content_blocks(content)
        if not content:
            continue
        if role == "user":
            user_text = _extract_user_text(content, strict_dialogue=True)
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
                current = {"user": [], "assistant": []}
            asst = _extract_assistant_blocks(
                content,
                tools=tools,
                render_operator_tool_bodies=render_operator_tool_bodies,
            )
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
    *,
    omit_empty: bool = False,
) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    for idx, turn in enumerate(turns, start=1):
        user_content = turn.get("user") or None
        asst_content = turn.get("assistant") or None
        if omit_empty:
            if isinstance(user_content, str) and user_content.strip():
                messages.append(
                    {
                        "role": "user",
                        "content": user_content.strip(),
                        "turn_index": idx,
                        "source": _SOURCE,
                    }
                )
            if isinstance(asst_content, str) and asst_content.strip():
                messages.append(
                    {
                        "role": "assistant",
                        "content": asst_content.strip(),
                        "turn_index": idx,
                        "source": _SOURCE,
                    }
                )
            continue
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


def extract_turns_from_jsonl_bytes(
    raw: bytes,
    *,
    tools: Tools = "none",
    transcript_id: str | None = None,
    session_id: str | None = None,
    observed_at: str | None = None,
    surface: str = "cursor",
    strict_dialogue: bool = False,
) -> ContinuityMessagesEnvelope:
    """Parse agent-transcript JSONL bytes into a continuity messages envelope."""
    source_sha256 = hashlib.sha256(raw).hexdigest()
    records = _read_jsonl_bytes(raw)
    turns = _walk_turns(
        records,
        tools=tools,
        strict_dialogue=strict_dialogue,
    )
    messages = _turns_to_messages(turns, omit_empty=strict_dialogue)
    now = observed_at or datetime.now(tz=UTC).isoformat()
    sha = seal_messages_sha256(messages)
    meta_surface = _normalize_envelope_surface(surface)
    meta = EnvelopeMeta(
        surface=meta_surface,
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
        surface=meta_surface,
        transcript_id=transcript_id or "",
        message_count=len(messages),
        turn_count=len(turns),
        user_turns=len(turns),
        tools=tools,
        truncated=False,
        source=_SOURCE,
    )
    return envelope


def extract_turns_from_jsonl(
    path: Path,
    *,
    tools: Tools = "none",
    transcript_id: str | None = None,
    session_id: str | None = None,
    observed_at: str | None = None,
    surface: str = "cursor",
    strict_dialogue: bool = False,
) -> ContinuityMessagesEnvelope:
    """Parse a JSONL transcript file into a continuity messages envelope."""
    return extract_turns_from_jsonl_bytes(
        path.read_bytes(),
        tools=tools,
        transcript_id=transcript_id,
        session_id=session_id,
        observed_at=observed_at,
        surface=surface,
        strict_dialogue=strict_dialogue,
    )


__all__ = ["extract_turns_from_jsonl"]
