"""Cross-host operator CSE identity resolution via Jupiter active-work HTTP.

Liaison and cursor-auto seats on io resolve operator/mission CSE rows from
``GET /v1/project-ask/active-work`` ``seat_rows`` (includes dormant seats)
before falling back to the host-local registry file that only cdp_ask populates.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from urllib.parse import quote

from claude_bundles.cdp_registry import chat_url_for_registration, list_active
from claude_bundles.cdp_registry_store import load_active
from claude_bundles.what_is_running_view import OPERATOR_PURPOSES

from cdp_ask.client import CdpAskClient, CdpAskClientError
from cdp_ask.lane_snapshot import read_cdp_lane_snapshot

_DEFAULT_TIMEOUT_S = 10.0
_LaneSnapshotGetter = Callable[[], dict[str, Any]]


def _null_identity() -> dict[str, str | None]:
    return {"chat_url": None, "registration_id": None, "source": None}


def _started_at_float(raw: Any) -> float:
    try:
        return float(raw) if raw is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _mission_kind(raw: Any) -> str:
    return str(raw or "root").strip().lower() or "root"


def _select_registration_id(
    candidates: list[tuple[str, str, float]],
) -> str | None:
    """Pick one registration: sole hop wins; else newest ``started_at``."""
    if not candidates:
        return None
    hop_rows = [row for row in candidates if row[1] == "hop"]
    if len(hop_rows) == 1:
        return hop_rows[0][0]
    return max(candidates, key=lambda row: row[2])[0]


def _chat_url_for_registration(registration_id: str) -> str | None:
    return (chat_url_for_registration(registration_id) or "").strip() or None


def _provenance_get(
    registration_id: str,
    *,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
    client: CdpAskClient | None = None,
) -> dict[str, Any] | None:
    """Best-effort GET /v1/cse-session/provenance when identity matches."""
    rid = (registration_id or "").strip()
    if not rid:
        return None
    try:
        http = client or CdpAskClient(timeout_s=timeout_s)
        path = f"/v1/cse-session/provenance?registration_id={quote(rid, safe='')}"
        data = http._request("GET", path)
        if not isinstance(data, dict):
            return None
        resp_reg = str(data.get("registration_id") or "").strip()
        if resp_reg != rid:
            return None
        return data
    except (CdpAskClientError, OSError, ValueError, TypeError):
        return None


def _chat_url_from_provenance(
    registration_id: str,
    *,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
    client: CdpAskClient | None = None,
) -> str | None:
    data = _provenance_get(registration_id, timeout_s=timeout_s, client=client)
    if data is None:
        return None
    return str(data.get("chat_url") or "").strip() or None


def registration_resolvable_via_provenance(
    registration_id: str,
    *,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
    client: CdpAskClient | None = None,
) -> bool:
    """True when remote provenance confirms *registration_id* (GIW listable leg)."""
    return _provenance_get(registration_id, timeout_s=timeout_s, client=client) is not None


def _candidates_from_seat_rows(
    seat_rows: list[Any],
    parent_thread: str,
    purposes: frozenset[str],
) -> list[tuple[str, str, float]]:
    parent = parent_thread.strip()
    out: list[tuple[str, str, float]] = []
    for row in seat_rows:
        if not isinstance(row, dict):
            continue
        purpose = str(row.get("purpose") or "").strip()
        if purpose not in purposes:
            continue
        if str(row.get("parent_thread") or "").strip() != parent:
            continue
        reg_id = str(row.get("registration_id") or "").strip()
        if not reg_id:
            continue
        out.append(
            (
                reg_id,
                _mission_kind(row.get("mission_kind")),
                _started_at_float(row.get("started_at")),
            )
        )
    return out


def _resolve_from_http(
    parent_thread: str,
    purposes: frozenset[str],
    *,
    get_lane_snapshot: _LaneSnapshotGetter,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
    client: CdpAskClient | None = None,
) -> dict[str, str | None]:
    try:
        snap = get_lane_snapshot()
    except (CdpAskClientError, OSError, ValueError):
        return _null_identity()
    if not isinstance(snap, dict):
        return _null_identity()
    seat_rows = snap.get("seat_rows")
    if not isinstance(seat_rows, list):
        return _null_identity()
    candidates = _candidates_from_seat_rows(seat_rows, parent_thread, purposes)
    reg_id = _select_registration_id(candidates)
    if not reg_id:
        return _null_identity()
    row_chat = None
    for row in seat_rows:
        if isinstance(row, dict) and str(row.get("registration_id") or "").strip() == reg_id:
            row_chat = str(row.get("chat_url") or "").strip() or None
            break
    chat_url = (
        row_chat
        or _chat_url_from_provenance(reg_id, timeout_s=timeout_s, client=client)
        or _chat_url_for_registration(reg_id)
    )
    return {"chat_url": chat_url, "registration_id": reg_id, "source": "http"}


def _resolve_from_local(
    parent_thread: str,
    purposes: frozenset[str],
) -> dict[str, str | None]:
    parent = parent_thread.strip()
    active = load_active()
    candidates: list[tuple[str, str, float]] = []
    for reg in list_active():
        purpose = (reg.purpose or "").strip()
        if purpose not in purposes:
            continue
        if str(reg.parent_thread or "").strip() != parent:
            continue
        raw = active.get(reg.registration_id) or {}
        candidates.append(
            (
                reg.registration_id,
                _mission_kind(reg.mission_kind),
                _started_at_float(raw.get("started_at")),
            )
        )
    reg_id = _select_registration_id(candidates)
    if not reg_id:
        return _null_identity()
    chat_url = _chat_url_for_registration(reg_id)
    return {"chat_url": chat_url, "registration_id": reg_id, "source": "local"}


def resolve_operator_seat(
    parent_thread: str,
    *,
    purposes: frozenset[str] = OPERATOR_PURPOSES,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
    get_lane_snapshot: _LaneSnapshotGetter | None = None,
) -> dict[str, str | None]:
    """Resolve operator CSE ``{chat_url, registration_id, source}`` for *parent_thread* via HTTP then local registry."""
    parent = str(parent_thread or "").strip()
    if not parent:
        return _null_identity()

    client: CdpAskClient | None = None
    if get_lane_snapshot is None:
        client = CdpAskClient(timeout_s=timeout_s)

        def _default_getter() -> dict[str, Any]:
            return read_cdp_lane_snapshot(client=client)

        getter = _default_getter
    else:
        getter = get_lane_snapshot

    http_result = _resolve_from_http(
        parent,
        purposes,
        get_lane_snapshot=getter,
        timeout_s=timeout_s,
        client=client,
    )
    if http_result.get("registration_id"):
        return http_result
    return _resolve_from_local(parent, purposes)


__all__ = ["registration_resolvable_via_provenance", "resolve_operator_seat"]
