"""Hop row lane-claim provenance episode, written before the CSE URL is published.

The Jupiter registry journal (``registry.jsonl``) is the authority.  The hop
birth-claim episode must land in that journal before ``attachment_observed`` is
appended and folded, because the fold publishes ``chat_url`` on the active row
that the hub poll reads when stamping the seat.  Recovery: the end-of-run
``bind_session_address`` idempotent path restates the same binding fields when
no proven tail already owns them.

Lock rule: perform claim work inside one ``ports_lock`` span.  Never call
``has_attachment_observed``, ``append_attachment_journal``, or
``fold_attachment_journal`` while holding that lock.
"""

from __future__ import annotations

from typing import Any

from universal_protocol.errors import ProtocolError

from claude_bundles import cdp_registry_store as _store
from claude_bundles.cse_url import (
    BINDABLE_SEAT_KINDS,
    claude_seat_identity_key,
    parse_claude_seat_address,
)

from .attachment_journal import assert_attachment_unique
from .session_address import _append_lane_less_episode

RECORDED = "recorded"
NOT_SEAT_ADDRESS = "not_seat_address"
MISSING_ROW = "missing_row"
NOT_HOP_CLAIM = "not_hop_claim"
NO_PORT = "no_port"
FOREIGN_URL = "foreign_url"
CONFLICT = "conflict"


def is_hop_claim_row(row: dict[str, Any]) -> bool:
    """Return whether ``row`` is a hop seat carrying a parent lane thread claim."""
    kind = str(row.get("mission_kind") or "").strip().lower()
    lane = str(row.get("parent_thread") or "").strip()
    return kind == "hop" and bool(lane)


def record_hop_birth_claim(
    registration_id: str,
    *,
    chat_url: str,
    execution_id: str | None,
) -> str:
    """Append the hop lane-claim episode for ``registration_id`` when eligible.

    Returns one of the module ``RECORDED`` / refusal constants; only
    ``attachment.conflict`` is swallowed as ``CONFLICT``.
    """
    parsed = parse_claude_seat_address((chat_url or "").strip())
    if parsed is None or parsed.kind not in BINDABLE_SEAT_KINDS:
        return NOT_SEAT_ADDRESS
    url = parsed.canonical_url
    with _store.ports_lock():
        active = _store.load_active()
        row = active.get(registration_id)
        if not isinstance(row, dict):
            return MISSING_ROW
        if not is_hop_claim_row(row):
            return NOT_HOP_CLAIM
        if not isinstance(row.get("port"), int):
            return NO_PORT
        bound = str(row.get("chat_url") or "").strip()
        if bound and claude_seat_identity_key(bound) != url:
            return FOREIGN_URL
        try:
            assert_attachment_unique(active, url, registration_id=registration_id)
        except ProtocolError as exc:
            if exc.code != "attachment.conflict":
                raise
            return CONFLICT
        _append_lane_less_episode(
            url=url,
            registration_id=registration_id,
            updated=row,
            execution_id=execution_id or None,
        )
    return RECORDED


def proven_tail_owns_binding(
    row: dict[str, Any],
    *,
    chat_url: str,
    registration_id: str,
    execution_id: str | None,
) -> bool:
    """Return whether the URL-latest proven episode already owns this hop bind."""
    if not is_hop_claim_row(row):
        return False
    from claude_bundles.cse_provenance import latest_episode

    prior = latest_episode(chat_url=chat_url)
    return (
        prior is not None
        and prior.lineage_state == "proven"
        and prior.registration_id == registration_id
        and prior.correlation_id == (execution_id or None)
    )
