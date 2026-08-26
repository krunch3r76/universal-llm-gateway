"""CSE chat URL stash/stamp helpers for :class:`~.cdp.CdpFold`.

Bind signals ``mcp.agentbus.thread.cse.bound`` and ``cdp.provenance.bound`` never
open a CDP leg — they join on ``thread_id`` / ``lane_thread`` and paint ``url=``
when a ``cdp.generate.admitted`` row exists (or stash until one arrives).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..protocols import EventRecord

if TYPE_CHECKING:
    from .cdp import CdpFold, CdpState


def normalize_chat_url(url: str | None) -> str | None:
    """Strip scheme/trailing slash; return None when empty or not a CSE URL."""
    if not url or not str(url).strip():
        return None
    value = str(url).strip()
    if value.startswith("https://"):
        value = value[8:]
    elif value.startswith("http://"):
        value = value[7:]
    value = value.rstrip("/")
    lower = value.lower()
    if "cowork/cse_" in lower or "/cse_" in lower:
        return value
    return None


def _thread_id_from_payload(payload: dict[str, Any]) -> str | None:
    for key in ("thread_id", "lane_thread"):
        value = payload.get(key)
        if value:
            return str(value)
    return None


def _chat_url_from_payload(payload: dict[str, Any]) -> str | None:
    for key in ("cse_chat_url", "chat_url"):
        value = payload.get(key)
        normalized = normalize_chat_url(str(value) if value else None)
        if normalized:
            return normalized
    return None


def _leg_by_thread(fold: CdpFold, thread_id: str) -> CdpState | None:
    for row in fold.legs.values():
        if row.thread_id == thread_id:
            return row
    return None


def apply_pending_chat(fold: CdpFold, row: CdpState) -> None:
    """Apply stashed chat URL to ``row`` — first-writer-wins on ``chat_url``."""
    if row.chat_url is not None:
        return
    thread_id = row.thread_id
    if not thread_id:
        return
    pending = fold._pending_chat.get(thread_id)
    if pending:
        row.chat_url = pending


def stash_or_stamp_chat(fold: CdpFold, record: EventRecord) -> None:
    """Stamp an existing leg or stash chat URL until ``admitted`` opens one."""
    payload = record.payload
    thread_id = _thread_id_from_payload(payload)
    chat_url = _chat_url_from_payload(payload)
    if not thread_id or not chat_url:
        return
    existing = _leg_by_thread(fold, thread_id)
    if existing is not None:
        if existing.chat_url is None:
            existing.chat_url = chat_url
        return
    if thread_id not in fold._pending_chat:
        fold._pending_chat[thread_id] = chat_url
