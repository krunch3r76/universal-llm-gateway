"""Read-only drift probes for shadow seat ledger (fail-open)."""

from __future__ import annotations

from typing import Any

from claude_bundles.cdp_reclaim_refuse import cse_id_from_url
from claude_bundles.cdp_registry.session_address import chat_url_for_registration
from claude_bundles.cse_url import normalize_cse_url
from universal_logging import get_logger

logger = get_logger(__name__)


def _normalize_chat_url(raw: str | None) -> str | None:
    url = normalize_cse_url(raw or "")
    return url or None


def load_registry_active() -> dict[str, dict[str, Any]]:
    """Load CDP registry ``active.json`` once (drift summaries batch on this)."""
    from claude_bundles.cdp_registry.session_address import _store

    active = _store.load_active()
    return active if isinstance(active, dict) else {}


def registry_chat_from_active(
    active: dict[str, dict[str, Any]],
    registration_id: str | None,
) -> tuple[str, str | None, str | None]:
    """Return (probe_status, chat_url, chat_id) from a pre-loaded registry map."""
    reg = (registration_id or "").strip()
    if not reg:
        return "ok", None, None
    try:
        row = active.get(reg)
        if not isinstance(row, dict):
            return "ok", None, None
        url = _normalize_chat_url(str(row.get("chat_url") or "").strip() or None)
        chat_id = cse_id_from_url(url or "") if url else None
        return "ok", url, chat_id
    except Exception:
        logger.debug(
            "registry chat lookup failed registration_id=%s",
            reg,
            exc_info=True,
        )
        return "unavailable", None, None


def _holder_lane_key(holder: dict[str, Any]) -> str | None:
    for key in (
        "lane_thread_id",
        "holder_lane",
        "thread_id",
        "parent_thread",
    ):
        val = str(holder.get(key) or "").strip()
        if val:
            return val
    return None


def _normalize_holder_row(holder: dict[str, Any]) -> dict[str, Any]:
    chat_url = holder.get("chat_url") or holder.get("holder_chat_url")
    reg = holder.get("registration_id") or holder.get("holder_registration_id")
    out = dict(holder)
    if chat_url is not None:
        out["chat_url"] = chat_url
    if reg is not None:
        out["registration_id"] = reg
    return out


def _cse_holder_replaces_incumbent(
    incumbent: dict[str, Any], candidate: dict[str, Any]
) -> bool:
    """Prefer ``driving`` over ``dormant``; otherwise latest ``bound_at``."""
    new_state = str(candidate.get("seat_state") or "")
    old_state = str(incumbent.get("seat_state") or "")
    if new_state == "driving" and old_state != "driving":
        return True
    if old_state == "driving" and new_state != "driving":
        return False
    new_ba = str(candidate.get("bound_at") or "")
    old_ba = str(incumbent.get("bound_at") or "")
    return new_ba >= old_ba


def fetch_giw_holders_map(*, timeout: float = 5.0) -> tuple[str, dict[str, dict[str, Any]]]:
    """Single GIW HTTP fetch; map lane thread id → CSE holder row (may be partial)."""
    del timeout  # single fetch path; timeout lives on the HTTP client
    try:
        from bus_watch.roster import fetch_giw_active_work_payload

        payload = fetch_giw_active_work_payload()
        if payload is None:
            return "unavailable", {}
        raw = payload.get("cse_holders")
        if not isinstance(raw, list):
            return "ok", {}
        by_lane: dict[str, dict[str, Any]] = {}
        for holder in raw:
            if not isinstance(holder, dict):
                continue
            lane = _holder_lane_key(holder)
            if not lane:
                continue
            normalized = _normalize_holder_row(holder)
            existing = by_lane.get(lane)
            if existing is None or _cse_holder_replaces_incumbent(existing, holder):
                by_lane[lane] = normalized
        return "ok", by_lane
    except Exception:
        logger.debug("giw holders map unavailable", exc_info=True)
        return "unavailable", {}


def giw_holder_chat(
    holder: dict[str, Any] | None,
    *,
    registry_active: dict[str, dict[str, Any]] | None = None,
) -> tuple[str | None, str | None]:
    if not holder:
        return None, None
    url = _normalize_chat_url(holder.get("chat_url"))
    chat_id = cse_id_from_url(url or "") or None
    reg = str(holder.get("registration_id") or "").strip() or None
    if url is None and reg:
        if registry_active is not None:
            _status, reg_url, reg_chat_id = registry_chat_from_active(registry_active, reg)
            url = reg_url
            if reg_chat_id:
                chat_id = reg_chat_id
        else:
            url = _normalize_chat_url(chat_url_for_registration(reg))
            if url:
                chat_id = cse_id_from_url(url) or chat_id
    return url, chat_id


def probe_registry_chat(
    registration_id: str | None,
    *,
    active: dict[str, dict[str, Any]] | None = None,
) -> tuple[str, str | None, str | None]:
    """Return (probe_status, chat_url, chat_id)."""
    reg = (registration_id or "").strip()
    if not reg:
        return "ok", None, None
    if active is not None:
        return registry_chat_from_active(active, reg)
    try:
        url = _normalize_chat_url(chat_url_for_registration(reg))
        chat_id = cse_id_from_url(url or "") if url else None
        return "ok", url, chat_id
    except Exception:
        logger.debug(
            "registry chat probe unavailable registration_id=%s",
            reg,
            exc_info=True,
        )
        return "unavailable", None, None


def ledger_matches_chat(
    *,
    seat_chat_url: str | None,
    seat_chat_id: str | None,
    other_url: str | None,
    other_chat_id: str | None,
) -> bool:
    seat_url = _normalize_chat_url(seat_chat_url)
    other = _normalize_chat_url(other_url)
    if seat_url or other:
        return seat_url == other
    if seat_chat_id or other_chat_id:
        return (seat_chat_id or "") == (other_chat_id or "")
    return True
