"""In-process immutable TTL proposal store for life intent (F-6)."""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

PROPOSAL_TTL_SECONDS = 900
PROPOSAL_KIND_LIFE_INTENT = "life-intent"
_STATUS_OPEN = "open"
_STATUS_COMMITTED = "committed"


@dataclass(frozen=True)
class StoredProposal:
    proposal_id: str
    normalized_intent: dict[str, Any]
    work_order: str
    verb: str
    lane: str
    status: str
    created_at: datetime
    expires_at: datetime
    kind: str = PROPOSAL_KIND_LIFE_INTENT


_store: dict[str, StoredProposal] = {}
_store_lock = threading.Lock()


def _now() -> datetime:
    return datetime.now(UTC)


def _purge_expired() -> None:
    now = _now()
    expired = [pid for pid, row in _store.items() if row.expires_at <= now]
    for pid in expired:
        if _store.get(pid) and _store[pid].status == _STATUS_OPEN:
            del _store[pid]


def create_proposal(
    *,
    normalized_intent: dict[str, Any],
    work_order: str,
    verb: str,
    lane: str,
) -> str:
    """Mint a short-lived proposal; returns server-generated id."""
    _purge_expired()
    proposal_id = str(uuid.uuid4())
    now = _now()
    _store[proposal_id] = StoredProposal(
        proposal_id=proposal_id,
        normalized_intent=dict(normalized_intent),
        work_order=work_order,
        verb=verb,
        lane=lane,
        status=_STATUS_OPEN,
        created_at=now,
        expires_at=now + timedelta(seconds=PROPOSAL_TTL_SECONDS),
        kind=PROPOSAL_KIND_LIFE_INTENT,
    )
    return proposal_id


def get_proposal(proposal_id: str) -> StoredProposal | None:
    _purge_expired()
    return _store.get(proposal_id)


def is_expired(row: StoredProposal) -> bool:
    return row.expires_at <= _now()


def commit_reject_code(row: StoredProposal | None) -> str | None:
    if row is None:
        return "foreign_proposal_kind"
    if row.kind != PROPOSAL_KIND_LIFE_INTENT:
        return "foreign_proposal_kind"
    if row.status == _STATUS_COMMITTED:
        return "proposal_already_committed"
    if is_expired(row):
        return "proposal_expired"
    if row.status != _STATUS_OPEN:
        return "proposal_not_committable"
    return None


def claim_proposal(proposal_id: str) -> tuple[StoredProposal | None, str | None]:
    """Atomically validate kind/state and mark committed; one winner under concurrency."""
    with _store_lock:
        row = _store.get(proposal_id)
        code = commit_reject_code(row)
        if code:
            return None, code
        assert row is not None
        _store[proposal_id] = StoredProposal(
            proposal_id=row.proposal_id,
            normalized_intent=row.normalized_intent,
            work_order=row.work_order,
            verb=row.verb,
            lane=row.lane,
            status=_STATUS_COMMITTED,
            created_at=row.created_at,
            expires_at=row.expires_at,
            kind=row.kind,
        )
        return row, None


def mark_committed(proposal_id: str) -> bool:
    with _store_lock:
        row = _store.get(proposal_id)
        if row is None or row.kind != PROPOSAL_KIND_LIFE_INTENT:
            return False
        if row.status != _STATUS_OPEN or is_expired(row):
            return False
        _store[proposal_id] = StoredProposal(
            proposal_id=row.proposal_id,
            normalized_intent=row.normalized_intent,
            work_order=row.work_order,
            verb=row.verb,
            lane=row.lane,
            status=_STATUS_COMMITTED,
            created_at=row.created_at,
            expires_at=row.expires_at,
            kind=row.kind,
        )
        return True


def seed_proposal(
    *,
    proposal_id: str,
    normalized_intent: dict[str, Any],
    work_order: str = "test",
    verb: str = "investigate",
    lane: str = "recon",
    status: str = _STATUS_OPEN,
    kind: str = PROPOSAL_KIND_LIFE_INTENT,
) -> None:
    """Test helper — insert a proposal row with explicit kind/status."""
    now = _now()
    _store[proposal_id] = StoredProposal(
        proposal_id=proposal_id,
        normalized_intent=dict(normalized_intent),
        work_order=work_order,
        verb=verb,
        lane=lane,
        status=status,
        created_at=now,
        expires_at=now + timedelta(seconds=PROPOSAL_TTL_SECONDS),
        kind=kind,
    )


def clear_store() -> None:
    """Test helper — reset in-process store."""
    _store.clear()
