"""Chunk ``ulg.continuity.messages/1`` continuity seal files into RAG dialogue exchanges.

``chunk_messages`` is called from ``chunk_file`` in ``services/rag/chunkers/office_dispatch.py``
when the RAG watcher indexes ``*.messages.json`` paths; lane and tag exclusion apply during
thread metadata resolution.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from continuity_tape.messages import (
    SCHEMA_ID,
    TOOL_MARKER_RE,
    ContinuityMessagesEnvelope,
)
from continuity_tape.notice import is_notice_row

from services.rag.chunkers._sizing import _CHUNK_CHARS_PAD, _CHUNK_CHARS_TARGET
from services.rag.chunkers.paragraph_utils import Chunk, _annotate_chunk_indices

logger = logging.getLogger(__name__)

CHUNKER_VERSION = "conversation-messages/3"
CHUNKER_POLICY_VERSION = "messages-projection/1"

# Map-slot postcondition from Slice B acceptance: len//4 + 287 + 4096 <= 8192.
_MAP_PREFLIGHT_OVERHEAD = 287
_MAP_SLOT_RESERVE = 4096
_MAP_SLOT_LIMIT = 8192

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

_LANE_RESOLVER_LOGGED = False
_THREAD_TAGS_CACHE: dict[str, list[str]] = {}
_THREAD_TAGS_BUS_WARNED: set[str] = set()

_HARNESS_BLOCK_RES = [
    re.compile(r"<timestamp>[\s\S]*?</timestamp>\s*", re.IGNORECASE),
    re.compile(
        r"<manually_attached_skills>[\s\S]*?</manually_attached_skills>\s*",
        re.IGNORECASE,
    ),
    re.compile(
        r"<available_subagent_types>[\s\S]*?</available_subagent_types>\s*",
        re.IGNORECASE,
    ),
    re.compile(
        r"<dynamic_tool_namespaces>[\s\S]*?</dynamic_tool_namespaces>\s*", re.IGNORECASE
    ),
    re.compile(
        r"<dynamic_tool_catalog>[\s\S]*?</dynamic_tool_catalog>\s*", re.IGNORECASE
    ),
    re.compile(r"<cursor_commands>[\s\S]*?</cursor_commands>\s*", re.IGNORECASE),
    re.compile(r"<system_reminder>[\s\S]*?</system_reminder>\s*", re.IGNORECASE),
    re.compile(r"<agent_skills>[\s\S]*?</agent_skills>\s*", re.IGNORECASE),
    re.compile(r"<user_rules>[\s\S]*?</user_rules>\s*", re.IGNORECASE),
    re.compile(
        r"<available_subagent_models>[\s\S]*?</available_subagent_models>\s*",
        re.IGNORECASE,
    ),
]
_USER_QUERY_RE = re.compile(r"<user_query>\s*([\s\S]*?)\s*</user_query>", re.IGNORECASE)
_INLINE_TIMESTAMP_RE = re.compile(
    r"<timestamp>\s*([\s\S]*?)\s*</timestamp>", re.IGNORECASE
)
_TOOL_CALL_PARA_RE = re.compile(
    r"^\[tool call: [^\]]+\]$",
    re.MULTILINE,
)
_TOOL_USE_INLINE_RE = re.compile(r"\[tool call: [^\]]+\]")
_REASONING_FENCE_RE = re.compile(
    r"<\s*(?:redacted_thinking|think|thinking|reasoning)[^>]*>[\s\S]*?"
    r"(?:</\s*(?:redacted_thinking|think|thinking|reasoning)\s*>|$)",
    re.IGNORECASE,
)
_TOOL_JSON_RE = re.compile(
    r"\{[^{}]*\"(?:type|tool_use_id|input|name)\"[^{}]*\}",
    re.DOTALL,
)

_API_KEY_RE = re.compile(
    r"(?i)(?:"
    r"\b(?:sk-[a-z0-9_-]{10,}|sk-ant-[a-z0-9_-]{10,}|"
    r"ghp_[a-z0-9]{20,}|gho_[a-z0-9]{20,}|ghs_[a-z0-9]{20,}|"
    r"github_pat_[a-z0-9_]{20,}|xai-[a-z0-9_-]{10,}|AIza[a-z0-9_-]{20,}|"
    r"xox[baprs]-[a-z0-9-]{10,})|"
    r"Bearer\s+[A-Za-z0-9._\-+/=]{20,}|"
    r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |ENCRYPTED )?PRIVATE KEY-----[\s\S]*?"
    r"-----END (?:RSA |EC |OPENSSH |DSA |ENCRYPTED )?PRIVATE KEY-----)"
)
_JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_PHONE_RE = re.compile(r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b")
_STREET_RE = re.compile(
    r"\b\d{1,5}\s+(?:[A-Za-z0-9.'-]+\s+){1,4}"
    r"(?:St|Street|Ave|Avenue|Rd|Road|Blvd|Lane|Dr|Drive|Way|Ct|Court|Pl|Place|Cir|Pkwy|Hwy)\b",
    re.IGNORECASE,
)
_ENV_DUMP_RE = re.compile(
    r"(?m)^(?:export\s+)?(?:DATABASE_URL|[A-Z][A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD|PASS)[A-Z0-9_]*)="
    r"(?:[^\n\\]|\\.)+$"
)

_SYNTHETIC_PREFIXES = (
    "briefly inform the user about the task result",
    "you are the attended liaison for house",
    "conductors on house",
    "liaison ide hop",
    "this chat monitors house",
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
    has_real_user: bool = True


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_hash_includes_policy(path: Path) -> bool:
    """True when the chunker policy version must participate in the cache key.

    JSON transcripts and files under the seal or transcript roots re-run when
    the policy changes even if the file bytes are unchanged. Other files keep
    a plain content hash so article joins stay stable.
    """
    if path.name.lower().endswith(".json"):
        return True
    parts = [part.lower() for part in path.parts]
    for index in range(len(parts) - 2):
        if parts[index] == "notes" and parts[index + 1] == "system":
            if parts[index + 2] in {"transcripts", "seals"}:
                return True
    return False


def indexing_source_hash(raw: bytes, path: Path) -> str:
    """Source identity for RAG skip guards.

    Messages seals still fold ``CHUNKER_VERSION``. Policy paths also fold
    ``CHUNKER_POLICY_VERSION`` so a skip/cap change reindexes unchanged files.
    """
    folded: list[str] = []
    if path.name.endswith(".messages.json"):
        folded.append(CHUNKER_VERSION)
    if source_hash_includes_policy(path):
        folded.append(CHUNKER_POLICY_VERSION)
    if not folded:
        return hashlib.sha256(raw).hexdigest()
    payload = raw + b"\n" + "\n".join(folded).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


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
    """Return whether this seal's lane id or tags are on the chunker exclusion list.

    ``chunk_messages`` drops the file when this is true so case lanes never
    become dialogue chunks. Tag match is a case-insensitive prefix check.
    """
    if lane_id and lane_id in exclude_lanes:
        return True
    return _lane_tag_excluded(lane_tags, exclude_lane_tag_prefixes)


def exchange_matches_case_markers(text: str) -> bool:
    """Return whether exchange text contains a case-matter marker and must be skipped.

    Markers are matter identifiers (appeal numbers, street names). Matching
    exchanges are omitted before scrub and split so they are not embedded.
    """
    lowered = text.casefold()
    return any(marker in lowered for marker in CASE_CONTENT_MARKERS)


def _strip_harness_blocks(content: str) -> str:
    text = content
    for pattern in _HARNESS_BLOCK_RES:
        text = pattern.sub("", text)
    return text.strip()


def _synthetic_unwrapped_text(text: str) -> bool:
    folded = text.casefold().strip()
    if not folded:
        return True
    if any(folded.startswith(prefix) for prefix in _SYNTHETIC_PREFIXES):
        return True
    if folded.startswith("[grok_bot_hidden_prompt") or folded.startswith(
        "[sand_hidden_prompt"
    ):
        return True
    if re.match(r"^\[a background task\b", folded):
        return True
    if re.match(r"^resume\s+\d+\b", folded) and re.search(
        r"\bliaison ide hop\b", folded[:320]
    ):
        return True
    if re.search(r"\bliaison ide hop\s*\(", folded[:400]):
        return True
    return False


def _extract_last_user_query(content: str) -> str:
    stripped = _strip_harness_blocks(content)
    queries = [m.group(1).strip() for m in _USER_QUERY_RE.finditer(stripped)]
    non_synthetic = [q for q in queries if q and not _synthetic_unwrapped_text(q)]
    if non_synthetic:
        return non_synthetic[-1]
    if queries:
        remainder = _USER_QUERY_RE.sub("", stripped).strip()
        if remainder and not _synthetic_unwrapped_text(remainder):
            return remainder
        return ""
    return stripped.strip()


def _inline_timestamp(content: str) -> str | None:
    matches = list(_INLINE_TIMESTAMP_RE.finditer(content))
    if matches:
        return matches[-1].group(1).strip()
    return None


def _is_synthetic_user(content: str) -> bool:
    if is_notice_row("user", content):
        return True
    unwrapped = _extract_last_user_query(content)
    if not unwrapped:
        return True
    return _synthetic_unwrapped_text(unwrapped)


def _verbatim_user(content: str) -> str:
    """Operator utterance after harness strip (a:1627 — not paraphrased)."""
    return _extract_last_user_query(content)


def _strip_balanced_json_objects(text: str) -> str:
    """Remove JSON objects that look like tool-use payloads (supports nesting)."""
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        if text[i] != "{":
            out.append(text[i])
            i += 1
            continue
        depth = 0
        start = i
        j = i
        while j < n:
            ch = text[j]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    j += 1
                    break
            j += 1
        else:
            out.append(text[i])
            i += 1
            continue
        candidate = text[start:j]
        if re.search(r'"(?:type|tool_use_id|input|name)"', candidate):
            i = j
            continue
        out.append(candidate)
        i = j
    return "".join(out)


def _strip_tool_json_blobs(text: str) -> str:
    out = text
    for _ in range(32):
        new = _strip_balanced_json_objects(out)
        new = _TOOL_JSON_RE.sub("", new)
        if new == out:
            break
        out = new
    return out


def _normalize_assistant(content: str) -> str:
    if not content:
        return ""
    text = _REASONING_FENCE_RE.sub("", content)
    text = _strip_tool_json_blobs(text)
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
    """Replace secrets and contact PII in text that will be stored as a chunk.

    Runs after exchange assembly and before the prose-budget split. Returns
    the redacted string; it does not write files or emit events.
    """
    out = text
    out = _API_KEY_RE.sub("[REDACTED_SECRET]", out)
    out = _JWT_RE.sub("[REDACTED_JWT]", out)
    out = _ENV_DUMP_RE.sub("[REDACTED_ENV]", out)
    out = _EMAIL_RE.sub("[REDACTED_EMAIL]", out)
    out = _PHONE_RE.sub("[REDACTED_PHONE]", out)
    out = _STREET_RE.sub("[REDACTED_ADDRESS]", out)
    return out


def _message_ts(
    msg: dict[str, Any], envelope: ContinuityMessagesEnvelope
) -> str | None:
    content = msg.get("content")
    if isinstance(content, str):
        ts = _inline_timestamp(content)
        if ts:
            return ts
    for key in ("ts", "timestamp", "created_at"):
        val = msg.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    meta = envelope.meta
    if meta.observed_at:
        return meta.observed_at
    if meta.sealed_at:
        return meta.sealed_at
    return None


def _iter_turn_pairs(
    messages: list[dict[str, Any]], envelope: ContinuityMessagesEnvelope
) -> list[_TurnPair]:
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
                continue
            user_text = _verbatim_user(content)
            if not user_text.strip():
                continue
            if pending_user is not None:
                pairs.append(
                    _TurnPair(
                        turn_index=int(pending_user.get("turn_index") or 0),
                        user_text=_verbatim_user(
                            str(pending_user.get("content") or "")
                        ),
                        assistant_text="",
                        user_ts=_message_ts(pending_user, envelope),
                        has_real_user=True,
                    )
                )
            pending_user = msg
        elif role == "assistant" and pending_user is not None:
            user_text = _verbatim_user(str(pending_user.get("content") or ""))
            assistant_text = _normalize_assistant(
                content if isinstance(content, str) else ""
            )
            turn_index = int(
                pending_user.get("turn_index") or msg.get("turn_index") or 0
            )
            pairs.append(
                _TurnPair(
                    turn_index=turn_index,
                    user_text=user_text,
                    assistant_text=assistant_text,
                    user_ts=_message_ts(pending_user, envelope),
                    has_real_user=bool(user_text.strip()),
                )
            )
            pending_user = None
    if pending_user is not None:
        user_text = _verbatim_user(str(pending_user.get("content") or ""))
        if user_text.strip():
            pairs.append(
                _TurnPair(
                    turn_index=int(pending_user.get("turn_index") or 0),
                    user_text=user_text,
                    assistant_text="",
                    user_ts=_message_ts(pending_user, envelope),
                    has_real_user=True,
                )
            )
    return pairs


def _pair_exchange_text(pair: _TurnPair) -> str:
    parts: list[str] = []
    if pair.user_text.strip():
        parts.append(pair.user_text)
    if pair.assistant_text:
        parts.append(pair.assistant_text)
    return "\n\n".join(parts)


def _resolve_transcript_id(envelope: ContinuityMessagesEnvelope, path: Path) -> str:
    meta = envelope.meta
    if meta.session_id:
        return meta.session_id
    if meta.transcript_id:
        return meta.transcript_id
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


def _load_thread_tags(thread_id: str) -> list[str]:
    """Load bus thread tags via agent-bus HTTP; cache successes for the process lifetime."""
    cached = _THREAD_TAGS_CACHE.get(thread_id)
    if cached is not None:
        return cached
    tags: list[str] = []
    try:
        from cortex_store.transcript_projection_membership import fetch_thread_detail

        detail = fetch_thread_detail(thread_id)
    except Exception as exc:
        if thread_id not in _THREAD_TAGS_BUS_WARNED:
            logger.warning(
                "messages_chunker thread tags (agent-bus) thread=%s: %s",
                thread_id,
                exc,
            )
            _THREAD_TAGS_BUS_WARNED.add(thread_id)
        return tags
    if not detail:
        if thread_id not in _THREAD_TAGS_BUS_WARNED:
            logger.warning(
                "messages_chunker thread tags (agent-bus) thread=%s: unavailable",
                thread_id,
            )
            _THREAD_TAGS_BUS_WARNED.add(thread_id)
        return tags
    raw = detail.get("tags")
    if isinstance(raw, list):
        tags = [str(t) for t in raw]
    _THREAD_TAGS_CACHE[thread_id] = tags
    return tags


def _sqlite_exception(exc: BaseException) -> bool:
    # Type-name compare on the exception class module, not a database read.
    return type(exc).__module__ == "sqlite3"


def _expected_lane_resolver_failure(exc: BaseException) -> bool:
    if isinstance(exc, (ImportError, OSError)):
        return True
    return _sqlite_exception(exc)


def default_lane_resolver(
    envelope: ContinuityMessagesEnvelope, path: Path
) -> tuple[str | None, LaneTags]:
    """Resolve dominant lane from cortex and bus thread tags via HTTP (soft-fail on errors)."""
    global _LANE_RESOLVER_LOGGED
    session_id = envelope.meta.session_id or _resolve_transcript_id(envelope, path)
    lane_id: str | None = None
    try:
        from cortex_store.schema_snapshot import open_live_db_readonly

        conn = open_live_db_readonly()
        try:
            row = conn.execute(
                "SELECT dominant_lane FROM session_journals WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if row and row[0]:
                lane_id = str(row[0]).strip() or None
        finally:
            conn.close()
    except Exception as exc:
        if _expected_lane_resolver_failure(exc):
            if not _LANE_RESOLVER_LOGGED:
                logger.warning("messages_chunker lane resolver (cortex): %s", exc)
                _LANE_RESOLVER_LOGGED = True
        else:
            logger.exception("messages_chunker lane resolver (cortex): %s", exc)
    tags = _load_thread_tags(lane_id) if lane_id else []
    return lane_id, tuple(tags)


def _max_piece_chars() -> int:
    """Largest piece length that meets both the prose hard max and the map-slot estimate."""
    hard = _CHUNK_CHARS_TARGET + _CHUNK_CHARS_PAD
    room = _MAP_SLOT_LIMIT - _MAP_PREFLIGHT_OVERHEAD - _MAP_SLOT_RESERVE
    by_estimate = room * 4 + 3
    return max(1, min(hard, by_estimate))


def _pieces_of_long_text(text: str, limit: int) -> list[str]:
    """Split one over-long paragraph on words, then on characters when a word exceeds ``limit``."""
    words = text.split()
    if not words:
        return []
    pieces: list[str] = []
    buf: list[str] = []
    buf_len = 0
    for word in words:
        if len(word) > limit:
            if buf:
                pieces.append(" ".join(buf))
                buf = []
                buf_len = 0
            pieces.extend(word[i : i + limit] for i in range(0, len(word), limit))
            continue
        extra = len(word) if not buf else 1 + len(word)
        if buf and buf_len + extra > limit:
            pieces.append(" ".join(buf))
            buf = [word]
            buf_len = len(word)
        else:
            buf.append(word)
            buf_len += extra
    if buf:
        pieces.append(" ".join(buf))
    return pieces


def _split_exchange_body(body: str) -> list[str]:
    """Cut one scrubbed exchange so every piece stays inside the prose budget.

    A body already under the char cap and the map-slot estimate stays one piece,
    including a multi-paragraph body. Longer text packs on paragraph boundaries
    until the next join would exceed the cap, then on words and raw characters.
    Overlap carry and pad-zone flush are omitted: those paths emit a second copy
    of a fitting body and can grow a later window past the hard max.
    """
    text = body.strip()
    if not text:
        return []
    limit = _max_piece_chars()
    if len(text) <= limit:
        return [text]
    paragraphs = [p.strip() for p in re.split(r"\n{2,}", text) if p.strip()]
    pieces: list[str] = []
    current: list[str] = []
    current_len = 0
    for para in paragraphs:
        if len(para) > limit:
            if current:
                pieces.append("\n\n".join(current))
                current = []
                current_len = 0
            pieces.extend(_pieces_of_long_text(para, limit))
            continue
        extra = len(para) if not current else 2 + len(para)
        if current and current_len + extra > limit:
            pieces.append("\n\n".join(current))
            current = [para]
            current_len = len(para)
        else:
            current.append(para)
            current_len += extra
    if current:
        pieces.append("\n\n".join(current))
    return [part for part in pieces if part.strip()]


def chunk_messages(
    path: str | Path,
    *,
    config: MessagesChunkerConfig | None = None,
) -> list[Chunk]:
    """Split one continuity ``*.messages.json`` envelope into dialogue-exchange chunks.

    Called from ``chunk_file`` when the path name ends with ``.messages.json``.
    Each scrubbed user+assistant turn is one chunk when it fits the prose hard
    max and the map-slot estimate; longer turns are cut without overlap carry.
    Returns an empty list when the lane is excluded or the body scrubs away.
    """
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

    resolver = cfg.lane_resolver or default_lane_resolver
    lane_id, lane_tags = resolver(envelope, file_path)
    if transcript_lane_excluded(
        lane_id,
        lane_tags,
        exclude_lanes=cfg.exclude_lanes,
        exclude_lane_tag_prefixes=cfg.exclude_lane_tag_prefixes,
    ):
        return []

    chunks: list[Chunk] = []
    for pair in _iter_turn_pairs(envelope.messages, envelope):
        exchange = _pair_exchange_text(pair)
        if not exchange.strip():
            continue
        if exchange_matches_case_markers(exchange):
            continue
        body = scrub_chunk_text(exchange)
        if not body.strip():
            continue
        speaker: Literal["user", "assistant", "exchange"]
        if pair.has_real_user and pair.assistant_text:
            speaker = "exchange"
        elif pair.has_real_user:
            speaker = "user"
        else:
            speaker = "assistant"
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
        for part_text in _split_exchange_body(body):
            chunks.append(Chunk(text=part_text, metadata=dict(meta)))
    return _annotate_chunk_indices(chunks)


__all__ = [
    "CHUNKER_VERSION",
    "CASE_CONTENT_MARKERS",
    "DEFAULT_EXCLUDE_LANES",
    "MessagesChunkerConfig",
    "chunk_messages",
    "default_lane_resolver",
    "exchange_matches_case_markers",
    "indexing_source_hash",
    "scrub_chunk_text",
    "transcript_lane_excluded",
]
