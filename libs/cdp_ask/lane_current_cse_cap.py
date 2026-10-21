"""Wire-size cap and shared helpers for lane-current CSE resolution."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from claude_bundles.cse_idle_probe import in_flight_from_state
from claude_bundles.cse_url import normalize_cse_url
from claude_bundles.what_is_running_view import OPERATOR_PURPOSES

PageProbe = Callable[[int, str], tuple[dict[str, Any] | None, bool]]
PurposeFor = Callable[[str], str | None]

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


def _none_body(
    lane: str,
    now_s: float,
    *,
    reason: str,
    superseded_count: int = 0,
) -> dict[str, Any]:
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
        "superseded_count": int(superseded_count),
        "as_of": _iso_utc(now_s),
        "source": "cdp_live_probe",
        "scope": f"parent_thread:{lane}",
    }


def _holder_view(
    snap: dict[str, Any] | None,
    lane: str,
    chat_url_for_registration: Any,
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


def _wire_bytes(b: dict[str, Any]) -> int:
    return len(
        json.dumps({**b, "code": "lane_cse_ambiguous"}).encode("utf-8")
    )


def _compact_candidate(c: dict[str, Any]) -> dict[str, Any]:
    return {
        "chat_url": c.get("chat_url"),
        "in_flight": c.get("in_flight"),
        "claims": c.get("claims"),
        "registration_ids": list(c.get("registration_ids") or [])[:2],
    }


def finalize_lane_current_body(
    body: dict[str, Any],
    claimed: list[dict[str, Any]],
    superseded_count: int,
    *,
    include_superseded: bool = False,
    max_ambiguous_bytes: int = 1024,
) -> dict[str, Any]:
    """Set ``superseded_count`` and cap ambiguous 409 payloads under ``max_ambiguous_bytes``."""
    body["superseded_count"] = int(superseded_count)
    if body.get("state") != "ambiguous":
        return body
    if include_superseded:
        body["candidates"] = list(claimed)
        return body

    active_candidates = [
        page
        for page in claimed
        if "superseded_registry_row" not in (page.get("claims") or [])
        or "seat_holder" in (page.get("claims") or [])
    ]
    active_candidates.sort(
        key=lambda p: (
            0 if p.get("in_flight") is True else (1 if p.get("in_flight") is None else 2),
            0 if "registry_row" in (p.get("claims") or []) else 1,
            str(p.get("chat_url") or ""),
        )
    )
    body["candidates"] = [dict(p) for p in active_candidates]

    if _wire_bytes(body) >= max_ambiguous_bytes and body.get("unclaimed_cse_urls"):
        body["unclaimed_cse_count"] = len(body["unclaimed_cse_urls"])
        body["unclaimed_cse_urls"] = []

    if _wire_bytes(body) >= max_ambiguous_bytes:
        body["candidates"] = [_compact_candidate(c) for c in body["candidates"]]

    if _wire_bytes(body) >= max_ambiguous_bytes:
        holder = body.get("seat_holder")
        if isinstance(holder, dict):
            body["seat_holder"] = {
                "registration_id": holder.get("registration_id"),
                "chat_url": holder.get("chat_url"),
            }

    if _wire_bytes(body) >= max_ambiguous_bytes:
        body["candidates_truncated"] = True
        while body["candidates"] and _wire_bytes(body) >= max_ambiguous_bytes:
            body["candidates"].pop()

    return body


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
    """Pick current/ambiguous/none over already-claimed identities."""
    non_superseded = [
        page
        for page in claimed
        if "superseded_registry_row" not in (page.get("claims") or [])
        or "seat_holder" in (page.get("claims") or [])
    ]
    in_flight = [page for page in non_superseded if page.get("in_flight") is True]
    unknown = [page for page in non_superseded if page.get("in_flight") is None]
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
        return "ambiguous", None, "seat_holder_not_open", None
    if claimed:
        return "ambiguous", None, "no_live_signal", None
    if holder_key and not holder_live:
        return "stored", "stored_seat_holder", "seat_holder_stored", None
    return "none", None, "no_claimed_live_page", None


def index_operator_registrations(
    *,
    lane: str,
    active: list[Any],
    seat_rid_key: str | None,
    has_open_seat: bool,
    chat_url_for_registration: Callable[..., str | None],
    purpose_for_registration: PurposeFor,
) -> tuple[
    dict[str, list[str]],
    dict[str, list[str]],
    set[str],
]:
    live_registry_by_url: dict[str, list[str]] = {}
    superseded_registry_by_url: dict[str, list[str]] = {}
    superseded_reg_ids: set[str] = set()
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
        live_lane_seat = getattr(reg, "seat_closed_at", None) is None and (
            getattr(reg, "seat_lane", None) == lane
            or rid == seat_rid_key
            or not has_open_seat
        )
        if not live_lane_seat and rid:
            superseded_reg_ids.add(rid)
        if key:
            target = (
                live_registry_by_url if live_lane_seat else superseded_registry_by_url
            )
            target.setdefault(key, [])
            if rid and rid not in target[key]:
                target[key].append(rid)
    return live_registry_by_url, superseded_registry_by_url, superseded_reg_ids


def collect_claimed_pages(
    *,
    lane: str,
    grouped: dict[str, dict[str, Any]],
    holder_key: str,
    holder_live: bool,
    live_registry_by_url: dict[str, list[str]],
    superseded_registry_by_url: dict[str, list[str]],
    provenance_for: Callable[[str], dict[str, Any] | None],
    purpose_for_registration: PurposeFor,
    probe_page: PageProbe,
) -> tuple[list[dict[str, Any]], list[str]]:
    claimed: list[dict[str, Any]] = []
    unclaimed: list[str] = []
    for url in sorted(grouped):
        slot = grouped[url]
        claims: list[str] = []
        registration_ids: list[str] = []
        provenance_reason = None
        if holder_key and url == holder_key:
            claims.append("seat_holder")
        live_rids = live_registry_by_url.get(url) or []
        superseded_rids = superseded_registry_by_url.get(url) or []
        if live_rids:
            if "registry_row" not in claims:
                claims.append("registry_row")
            for rid in live_rids:
                if rid not in registration_ids:
                    registration_ids.append(rid)
        elif superseded_rids:
            if "superseded_registry_row" not in claims:
                claims.append("superseded_registry_row")
            for rid in superseded_rids:
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
    return claimed, unclaimed
