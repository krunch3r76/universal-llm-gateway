"""Live lane-current CSE identity from open CDP pages.

Registry and thread pointers lag the operator's open Maestro tab. This module
computes the current page for one parent thread from Chrome's ``/json/list``
plus a liveness probe, and never raises into the caller.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from typing import Any

from claude_bundles import cdp_orphans, cdp_registry
from claude_bundles.cdp_orphan_cse_classify import cse_pages_from_list
from claude_bundles.cse_idle_probe import in_flight_from_state, probe_page_liveness_sync
from claude_bundles.cse_url import normalize_cse_url
from claude_bundles.what_is_running_view import OPERATOR_PURPOSES

PageProbe = Callable[[int, str], tuple[dict[str, Any] | None, bool]]
ProvenanceFor = Callable[[str], dict[str, Any] | None]
ListPages = Callable[[], Iterator[tuple[int, str, str | None]]]
PurposeFor = Callable[[str], str | None]


def enumerate_live_cse_pages() -> Iterator[tuple[int, str, str | None]]:
    """Yield ``(port, normalized_chat_url, websocket_url)`` for open CSE pages."""
    for port in cdp_registry.PORT_RANGE:
        try:
            version = cdp_orphans._fetch_json(f"http://127.0.0.1:{port}/json/version")
        except Exception:
            continue
        if version is None:
            continue
        try:
            listing = cdp_orphans._fetch_json(f"http://127.0.0.1:{port}/json/list")
            pages = cse_pages_from_list(listing)
        except Exception:
            continue
        for page in pages:
            try:
                url = normalize_cse_url(str(page.get("url") or ""))
            except Exception:
                continue
            if not url:
                continue
            ws = page.get("webSocketDebuggerUrl")
            ws_url = ws if isinstance(ws, str) and ws.strip() else None
            yield port, url, ws_url


def _default_provenance(chat_url: str) -> dict[str, Any] | None:
    from claude_bundles.cse_provenance import resolve as resolve_provenance
    from claude_bundles.cse_provenance_resolve import is_row_present

    return resolve_provenance(chat_url=chat_url, host_listable=is_row_present)


def _default_purpose_for() -> PurposeFor:
    from claude_bundles import cdp_registry_store as store

    rows = store.load_active()

    def purpose_for(registration_id: str) -> str | None:
        row = rows.get(registration_id)
        if not isinstance(row, dict):
            return None
        return str(row.get("purpose") or "").strip() or None

    return purpose_for


def _is_operator_purpose(purpose: Any) -> bool:
    return str(purpose or "").strip().lower() in OPERATOR_PURPOSES


def _probe_one(
    port: int,
    ws_url: str | None,
    probe_page: PageProbe,
) -> tuple[bool | None, bool]:
    if not ws_url:
        return None, False
    try:
        state, probe_ok = probe_page(port, ws_url)
    except Exception:
        return None, False
    if not probe_ok or not isinstance(state, dict):
        return None, False
    try:
        return bool(in_flight_from_state(state)), True
    except Exception:
        return None, False


def _fold_liveness(samples: list[tuple[bool | None, bool]]) -> tuple[bool | None, bool]:
    if not samples:
        return None, False
    inflight: bool | None = False
    if any(item is True for item, _ok in samples):
        inflight = True
    elif any(item is None for item, _ok in samples):
        inflight = None
    probe_ok = any(ok for _item, ok in samples)
    return inflight, probe_ok


def _page_evidence_class(
    claims: list[str],
    *,
    holder_live: bool,
) -> str:
    if "registry_row" in claims or ("seat_holder" in claims and holder_live):
        return "live_binding"
    return "stored_association"


def select_lane_current(
    claimed: list[dict[str, Any]],
    holder_url: str | None,
    holder_live: bool = False,
    *,
    attended_idle_ok: bool = False,
) -> tuple[str, str | None, str | None, dict[str, Any] | None]:
    """Pick current/ambiguous/none over already-claimed identities.

    Invariant: ``state == \"current\"`` implies
    ``current[\"evidence_class\"] == \"live_binding\"``.

    Returns ``(state, basis, reason, current_page)``.
    """
    in_flight = [page for page in claimed if page.get("in_flight") is True]
    unknown = [page for page in claimed if page.get("in_flight") is None]
    if len(in_flight) >= 2:
        return "ambiguous", None, "multiple_in_flight", None
    if len(in_flight) == 1 and len(unknown) > 0:
        return "ambiguous", None, "probe_incomplete", None
    if len(in_flight) == 1 and len(unknown) == 0:
        page = in_flight[0]
        if page.get("evidence_class") == "live_binding":
            return "current", "in_flight", None, page
        return "ambiguous", None, "stored_association_streaming", None
    holder_key = normalize_cse_url(holder_url or "")
    if any(
        "registry_row" in (page.get("claims") or [])
        and page.get("chat_url") != holder_key
        for page in unknown
    ):
        return "ambiguous", None, "probe_incomplete", None
    holder_page = next(
        (page for page in claimed if page.get("chat_url") == holder_key),
        None,
    )
    if holder_key and holder_page is not None:
        if holder_live:
            return "current", "seat_holder", None, holder_page
        # Harvested generate left the Maestro tab open and idle. That page is
        # the operator seat. A hygiene_drain provenance stays a refusal.
        if (
            attended_idle_ok
            and holder_page.get("in_flight") is False
            and holder_page.get("probe_ok") is True
            and holder_page.get("provenance_reason") != "hygiene_drain"
        ):
            page = dict(holder_page)
            page["evidence_class"] = "live_binding"
            return "current", "attended_idle", None, page
        return "ambiguous", None, "seat_holder_dormant", None
    if len(unknown) > 0:
        return "ambiguous", None, "probe_incomplete", None
    if holder_key and claimed:
        # A dormant holder is still the lane's seat; an idle open page is not
        # evidence the operator moved off it.
        return "ambiguous", None, "seat_holder_not_open", None
    if claimed:
        # An idle claim is stored association, the same evidence that named
        # the hygiene_drain page in a:37834.
        return "ambiguous", None, "no_live_signal", None
    if holder_key and not holder_live:
        # Stored seat with no open claimed page. Not a paste target and not
        # the empty-lane miss.
        return "stored", "stored_seat_holder", "seat_holder_stored", None
    return "none", None, "no_claimed_live_page", None


_HARVESTED_STATUSES = frozenset({"completed", "failed", "aborted"})


def _harvested_holder(snap: dict | None, registration_id: str | None) -> bool:
    """True when this seat's tracked generate has already reached a terminal row."""
    rid = (registration_id or "").strip()
    if not rid or not isinstance(snap, dict):
        return False
    rows = snap.get("rows")
    if not isinstance(rows, list):
        return False
    for row in rows:
        if not isinstance(row, dict):
            continue
        if str(row.get("registration_id") or "").strip() != rid:
            continue
        status = str(row.get("status") or "").strip()
        stream = str(row.get("stream_state") or "").strip()
        if status in _HARVESTED_STATUSES or stream.startswith("terminal:"):
            return True
    return False


def _iso_utc(now_s: float) -> str:
    return datetime.fromtimestamp(now_s, tz=UTC).isoformat()


def _none_body(lane: str, now_s: float, *, reason: str) -> dict[str, Any]:
    return {
        "parent_thread": lane,
        "state": "none",
        "basis": None,
        "reason": reason,
        "current": None,
        "stale": [],
        "candidates": [],
        "seat_holder": None,
        "unclaimed_cse_urls": [],
        "as_of": _iso_utc(now_s),
        "source": "cdp_live_probe",
        "scope": f"parent_thread:{lane}",
    }


def _holder_view(
    snap: dict[str, Any] | None,
    lane: str,
    chat_url_for_registration: Callable[..., str | None],
) -> dict[str, Any] | None:
    if not isinstance(snap, dict):
        return None
    try:
        from cdp_ask.lane_admission import lane_seat_holder

        holder = lane_seat_holder(snap, lane)
    except Exception:
        return None
    if not isinstance(holder, dict):
        return None
    reg_id = str(holder.get("registration_id") or "").strip() or None
    url = None
    if reg_id:
        try:
            url = chat_url_for_registration(reg_id)
        except Exception:
            url = None
    if not url:
        raw = holder.get("chat_url")
        url = str(raw).strip() if raw else None
    if not reg_id and not url:
        return None
    return {"registration_id": reg_id, "chat_url": normalize_cse_url(url or "") or url}


def resolve_lane_current_cse(
    parent_thread: str,
    *,
    snap: dict | None,
    list_pages: ListPages = enumerate_live_cse_pages,
    probe_page: PageProbe = probe_page_liveness_sync,
    provenance_for: ProvenanceFor | None = None,
    list_active: Callable[[], list[Any]] | None = None,
    chat_url_for_registration: Callable[..., str | None] | None = None,
    purpose_for_registration: PurposeFor | None = None,
    now: Callable[[], float] = time.time,
) -> dict[str, Any]:
    """Return the live CSE identity for ``parent_thread``. Never raises.

    Only operator-purpose registrations claim a page; a consult or worker
    CSE registered under the same ``parent_thread`` is reported unclaimed.
    """
    lane = (parent_thread or "").strip()
    try:
        now_s = float(now())
    except Exception:
        now_s = time.time()
    if provenance_for is None:
        provenance_for = _default_provenance
    if list_active is None:
        list_active = cdp_registry.list_active
    if chat_url_for_registration is None:
        chat_url_for_registration = cdp_registry.chat_url_for_registration
    try:
        if purpose_for_registration is None:
            purpose_for_registration = _default_purpose_for()
        return _resolve(
            lane,
            snap=snap,
            list_pages=list_pages,
            probe_page=probe_page,
            provenance_for=provenance_for,
            list_active=list_active,
            chat_url_for_registration=chat_url_for_registration,
            purpose_for_registration=purpose_for_registration,
            now_s=now_s,
        )
    except Exception:
        return _none_body(lane, now_s, reason="probe_error")


def _resolve(
    lane: str,
    *,
    snap: dict | None,
    list_pages: ListPages,
    probe_page: PageProbe,
    provenance_for: ProvenanceFor,
    list_active: Callable[[], list[Any]],
    chat_url_for_registration: Callable[..., str | None],
    purpose_for_registration: PurposeFor,
    now_s: float,
) -> dict[str, Any]:
    grouped: dict[str, dict[str, Any]] = {}
    gathered: list[Any] = []
    try:
        for item in list_pages():
            gathered.append(item)
    except Exception:
        pass
    for item in gathered:
        try:
            port, url, ws_url = item
            url = normalize_cse_url(str(url or ""))
        except Exception:
            continue
        if not url:
            continue
        slot = grouped.setdefault(
            url,
            {"chat_url": url, "ports": [], "sockets": []},
        )
        try:
            port_i = int(port)
        except (TypeError, ValueError):
            continue
        if port_i not in slot["ports"]:
            slot["ports"].append(port_i)
        slot["sockets"].append((port_i, ws_url if isinstance(ws_url, str) else None))

    seat = _holder_view(snap, lane, chat_url_for_registration)
    holder_url = (seat or {}).get("chat_url") or None
    holder_key = normalize_cse_url(holder_url or "")

    registry_by_url: dict[str, list[str]] = {}
    # An unreadable registry is a probe failure, not an empty lane.
    active = list_active()
    listable_ids = {
        str(getattr(r, "registration_id", "") or "").strip() for r in active
    } - {""}
    # A listable registration whose page is not among the open probe
    # candidates is a stored pointer, not the live holder (a:38165).
    holder_page_open = bool(holder_key and holder_key in grouped)
    holder_live = bool(
        seat
        and holder_page_open
        and str(seat.get("registration_id") or "").strip() in listable_ids
    )
    if seat is not None:
        seat = {
            **seat,
            "evidence_class": "live_binding" if holder_live else "stored_association",
        }
    for reg in active:
        try:
            parent = str(getattr(reg, "parent_thread", "") or "").strip()
            if parent != lane:
                continue
            rid = str(getattr(reg, "registration_id", "") or "").strip()
            purpose = getattr(reg, "purpose", None) or purpose_for_registration(rid)
            if not _is_operator_purpose(purpose):
                continue
            raw_url = chat_url_for_registration(rid)
            key = normalize_cse_url(str(raw_url or ""))
        except Exception:
            continue
        if key:
            registry_by_url.setdefault(key, [])
            if rid and rid not in registry_by_url[key]:
                registry_by_url[key].append(rid)

    claimed: list[dict[str, Any]] = []
    unclaimed: list[str] = []
    for url in sorted(grouped):
        slot = grouped[url]
        claims: list[str] = []
        registration_ids: list[str] = []
        provenance_reason = None
        if holder_key and url == holder_key:
            claims.append("seat_holder")
        for rid in registry_by_url.get(url, []):
            if "registry_row" not in claims:
                claims.append("registry_row")
            if rid not in registration_ids:
                registration_ids.append(rid)
        try:
            prov = provenance_for(url)
        except Exception:
            prov = None
        prid = ""
        if isinstance(prov, dict):
            prid = str(prov.get("registration_id") or "").strip()
        try:
            prov_purpose = purpose_for_registration(prid) if prid else None
        except Exception:
            prov_purpose = None
        if (
            isinstance(prov, dict)
            and str(prov.get("parent_thread_claim") or "") == lane
            and _is_operator_purpose(prov_purpose)
        ):
            claims.append("provenance_claim")
            provenance_reason = prov.get("reason")
            if prid not in registration_ids:
                registration_ids.append(prid)
        if not claims:
            unclaimed.append(url)
            continue
        samples = [_probe_one(port, ws, probe_page) for port, ws in slot["sockets"]]
        in_flight, probe_ok = _fold_liveness(samples)
        evidence_class = _page_evidence_class(claims, holder_live=holder_live)
        claimed.append(
            {
                "chat_url": url,
                "ports": sorted(slot["ports"]),
                "in_flight": in_flight,
                "probe_ok": probe_ok,
                "claims": claims,
                "registration_ids": registration_ids,
                "provenance_reason": provenance_reason,
                "evidence_class": evidence_class,
            }
        )

    # Admission ``rows`` keep only pending/running executions. A harvested
    # operator seat is absent from that list, so a terminal row cannot be the
    # idle signal. The seat registration plus an open idle page (and a
    # provenance that is not hygiene_drain) is the attestation.
    seat_rid = (seat or {}).get("registration_id") if isinstance(seat, dict) else None
    attended_idle_ok = bool(seat_rid) or _harvested_holder(snap, seat_rid)
    state, basis, reason, current = select_lane_current(
        claimed,
        holder_url,
        holder_live=holder_live,
        attended_idle_ok=attended_idle_ok,
    )
    if basis == "attended_idle" and isinstance(seat, dict):
        seat = {**seat, "evidence_class": "live_binding"}
    stale: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    if state == "current" and current is not None:
        stale_reason = (
            "not_in_flight"
            if basis == "in_flight"
            else "not_seat_holder"
            if basis in {"seat_holder", "attended_idle"}
            else None
        )
        if stale_reason is not None:
            for page in claimed:
                if page["chat_url"] == current["chat_url"]:
                    continue
                stale.append({**page, "stale_reason": stale_reason})
    elif state == "ambiguous":
        candidates = list(claimed)

    return {
        "parent_thread": lane,
        "state": state,
        "basis": basis,
        "reason": reason,
        "current": current,
        "stale": stale,
        "candidates": candidates,
        "seat_holder": seat,
        "unclaimed_cse_urls": sorted(unclaimed),
        "as_of": _iso_utc(now_s),
        "source": "cdp_live_probe",
        "scope": f"parent_thread:{lane}",
    }
