"""Hop attempt records (not wake-ledger authority — see orion_wake_owner.py)."""

from __future__ import annotations

import secrets
from dataclasses import asdict, dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class OrionHopAttempt:
    """Immutable hop attempt bound to registration and smoke receipts."""

    attempt_id: str
    identity: str
    expected_old_url: str
    intended_new_url: str
    handoff_read_sha256: str

    def normalized_old(self) -> str:
        return self.expected_old_url.split("?", 1)[0]

    def normalized_new(self) -> str:
        return self.intended_new_url.split("?", 1)[0]

    def to_dict(self) -> dict[str, str]:
        return asdict(self)

    @staticmethod
    def from_dict(raw: dict[str, Any] | None) -> OrionHopAttempt | None:
        if not raw:
            return None
        attempt_id = str(raw.get("attempt_id") or "").strip()
        identity = str(raw.get("identity") or "").strip()
        old_u = str(raw.get("expected_old_url") or "").strip()
        new_u = str(raw.get("intended_new_url") or "").strip()
        sha = str(raw.get("handoff_read_sha256") or "").strip().lower()
        if not attempt_id or not identity or not old_u or not new_u or len(sha) != 64:
            return None
        return OrionHopAttempt(
            attempt_id=attempt_id,
            identity=identity,
            expected_old_url=old_u,
            intended_new_url=new_u,
            handoff_read_sha256=sha,
        )


def new_attempt_id() -> str:
    return secrets.token_hex(16)


class HopAttemptStore(Protocol):
    """Optional in-process hop metadata (attempt + smoke), not durable wake state."""

    def save_pending_attempt(self, attempt: OrionHopAttempt) -> None: ...

    def load_pending_attempt(self) -> OrionHopAttempt | None: ...

    def clear_pending_attempt(self, attempt_id: str) -> None: ...

    def save_smoke_receipt(self, attempt_id: str, receipt: dict[str, Any]) -> None: ...

    def load_smoke_receipt(self, attempt_id: str) -> dict[str, Any] | None: ...


class InMemoryHopAttemptStore:
    """Tests and offline hop flows only."""

    def __init__(self) -> None:
        self._pending: OrionHopAttempt | None = None
        self._smoke: dict[str, dict[str, Any]] = {}

    def save_pending_attempt(self, attempt: OrionHopAttempt) -> None:
        self._pending = attempt

    def load_pending_attempt(self) -> OrionHopAttempt | None:
        return self._pending

    def clear_pending_attempt(self, attempt_id: str) -> None:
        if self._pending and self._pending.attempt_id == attempt_id:
            self._pending = None

    def save_smoke_receipt(self, attempt_id: str, receipt: dict[str, Any]) -> None:
        self._smoke[attempt_id] = dict(receipt)

    def load_smoke_receipt(self, attempt_id: str) -> dict[str, Any] | None:
        row = self._smoke.get(attempt_id)
        return dict(row) if row else None


class OrionHopStorage(InMemoryHopAttemptStore):
    """Compatibility alias — in-memory hop metadata only (not wake ledger storage)."""
