"""Persist CSE DOM harvest through the shared ``archive_chat_transcript`` helper."""

from __future__ import annotations

from datetime import UTC, datetime

from claude_bundles.cdp_reclaim_refuse import cse_id_from_url

from chat_harvest.archive import (
    ArchiveConflictError,
    ArchiveRefusalError,
    archive_chat_transcript,
)
from chat_harvest.messages import turns_to_messages
from chat_harvest.models import ChatTurn, ConflictDetail

# Sentinel passed to CSE turn JS when the caller wants the full transcript.
CSE_HARVEST_NO_LIMIT = 0


def resolve_cse_harvest_limit(limit: int | None) -> int:
    """Map request limit to the JS harvest cap (0 = no truncation)."""
    if limit is None:
        return CSE_HARVEST_NO_LIMIT
    return int(limit)


def cse_dom_rows_to_chat_turns(
    rows: list[dict[str, object]] | list[object],
) -> list[ChatTurn]:
    """Map normalized CSE DOM dict rows to ``ChatTurn`` for archive alignment."""
    turns: list[ChatTurn] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        author = str(row.get("author") or "assistant")
        if author not in {"user", "assistant"}:
            continue
        turns.append(
            ChatTurn(
                author=author,  # type: ignore[arg-type]
                ordinal=int(row.get("ordinal") or index + 1),
                text=str(row.get("text") or ""),
                source="dom",
            )
        )
    return turns


def cse_conversation_id(chat_url: str, page_url: str | None = None) -> str | None:
    """Resolve the durable archive key for a Cowork CSE URL."""
    return cse_id_from_url(chat_url) or cse_id_from_url(page_url or "")


def persist_cse_dom_harvest(
    *,
    chat_url: str,
    page_url: str | None,
    dom_turns: list[dict[str, object]],
) -> tuple[str, str, str, int, int]:
    """Write ``ulg.continuity.messages/1``; return uri, sha, alignment, counts."""
    conversation_id = cse_conversation_id(chat_url, page_url)
    if not conversation_id:
        raise ValueError("missing CSE conversation id")
    archive_turns = cse_dom_rows_to_chat_turns(dom_turns)
    messages = turns_to_messages(archive_turns)
    turn_count = max((int(m.get("turn_index") or 0) for m in messages), default=0)
    message_count = len(messages)
    url = (page_url or chat_url).strip()
    harvested_at = datetime.now(UTC).isoformat()
    archive_uri, archive_sha256, alignment = archive_chat_transcript(
        "claude",
        conversation_id,
        url,
        archive_turns,
        harvested_at=harvested_at,
        streaming=False,
    )
    return archive_uri, archive_sha256, alignment, turn_count, message_count


def archive_fields_from_conflict(exc: ArchiveConflictError) -> dict[str, object]:
    detail = ConflictDetail(
        ordinal=exc.detail.ordinal,
        existing_digest=exc.detail.existing_digest,
        new_digest=exc.detail.new_digest,
        existing_len=exc.detail.existing_len,
        new_len=exc.detail.new_len,
        existing_snippet=exc.detail.existing_snippet,
        new_snippet=exc.detail.new_snippet,
    )
    return {
        "outcome": "archive_conflict",
        "code": "archive_conflict",
        "reason": str(exc),
        "existing_sha256": exc.existing_sha256,
        "conflict": detail,
    }


def archive_fields_from_refusal(exc: ArchiveRefusalError) -> dict[str, object]:
    return {
        "outcome": "refused",
        "code": exc.code,
        "reason": exc.reason,
    }


__all__ = [
    "CSE_HARVEST_NO_LIMIT",
    "archive_fields_from_conflict",
    "archive_fields_from_refusal",
    "cse_conversation_id",
    "cse_dom_rows_to_chat_turns",
    "persist_cse_dom_harvest",
    "resolve_cse_harvest_limit",
]
