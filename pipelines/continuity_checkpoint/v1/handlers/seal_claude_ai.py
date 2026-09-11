"""Claude.ai succession seal via session_close + CSE harvest."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from agent_seat.session_id import derive_session_id_from_timestamp
from continuity_tape.messages import ContinuityMessagesEnvelope, EnvelopeMeta

_AGENT = "web-anthropic"
_CSE_TOKEN_RE = re.compile(r"cse_[A-Za-z0-9]+")
_CLAUDE_RESPONDED_PREFIX = "Claude responded:"
_SUCCESSION_STUB = (
    "## Session Summary\n\n"
    "**Decisions:** (absent — succession harvest)\n"
    "**Open items:** (absent — succession harvest)\n"
)
_SUMMARY = "Succession harvest seal for claude.ai continuity speech tape."


def _normalize_turn_text(text: str) -> str:
    t = (text or "").strip()
    if t.startswith(_CLAUDE_RESPONDED_PREFIX):
        return t[len(_CLAUDE_RESPONDED_PREFIX) :].strip()
    return t


def _dedupe_turns(turns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered = sorted(turns, key=lambda t: int(t.get("ordinal") or 0))
    out: list[dict[str, Any]] = []
    for turn in ordered:
        norm = _normalize_turn_text(str(turn.get("text") or ""))
        if not norm:
            continue
        if out and norm == _normalize_turn_text(str(out[-1].get("text") or "")):
            continue
        out.append(turn)
    return out


def _author_to_role(author: str) -> str:
    label = (author or "").lower()
    if label in {"user", "human"}:
        return "user"
    return "assistant"


def _coverage_from_harvest(harvest: dict[str, Any], turn_count: int) -> str:
    if turn_count <= 2:
        return "tail_only"
    if harvest.get("truncated"):
        return "tail_only"
    cursor = harvest.get("cursor")
    if cursor is not None and turn_count <= int(cursor):
        return "tail_only"
    return "full"


def _lookup_session_id_for_transcript(transcript_id: str) -> str | None:
    from cortex_store.db import cortex_conn

    with cortex_conn() as conn:
        row = conn.execute(
            "SELECT session_id FROM session_journals WHERE conversation_uuid = ? "
            "ORDER BY id DESC LIMIT 1",
            (transcript_id,),
        ).fetchone()
    if row and row["session_id"]:
        return str(row["session_id"])
    return None


def _mint_session_id() -> str:
    return derive_session_id_from_timestamp(_AGENT, datetime.now(UTC).isoformat())


async def seal_claude_ai(
    *,
    thread: str,
    chat_url: str,
    transcript_id: str,
    from_agent: str,
    harvest_fn: Callable[..., Awaitable[dict[str, Any]]],
    cortex_dispatch_fn: Callable[..., Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
    """Harvest CSE chat, seal messages-v1 via session_close, map cursor seal shape."""
    _ = from_agent  # reserved for future provenance stamps
    harvest = await harvest_fn(chat_url, limit=50, source="chat")
    if harvest.get("outcome") != "harvested":
        return {
            "refused": {
                "code": "checkpoint.harvest_unavailable",
                "message": (
                    f"outcome={harvest.get('outcome')!r} "
                    f"reason={harvest.get('reason')!r}"
                ),
            },
            "already_closed": False,
        }

    deduped = _dedupe_turns(list(harvest.get("turns") or []))
    if not deduped:
        return {
            "refused": {
                "code": "transcript_seal.hollow",
                "message": "zero turns after dedupe",
            },
            "already_closed": False,
        }

    content_provenance = harvest.get("content_provenance")
    coverage = harvest.get("coverage") or _coverage_from_harvest(harvest, len(deduped))
    messages = [
        {
            "role": _author_to_role(str(turn.get("author") or "")),
            "content": str(turn.get("text") or ""),
        }
        for turn in deduped
    ]

    from cortex_store.session_close_successor_hop import (
        lookup_journaled_by_conversation_uuid,
    )

    human_closed = lookup_journaled_by_conversation_uuid(transcript_id)
    if human_closed is not None:
        turn_count = len(messages)
        return {
            "session_id": human_closed.session_id,
            "transcript_id": transcript_id,
            "turn_count": turn_count,
            "already_closed": True,
            "refused": None,
            "chat_url": chat_url,
            "coverage": coverage,
            "content_provenance": content_provenance,
        }

    session_id = _lookup_session_id_for_transcript(transcript_id) or _mint_session_id()
    envelope = ContinuityMessagesEnvelope(
        messages=messages,
        meta=EnvelopeMeta(
            surface="claude_ai",
            transcript_id=transcript_id,
            chat_url=chat_url,
            turn_count=len(messages),
            message_count=len(messages),
            coverage=coverage,
        ),
    )
    close_args: dict[str, Any] = {
        "session_id": session_id,
        "agent": _AGENT,
        "session_summary_md": _SUCCESSION_STUB,
        "summary": _SUMMARY,
        "transcript_messages": envelope.model_dump(mode="json", by_alias=True),
        "transcript_depth": "verbatim",
        "entity_ids": [f"agent-bus:{thread}"],
        "closed_by": "succession",
        "assistant_label": "Assistant",
        "succession_seal_authority": True,
    }

    close = await cortex_dispatch_fn("session_close", close_args)
    if close.get("already_closed"):
        turn_count = int(close.get("turn_count") or len(messages))
        if turn_count == 0:
            from .seal import _count_turns_from_journal

            counted = _count_turns_from_journal(session_id)
            turn_count = int(counted or len(messages))
        return {
            "session_id": session_id,
            "transcript_id": transcript_id,
            "turn_count": turn_count,
            "messages_sha256": close.get("content_hash")
            or close.get("messages_sha256"),
            "verbatim_codec": "messages-v1",
            "already_closed": True,
            "refused": None,
            "chat_url": chat_url,
            "coverage": coverage,
            "content_provenance": content_provenance,
        }

    if close.get("error"):
        code = str(close.get("reason") or close.get("code") or "session_close.refused")
        return {
            "refused": {"code": code, "message": str(close.get("error"))},
            "already_closed": False,
        }

    from cortex_store.dispatch_ops.ops_transcript_seal import _stamp_succession_fields

    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    _stamp_succession_fields(
        session_id=session_id,
        sealed_by=_AGENT,
        sealed_on=now,
        conversation_uuid=transcript_id,
        dominant_lane=thread,
    )

    return {
        "session_id": session_id,
        "journal_row_id": close.get("journal_row_id"),
        "transcript_id": transcript_id,
        "transcript_entity_id": close.get("transcript_entity_id"),
        "turn_count": int(close.get("turn_count") or len(messages)),
        "messages_sha256": close.get("content_hash") or close.get("messages_sha256"),
        "verbatim_codec": "messages-v1",
        "already_closed": False,
        "refused": None,
        "chat_url": chat_url,
        "coverage": coverage,
        "content_provenance": content_provenance,
    }


__all__ = ["seal_claude_ai"]
