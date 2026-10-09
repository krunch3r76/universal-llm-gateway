"""Shared succession seal: one inline messages envelope, one session_close."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from agent_seat.session_id import derive_session_id_from_timestamp

Dispatch = Callable[..., Awaitable[dict[str, Any]]]

# ``agent`` on the close is the bus identity (grok-bot-hm1). The session id
# prefix stays the seat slug (grok-bot). Same split as a:33286.


@dataclass(frozen=True)
class SealResult:
    close_args: dict[str, Any] | None
    close: dict[str, Any] | None
    refused: dict[str, str] | None
    session_id: str


def derive_anchored_session_id(
    seat_slug: str,
    opened_at: str,
    conversation_uuid: str | None,
) -> str:
    """Time-anchored id. The uuid is the conversation, never a fixed date."""
    return derive_session_id_from_timestamp(
        seat_slug,
        opened_at,
        deterministic=True,
        conversation_uuid=conversation_uuid,
    )


def _refuse(code: str, message: str, session_id: str = "") -> SealResult:
    return SealResult(
        close_args=None,
        close=None,
        refused={"code": code, "message": message},
        session_id=session_id,
    )


async def seal_messages_envelope(
    envelope: dict[str, Any],
    *,
    agent: str,
    seat_slug: str,
    conversation_uuid: str | None,
    opened_at: str | None,
    thread: str,
    structural_md: str,
    summary: str,
    cortex_dispatch_fn: Dispatch,
    session_id: str | None = None,
    assistant_label: str | None = None,
    attach_conversation_uuid: bool = False,
    transcript_messages_path: str | None = None,
) -> SealResult:
    """Close one messages envelope.

    ``session_id`` is set by claude.ai after its own lookup-or-mint so that
    path stays byte-identical. Grok omits it and the id is derived here.
    ``attach_conversation_uuid`` is off for claude.ai: that close_args dict
    did not carry the field. Grok sets it so persist writes the column and
    Stargate does not open sqlite.
    ``transcript_messages_path`` is rejected. Inline messages are the seal.
    """
    if transcript_messages_path:
        return _refuse(
            "seal.path_rejected",
            "transcript_messages_path is not a seal input",
        )
    if not session_id:
        if not opened_at:
            return _refuse("seal.missing_start", "conversation start is absent")
        session_id = derive_anchored_session_id(
            seat_slug, opened_at, conversation_uuid
        )
    close_args: dict[str, Any] = {
        "session_id": session_id,
        "agent": agent,
        "session_summary_md": structural_md,
        "summary": summary,
        "transcript_messages": envelope,
        "transcript_depth": "verbatim",
        "entity_ids": [f"agent-bus:{thread}"] if thread else [],
        "closed_by": "succession",
        "succession_seal_authority": True,
    }
    if assistant_label is not None:
        close_args["assistant_label"] = assistant_label
    if attach_conversation_uuid and conversation_uuid:
        close_args["conversation_uuid"] = conversation_uuid
    close = await cortex_dispatch_fn("session_close", close_args)
    if not isinstance(close, dict):
        return _refuse(
            "seal.transport", "session_close returned non-object", session_id
        )
    if close.get("error") and not close.get("already_closed"):
        return SealResult(
            close_args=close_args,
            close=close,
            refused={
                "code": str(close.get("reason") or close.get("code") or "seal.refused"),
                "message": str(close.get("error")),
            },
            session_id=session_id,
        )
    # FastAPI ``{"detail": ...}`` and other bodies with no journal id are not
    # a successful close. Treating them as success stored journal_row_id None.
    if close.get("journal_row_id") is None and not close.get("already_closed"):
        return _refuse("seal.transport", "session_close returned no journal row", session_id)
    return SealResult(
        close_args=close_args,
        close=close,
        refused=None,
        session_id=str(close.get("session_id") or session_id),
    )
