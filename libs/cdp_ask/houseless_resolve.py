"""Houseless CSE mailbox resolution via Jupiter cdp_ask provenance (not local GIW registry)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from claude_bundles.cdp_registry.models import _HOST_LISTABLE_STATUSES
from claude_bundles.what_is_running_view import is_houseless_purpose

from cdp_ask.operator_seat_resolve import _provenance_get

_DEFAULT_TIMEOUT_S = 5.0


@dataclass(frozen=True)
class HouselessResolution:
    ok: bool
    reason: str
    chat_url: str | None = None


_RESOLVE_MEMO: dict[str, HouselessResolution] = {}


def clear_houseless_resolve_memo() -> None:
    """Drop per-scan-pass memo so a blackholed Jupiter costs one timeout per pass."""
    _RESOLVE_MEMO.clear()


def _provenance_payload(
    registration_id: str,
    *,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
    client: Any | None = None,
) -> dict[str, Any] | None:
    """Authoritative Jupiter provenance GET with identity match."""
    return _provenance_get(registration_id, timeout_s=timeout_s, client=client)


def resolve_houseless_target(
    registration_id: str,
    *,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
    client: Any | None = None,
    memo: dict[str, HouselessResolution] | None = None,
) -> HouselessResolution:
    """Resolve a houseless mailbox target from authoritative Jupiter provenance."""
    rid = (registration_id or "").strip()
    if not rid:
        return HouselessResolution(ok=False, reason="no_row")
    cache = _RESOLVE_MEMO if memo is None else memo
    if rid in cache:
        return cache[rid]
    data = _provenance_payload(rid, timeout_s=timeout_s, client=client)
    if not data:
        out = HouselessResolution(ok=False, reason="no_row")
        cache[rid] = out
        return out
    if "purpose" not in data:
        out = HouselessResolution(ok=False, reason="purpose_not_houseless")
        cache[rid] = out
        return out
    if not is_houseless_purpose(data.get("purpose")):
        out = HouselessResolution(ok=False, reason="purpose_not_houseless")
        cache[rid] = out
        return out
    status = str(data.get("status") or "")
    if status not in _HOST_LISTABLE_STATUSES:
        out = HouselessResolution(ok=False, reason="status_unlisted")
        cache[rid] = out
        return out
    if bool(data.get("seat_open")):
        out = HouselessResolution(ok=False, reason="seat_open")
        cache[rid] = out
        return out
    chat_url = str(data.get("chat_url") or "").strip() or None
    if not chat_url:
        out = HouselessResolution(ok=False, reason="no_chat_url")
        cache[rid] = out
        return out
    out = HouselessResolution(ok=True, reason="ok", chat_url=chat_url)
    cache[rid] = out
    return out


__all__ = [
    "HouselessResolution",
    "clear_houseless_resolve_memo",
    "resolve_houseless_target",
]
