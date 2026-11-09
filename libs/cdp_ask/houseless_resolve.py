"""Houseless CSE mailbox resolution via Jupiter cdp_ask provenance (not local GIW registry)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from claude_bundles.cdp_registry.models import _HOST_LISTABLE_STATUSES
from claude_bundles.what_is_running_view import is_houseless_purpose

from cdp_ask.client import CdpAskClient, CdpAskClientError

_DEFAULT_TIMEOUT_S = 30.0


@dataclass(frozen=True)
class HouselessResolution:
    ok: bool
    reason: str
    chat_url: str | None = None


def _provenance_payload(
    registration_id: str,
    *,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
    client: CdpAskClient | None = None,
) -> dict[str, Any]:
    """GET /v1/cse-session/provenance; identity mismatch or bad shape → ``None``."""
    rid = (registration_id or "").strip()
    if not rid:
        return None  # type: ignore[return-value]
    http = client or CdpAskClient(timeout_s=timeout_s)
    path = f"/v1/cse-session/provenance?registration_id={quote(rid, safe='')}"
    data = http._request("GET", path)
    if not isinstance(data, dict):
        return None  # type: ignore[return-value]
    resp_reg = str(data.get("registration_id") or "").strip()
    if resp_reg != rid:
        return None  # type: ignore[return-value]
    return data


def resolve_houseless_target(
    registration_id: str,
    *,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
    client: CdpAskClient | None = None,
) -> HouselessResolution:
    """Resolve a houseless mailbox target from authoritative Jupiter provenance."""
    rid = (registration_id or "").strip()
    if not rid:
        return HouselessResolution(ok=False, reason="no_row")
    try:
        data = _provenance_payload(rid, timeout_s=timeout_s, client=client)
    except (CdpAskClientError, OSError, ValueError, TypeError):
        return HouselessResolution(ok=False, reason="cdp_ask_unreachable")
    if not data:
        return HouselessResolution(ok=False, reason="no_row")
    if "purpose" not in data:
        return HouselessResolution(ok=False, reason="purpose_not_houseless")
    if not is_houseless_purpose(data.get("purpose")):
        return HouselessResolution(ok=False, reason="purpose_not_houseless")
    status = str(data.get("status") or "")
    if status not in _HOST_LISTABLE_STATUSES:
        return HouselessResolution(ok=False, reason="status_unlisted")
    if bool(data.get("seat_open")):
        return HouselessResolution(ok=False, reason="seat_open")
    chat_url = str(data.get("chat_url") or "").strip() or None
    if not chat_url:
        return HouselessResolution(ok=False, reason="no_chat_url")
    return HouselessResolution(ok=True, reason="ok", chat_url=chat_url)


__all__ = ["HouselessResolution", "resolve_houseless_target"]
