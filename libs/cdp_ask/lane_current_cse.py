"""Live lane-current CSE identity from open CDP pages.

Registry and thread pointers lag the operator's open Maestro tab. This module
computes the current page for one parent thread from Chrome's ``/json/list``
plus a liveness probe, and never raises into the caller.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from typing import Any

from claude_bundles import cdp_orphans, cdp_registry
from claude_bundles.cdp_orphan_cse_classify import cse_pages_from_list
from claude_bundles.cse_idle_probe import probe_page_liveness_sync
from claude_bundles.cse_url import normalize_cse_url

from cdp_ask.lane_current_cse_cap import (
    PageProbe,
    PurposeFor,
    _harvested_holder,
    _holder_view,
    _iso_utc,
    _none_body,
    collect_claimed_pages,
    finalize_lane_current_body,
    index_operator_registrations,
    select_lane_current,
)

ProvenanceFor = Callable[[str], dict[str, Any] | None]
ListPages = Callable[[], Iterator[tuple[int, str, str | None]]]


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
    include_superseded: bool = False,
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
            include_superseded=include_superseded,
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
    include_superseded: bool = False,
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

    active = list_active()
    seat_rid_key = str((seat or {}).get("registration_id") or "").strip() or None
    has_open_seat = bool(seat_rid_key) or any(
        getattr(row, "seat_lane", None) == lane
        and getattr(row, "seat_closed_at", None) is None
        for row in active
        if str(getattr(row, "parent_thread", "") or "").strip() == lane
    )
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
    (
        live_registry_by_url,
        superseded_registry_by_url,
        superseded_reg_ids,
    ) = index_operator_registrations(
        lane=lane,
        active=active,
        seat_rid_key=seat_rid_key,
        has_open_seat=has_open_seat,
        chat_url_for_registration=chat_url_for_registration,
        purpose_for_registration=purpose_for_registration,
    )
    claimed, unclaimed = collect_claimed_pages(
        lane=lane,
        grouped=grouped,
        holder_key=holder_key,
        holder_live=holder_live,
        live_registry_by_url=live_registry_by_url,
        superseded_registry_by_url=superseded_registry_by_url,
        provenance_for=provenance_for,
        purpose_for_registration=purpose_for_registration,
        probe_page=probe_page,
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
                page_stale_reason = (
                    "superseded_seat"
                    if "superseded_registry_row" in (page.get("claims") or [])
                    else stale_reason
                )
                stale.append({**page, "stale_reason": page_stale_reason})

    for page in claimed:
        if "superseded_registry_row" not in (page.get("claims") or []):
            continue
        for prid in page.get("registration_ids") or []:
            s = str(prid or "").strip()
            if s:
                superseded_reg_ids.add(s)

    superseded_count = len(superseded_reg_ids)

    body: dict[str, Any] = {
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
    return finalize_lane_current_body(
        body,
        claimed,
        superseded_count,
        include_superseded=include_superseded,
    )
