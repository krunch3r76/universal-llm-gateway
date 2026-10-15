"""Chunk ``ulg.continuity.messages/1`` seal files for RAG (on-the-fly dialogue projection)."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from continuity_tape.messages import (
    SCHEMA_ID,
    TOOL_MARKER_RE,
    ContinuityMessagesEnvelope,
)
from continuity_tape.notice import is_notice_row, unwrap_operator_utterance

from services.rag.chunkers.paragraph_utils import Chunk

CHUNKER_VERSION = "conversation-messages/1"

DEFAULT_EXCLUDE_LANES: frozenset[str] = frozenset({"16934"})

CASE_CONTENT_MARKERS: tuple[str, ...] = (
    "25.7489",
    "25.7490",
    "424-20-043",
    "boe19p",
    "flintridge",
    "assessment appeals",
)

LaneTags = tuple[str, ...]
LaneResolver = Callable[[ContinuityMessagesEnvelope, Path], tuple[str | None, LaneTags]]

_TOOL_CALL_PARA_RE = re.compile(
    r"^\[tool call: [^\]]+\]$",
    re.MULTILINE,
)
_TOOL_USE_INLINE_RE = re.compile(r"\[tool call: [^\]]+\]")
_REASONING_FENCE_RE = re.compile(
    r"<\s*(?:think|thinking|reasoning)[^>]*>.*?</\s*(?:think|thinking|reasoning)\s*>",
    re.DOTALL | re.IGNORECASE,
)
_JSON_BLOB_RE = re.compile(r"\{[^{}]*\"(?:type|tool_use_id|input)\"[^{}]*\}", re.DOTALL)

_API_KEY_RE = re.compile(
    r"(?i)\b(?:sk-[a-z0-9_-]{10,}|"
    r"Bearer\s+[A-Za-z0-9._\-+/=]{20,}|"
    r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----[\s\S]*?-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)"
)
_JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_PHONE_RE = re.compile(r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b")
_STREET_RE = re.compile(
    r"\b\d{1,5}\s+[A-Za-z0-9.'-]+\s+(?:St|Street|Ave|Avenue|Rd|Road|Blvd|Lane|Dr|Drive)\b",
    re.IGNORECASE,
)
_ENV_DUMP_RE = re.compile(
    r"(?m)^(?:[A-Z][A-Z0-9_]{2,})=(?:[^\n\\]|\\.)+$"
)

_SYNTHETIC_USER_SUBSTRINGS = (
    "briefly inform the user about the task result",
    "liaison ide hop",
)


@dataclass(slots=True, kw_only=True)
class MessagesChunkerConfig:
    exclude_lanes: frozenset[str] = DEFAULT_EXCLUDE_LANES
    exclude_lane_tag_prefixes: tuple[str, ...] = ("case:",)
    lane_resolver: LaneResolver | None = None


@dataclass(slots=True, kw_only=True)
class _TurnPair:
    turn_index: int
    user_text: str
    assistant_text: str
    user_ts: str | None = None


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _source_sha256(envelope: ContinuityMessagesEnvelope, path: Path) -> str:
    meta = envelope.meta
    if meta.messages_sha256:
        return meta.messages_sha256
    if meta.source_sha256:
        return meta.source_sha256
    return _file_sha256(path)


def _chunk_idempotency_key(source_sha: str) -> str:
    return f"{CHUNKER_VERSION}:{source_sha}"


def _lane_tag_excluded(tags: LaneTags, prefixes: Sequence[str]) -> bool:
    for tag in tags:
        lowered = tag.lower()
        for prefix in prefixes:
            if lowered.startswith(prefix.lower()):
                return True
    return False


def transcript_lane_excluded(
    lane_id: str | None,
    lane_tags: LaneTags,
    *,
    exclude_lanes: frozenset[str] = DEFAULT_EXCLUDE_LANES,
    exclude_lane_tag_prefixes: tuple[str, ...] = ("case:",),
) -> bool:
    if lane_id and lane_id in exclude_lanes:
        return True
    return _lane_tag_excluded(lane_tags, exclude_lane_tag_prefixes)


def exchange_matches_case_markers(text: str) -> bool:
    lowered = text.casefold()
    return any(marker in lowered for marker in CASE_CONTENT_MARKERS)


def _is_synthetic_user(content: str) -> bool:
    if is_notice_row("user", content):
        return True
    probe = unwrap_operator_utterance(content) if "<user_query>" in content else content
    folded = probe.casefold()
    return any(sub in folded for sub in _SYNTHETIC_USER_SUBSTRINGS)


def _verbatim_user(content: str) -> str:
    """User text for indexing: envelope content verbatim (a:1627), not paraphrased."""
    return content if isinstance(content, str) else ""


def _normalize_assistant(content: str) -> str:
    if not content:
        return ""
    text = _REASONING_FENCE_RE.sub("", content)
    text = _JSON_BLOB_RE.sub("", text)
    paragraphs = text.split("\n\n")
    kept: list[str] = []
    for para in paragraphs:
        stripped = para.strip()
        if not stripped:
            continue
        if _TOOL_CALL_PARA_RE.fullmatch(stripped):
            continue
        if TOOL_MARKER_RE.fullmatch(stripped):
            continue
        cleaned = _TOOL_USE_INLINE_RE.sub("", stripped).strip()
        if cleaned:
            kept.append(cleaned)
    return "\n\n".join(kept).strip()


def scrub_chunk_text(text: str) -> str:
    """Secret/PII scrub on indexed chunk text only."""
    out = text
    out = _API_KEY_RE.sub("[REDACTED_SECRET]", out)
    out = _JWT_RE.sub("[REDACTED_JWT]", out)
    out = _ENV_DUMP_RE.sub("[REDACTED_ENV]", out)
    out = _EMAIL_RE.sub("[REDACTED_EMAIL]", out)
    out = _PHONE_RE.sub("[REDACTED_PHONE]", out)
    out = _STREET_RE.sub("[REDACTED_ADDRESS]", out)
    return out


def _message_ts(msg: dict[str, Any]) -> str | None:
    for key in ("ts", "timestamp", "created_at"):
        val = msg.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


def _iter_turn_pairs(messages: list[dict[str, Any]]) -> list[_TurnPair]:
    pairs: list[_TurnPair] = []
    pending_user: dict[str, Any] | None = None
    for msg in messages:
        role = msg.get("role")
        content = msg.get("content")
        if role == "tool" or role == "system":
            continue
        if role == "user":
            if not isinstance(content, str) or not content.strip():
                continue
            if _is_synthetic_user(content):
                pending_user = None
                continue
            if pending_user is not None:
                pairs.append(
                    _TurnPair(
                        turn_index=int(pending_user.get("turn_index") or 0),
                        user_text=_verbatim_user(str(pending_user.get("content") or "")),
                        assistant_text="",
                        user_ts=_message_ts(pending_user),
                    )
                )
            pending_user = msg
        elif role == "assistant" and pending_user is not None:
            user_text = _verbatim_user(str(pending_user.get("content") or ""))
            assistant_text = _normalize_assistant(content if isinstance(content, str) else "")
            turn_index = int(pending_user.get("turn_index") or msg.get("turn_index") or 0)
            pairs.append(
                _TurnPair(
                    turn_index=turn_index,
                    user_text=user_text,
                    assistant_text=assistant_text,
                    user_ts=_message_ts(pending_user),
                )
            )
            pending_user = None
    if pending_user is not None:
        pairs.append(
            _TurnPair(
                turn_index=int(pending_user.get("turn_index") or 0),
                user_text=_verbatim_user(str(pending_user.get("content") or "")),
                assistant_text="",
                user_ts=_message_ts(pending_user),
            )
        )
    return pairs


def _pair_exchange_text(pair: _TurnPair) -> str:
    parts = [pair.user_text]
    if pair.assistant_text:
        parts.append(pair.assistant_text)
    return "\n\n".join(p for p in parts if p.strip())


def _resolve_transcript_id(envelope: ContinuityMessagesEnvelope, path: Path) -> str:
    meta = envelope.meta
    if meta.transcript_id:
        return meta.transcript_id
    if meta.session_id:
        return meta.session_id
    stem = path.name
    if stem.endswith(".messages.json"):
        return stem[: -len(".messages.json")]
    return path.stem


def _surface_label(meta_surface: str | None, path: Path) -> str:
    if meta_surface and meta_surface != "mixed":
        return meta_surface
    name = path.name.casefold()
    if name.startswith("cursor-"):
        return "cursor"
    if "grok" in name:
        return "grok"
    if "claude" in name or "anthropic" in name:
        return "claude_ai"
    return meta_surface or "mixed"


def chunk_messages(
    path: str | Path,
    *,
    config: MessagesChunkerConfig | None = None,
) -> list[Chunk]:
    """Chunk one ``*.messages.json`` continuity envelope into user+assistant exchanges."""
    cfg = config or MessagesChunkerConfig()
    file_path = Path(path)
    raw = json.loads(file_path.read_text(encoding="utf-8"))
    if raw.get("schema") != SCHEMA_ID:
        raise ValueError(f"Unsupported schema in {file_path}: {raw.get('schema')!r}")

    envelope = ContinuityMessagesEnvelope.model_validate(raw)
    source_sha = _source_sha256(envelope, file_path)
    idempotency = _chunk_idempotency_key(source_sha)
    transcript_id = _resolve_transcript_id(envelope, file_path)
    surface = _surface_label(envelope.meta.surface, file_path)

    lane_id: str | None = None
    lane_tags: LaneTags = ()
    if cfg.lane_resolver is not None:
        lane_id, lane_tags = cfg.lane_resolver(envelope, file_path)
    if transcript_lane_excluded(
        lane_id,
        lane_tags,
        exclude_lanes=cfg.exclude_lanes,
        exclude_lane_tag_prefixes=cfg.exclude_lane_tag_prefixes,
    ):
        return []

    chunks: list[Chunk] = []
    for pair in _iter_turn_pairs(envelope.messages):
        exchange = _pair_exchange_text(pair)
        if not exchange.strip():
            continue
        if exchange_matches_case_markers(exchange):
            continue
        body = scrub_chunk_text(exchange)
        if not body.strip():
            continue
        speaker: Literal["user", "assistant"] = "user"
        meta: dict[str, str | int | float | bool] = {
            "transcript_id": transcript_id,
            "turn_index": pair.turn_index,
            "speaker": speaker,
            "source_sha256": source_sha,
            "surface": surface,
            "chunker_version": CHUNKER_VERSION,
            "messages_chunk_key": idempotency,
            "source_format": "continuity_messages",
        }
        if pair.user_ts:
            meta["ts"] = pair.user_ts
        chunks.append(Chunk(text=body, metadata=meta))
    return chunks


def default_lane_resolver(envelope: ContinuityMessagesEnvelope, path: Path) -> tuple[str | None, LaneTags]:
    """Best-effort dominant lane + tags from cortex session_journals and agent_bus."""
    session_id = envelope.meta.session_id or _resolve_transcript_id(envelope, path)
    lane_id: str | None = None
    tags: list[str] = []
    try:
        from cortex_store.schema_snapshot import open_live_db_readonly
    except ImportError:
        return None, ()
    try:
        conn = open_live_db_readonly()
    except (OSError, sqlite3.Error):
        return None, ()
    try:
        row = conn.execute(
            "SELECT dominant_lane FROM session_journals WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if row and row[0]:
            lane_id = str(row[0]).strip() or None
    finally:
        conn.close()
    if lane_id:
        try:
            from agent_bus_store.db.threads import get_thread

            thread = get_thread(lane_id)
            if thread and thread.get("tags"):
                raw_tags = thread["tags"]
                if isinstance(raw_tags, list):
                    tags = [str(t) for t in raw_tags]
        except Exception:
            pass
    return lane_id, tuple(tags)

__all__ = [
    "CHUNKER_VERSION",
    "CASE_CONTENT_MARKERS",
    "DEFAULT_EXCLUDE_LANES",
    "MessagesChunkerConfig",
    "chunk_messages",
    "default_lane_resolver",
    "exchange_matches_case_markers",
    "scrub_chunk_text",
    "transcript_lane_excluded",
]
