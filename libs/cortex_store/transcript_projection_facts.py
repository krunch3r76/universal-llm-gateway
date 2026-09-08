"""Single-pass JSONL walk for transcript projection window facts.

Derives turn boundaries (same rule as ``transcript_assembly._walk_turns``),
per-turn mechanical facts, tab titles, bus sends, and lane touch tallies.
Never reads tool responses (E11) — only ``tool_use`` inputs and user text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from cortex_store.transcript_assembly import _extract_user_text, _read_jsonl
from cortex_store.transcript_lane_touch import lane_touches
from cortex_store.transcript_tool_normalize import iter_normalized_tool_uses

_SHA_RE = re.compile(
    r"\b([0-9a-f]{7,40})\b(?=.*\b(?:land|merge|commit|master|@)\b)", re.I
)
_USER_QUERY_RE = re.compile(r"<user_query>(.*?)</user_query>", re.DOTALL | re.I)
_CHECKPOINT_SUBJ = re.compile(r"^CHECKPOINT\b", re.I)
_CLOSEOUT_SUBJ = re.compile(r"^CLOSEOUT\b", re.I)
_FS_WRITE_OPS = frozenset({"write", "append", "mkdir", "md_replace", "md_append"})
_CODE_MUTATION_TOOLS = frozenset({"write", "strreplace", "delete"})


@dataclass
class BusSendFact:
    """One agent-bus write observed in the JSONL."""

    thread: str
    kind: str
    subject: str
    subject_head: str
    turn_index: int
    record_index: int
    supersedes_turn: int | None = None


@dataclass
class DispatchFact:
    """One dispatch tool_use in the window."""

    tool: str
    op: str | None
    seat_or_model: str | None
    contract: str | None
    ref: str | None = None
    ref_source: str | None = None


@dataclass
class WindowFacts:
    """Mechanical facts extracted from one Cursor window JSONL."""

    transcript_id: str
    turn_count: int
    tab_titles: list[str] = field(default_factory=list)
    opening_ask: str | None = None
    asks: list[str] = field(default_factory=list)
    bus_sends: list[BusSendFact] = field(default_factory=list)
    bus_touch: dict[str, dict[str, int]] = field(default_factory=dict)
    artifacts: list[str] = field(default_factory=list)
    code_paths: list[str] = field(default_factory=list)
    dispatches: list[DispatchFact] = field(default_factory=list)
    sha_mentions: list[str] = field(default_factory=list)
    harness_errors: int = 0
    prose_by_turn: dict[int, str] = field(default_factory=dict)
    turn_user_text: dict[int, str] = field(default_factory=dict)


def turn_index_at(records: list[dict[str, Any]], record_index: int) -> int:
    """Count user-text turns opened at or before *record_index* (1-based turn index)."""
    count = 0
    for idx, record in enumerate(records[: record_index + 1]):
        if record.get("role") != "user":
            continue
        message = record.get("message") or {}
        content = message.get("content")
        if not isinstance(content, list):
            continue
        if _extract_user_text(content):
            count += 1
    return count


def _normalize_cortex_path(path: str) -> str:
    p = path.strip()
    if p.startswith("cortex://"):
        return p
    if p.startswith("notes/"):
        return f"cortex://{p}"
    return p


def _subject_kind(subject: str) -> str:
    if _CHECKPOINT_SUBJ.match(subject):
        return "CHECKPOINT"
    if _CLOSEOUT_SUBJ.match(subject):
        return "CLOSEOUT"
    if subject.upper().startswith("INFO"):
        return "INFO"
    if subject.upper().startswith("COORD"):
        return "COORD"
    return "OTHER"


def _tool_name_lower(block: dict[str, Any]) -> str:
    return str(block.get("name") or "").lower()


def _parse_records(records: list[dict[str, Any]], transcript_id: str) -> WindowFacts:
    facts = WindowFacts(transcript_id=transcript_id, turn_count=0)
    current_turn = 0
    first_ask: str | None = None
    sha_seen: set[str] = set()
    artifact_seen: set[str] = set()
    code_seen: set[str] = set()
    ask_seen: set[str] = set()

    for rec_idx, record in enumerate(records):
        if record.get("type") == "turn_ended":
            if record.get("status") not in (None, "completed", "success"):
                facts.harness_errors += 1
            continue
        role = record.get("role")
        message = record.get("message") or {}
        content = message.get("content")
        if not isinstance(content, list):
            continue
        if role == "user":
            user_text = _extract_user_text(content)
            if not user_text:
                continue
            current_turn += 1
            facts.turn_count = current_turn
            facts.turn_user_text[current_turn] = user_text
            for match in _USER_QUERY_RE.finditer(user_text):
                head = " ".join(match.group(1).split())[:160]
                if head and head not in ask_seen and len(facts.asks) < 3:
                    ask_seen.add(head)
                    facts.asks.append(head)
            if first_ask is None:
                first_ask = user_text[:160]
        elif role == "assistant":
            prose_parts: list[str] = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text = block.get("text", "")
                    if isinstance(text, str) and text.strip():
                        prose_parts.append(text.strip())
            for tool_name, inp in iter_normalized_tool_uses([record]):
                lname = tool_name.lower()
                if lname == "rename_chat" or "rename_chat" in lname:
                    title = inp.get("title") or inp.get("chat_title")
                    if isinstance(title, str) and title.strip():
                        facts.tab_titles.append(title.strip())
                elif lname == "agent_bus" or lname.endswith("agent_bus"):
                    thread = str(inp.get("thread") or inp.get("thread_id") or "")
                    subject = str(inp.get("subject") or "")
                    op = str(inp.get("op") or inp.get("tool") or "send").lower()
                    if thread and subject and op in {"send", "post", "reply"}:
                        turn_idx = turn_index_at(records, rec_idx)
                        sup = inp.get("supersedes_turn")
                        facts.bus_sends.append(
                            BusSendFact(
                                thread=thread,
                                kind=_subject_kind(subject),
                                subject=subject,
                                subject_head=subject[:120],
                                turn_index=turn_idx,
                                record_index=rec_idx,
                                supersedes_turn=int(sup) if sup is not None else None,
                            )
                        )
                elif lname == "fs":
                    op = str(inp.get("op") or "").lower()
                    path = inp.get("path") or inp.get("uri")
                    is_write = op in _FS_WRITE_OPS or (
                        isinstance(path, str) and inp.get("content") is not None
                    )
                    if isinstance(path, str) and is_write:
                        norm = _normalize_cortex_path(path)
                        if norm.startswith("cortex://") and norm not in artifact_seen:
                            artifact_seen.add(norm)
                            facts.artifacts.append(norm)
                elif lname == "team_dispatch":
                    facts.dispatches.append(
                        DispatchFact(
                            tool="team_dispatch",
                            op=str(inp.get("op") or "") or None,
                            seat_or_model=str(inp.get("seat") or inp.get("model") or "") or None,
                            contract=str(inp.get("contract") or "") or None,
                            ref=str(
                                inp.get("execution_id")
                                or inp.get("dispatch_id")
                                or inp.get("dispatch_thread_id")
                                or ""
                            )
                            or None,
                            ref_source="tool_input"
                            if inp.get("execution_id") or inp.get("dispatch_id")
                            else None,
                        )
                    )
                elif lname in {"write", "strreplace", "delete"}:
                    path = inp.get("path")
                    if isinstance(path, str) and path not in code_seen:
                        code_seen.add(path)
                        facts.code_paths.append(path)
            if prose_parts and current_turn:
                joined = "\n".join(prose_parts)
                facts.prose_by_turn[current_turn] = joined
                for sha in _SHA_RE.findall(joined):
                    if sha not in sha_seen and len(facts.sha_mentions) < 8:
                        sha_seen.add(sha)
                        facts.sha_mentions.append(sha)

    facts.opening_ask = first_ask
    facts.bus_touch = lane_touches(records)
    facts.artifacts = facts.artifacts[:12]
    facts.code_paths = facts.code_paths[:12]
    facts.dispatches = facts.dispatches[:8]
    return facts


def parse_jsonl_path(jsonl_path: str | Any, *, transcript_id: str | None = None) -> WindowFacts:
    """Load a JSONL file and return mechanical window facts."""
    from pathlib import Path

    path = Path(jsonl_path)
    tid = transcript_id or path.parent.name
    records = _read_jsonl(path)
    return _parse_records(records, tid)


def parse_records(records: list[dict[str, Any]], transcript_id: str) -> WindowFacts:
    """Parse in-memory JSONL records into window facts."""
    return _parse_records(records, transcript_id)


__all__ = [
    "BusSendFact",
    "DispatchFact",
    "WindowFacts",
    "parse_jsonl_path",
    "parse_records",
    "turn_index_at",
]
