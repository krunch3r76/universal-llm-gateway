"""Continuity messages envelope — pydantic SoT for ``ulg.continuity.messages/1``."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Literal, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_ID = "ulg.continuity.messages/1"
CORE_KEYS = ("role", "content")
Tools = Literal["none", "marker", "openai"]
TOOL_MARKER_RE = re.compile(r"^\[tool call: [^\]]+\]$", re.MULTILINE)


class ContinuityMessage(BaseModel):
    model_config = ConfigDict(extra="allow")

    role: Literal["system", "user", "assistant", "tool"]
    content: str | None = None


class IndexRow(BaseModel):
    transcript_span: str
    transcript_id: str
    session_id: str
    turn_index: int = Field(ge=1)
    bus_turn_id: int | None = None


class EnvelopeMeta(BaseModel):
    model_config = ConfigDict(extra="allow")

    surface: Literal["cursor", "claude_ai", "grok", "mixed"] = "mixed"
    tools: Tools = "none"
    tools_available: bool = False
    extras: bool = False
    turn_count: int = 0
    message_count: int = 0
    truncated: bool = False
    messages_sha256: str = ""
    budget_bytes: int | None = None
    payload_bytes: int | None = None
    codec_counts: dict[str, int] | None = None
    surfaces: list[str] | None = None
    checkpoint_turns: list[int] | None = None
    sources: list[dict[str, Any]] | None = None
    request: dict[str, Any] | None = None
    door: Literal["sync", "pipeline"] | None = None
    execution_id: str | None = None


class ContinuityMessagesEnvelope(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    schema_: Literal["ulg.continuity.messages/1"] = Field(
        default=SCHEMA_ID,
        alias="schema",
    )
    messages: list[dict[str, Any]] = Field(default_factory=list)
    index: list[dict[str, Any]] = Field(default_factory=list)
    meta: EnvelopeMeta
    open_line: dict[str, Any] | None = None


def strip_extras(msgs: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Project messages to ``CORE_KEYS`` only."""
    return [{k: msg.get(k) for k in CORE_KEYS} for msg in msgs]


def strip_tool_markers(msg: Mapping[str, Any]) -> dict[str, Any]:
    """Remove ``[tool call: NAME]`` paragraphs from assistant content when tools=none."""
    out = dict(msg)
    if out.get("role") != "assistant":
        return out
    content = out.get("content")
    if not isinstance(content, str) or not content:
        return out
    paragraphs = content.split("\n\n")
    kept = [p for p in paragraphs if not TOOL_MARKER_RE.fullmatch(p.strip())]
    out["content"] = "\n\n".join(kept) if kept else None
    return out


def apply_tools_policy(
    msgs: Sequence[Mapping[str, Any]],
    *,
    tools: Tools,
) -> list[dict[str, Any]]:
    if tools != "none":
        return [dict(m) for m in msgs]
    return [strip_tool_markers(m) for m in msgs]


def canonical_messages_bytes(msgs: Sequence[Mapping[str, Any]]) -> bytes:
    """Deterministic JSON bytes for pour idempotency (A3)."""
    canonical = strip_extras(msgs)
    return json.dumps(canonical, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )


def messages_sha256(msgs: Sequence[Mapping[str, Any]]) -> str:
    return hashlib.sha256(canonical_messages_bytes(msgs)).hexdigest()


def envelope_wire_dict(envelope: ContinuityMessagesEnvelope) -> dict[str, Any]:
    """Serialize with ``open_line`` first on the wire."""
    data = envelope.model_dump(mode="json", exclude_none=False, by_alias=True)
    open_line = data.pop("open_line", None)
    ordered: dict[str, Any] = {}
    if open_line is not None:
        ordered["open_line"] = open_line
    ordered.update(data)
    return ordered


__all__ = [
    "SCHEMA_ID",
    "CORE_KEYS",
    "TOOL_MARKER_RE",
    "Tools",
    "ContinuityMessage",
    "IndexRow",
    "EnvelopeMeta",
    "ContinuityMessagesEnvelope",
    "strip_extras",
    "strip_tool_markers",
    "apply_tools_policy",
    "canonical_messages_bytes",
    "messages_sha256",
    "envelope_wire_dict",
]
