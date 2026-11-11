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


def probe_giw_holder(lane_thread_id: str, *, timeout: float = 5.0) -> tuple[str, dict[str, Any] | None]:
    """Return (probe_status, holder) where status is ``ok`` or ``unavailable``."""
    try:
        from giw_endpoint import git_integration_worker_base_url
        from transport_utils import make_sync_client

        base = git_integration_worker_base_url()
        with make_sync_client(base, timeout=timeout) as client:
            response = client.get(
                "/api/v1/git/admin/cse-holders",
                params={"lane_thread_id": lane_thread_id},
            )
        response.raise_for_status()
        payload = response.json()
        holder = payload.get("holder") if isinstance(payload, dict) else None
        if holder is not None and not isinstance(holder, dict):
            holder = None
        return "ok", holder
    except Exception:
        logger.debug(
            "giw holder probe unavailable lane_thread_id=%s",
            lane_thread_id,
            exc_info=True,
        )
        return "unavailable", None


def giw_holder_chat(holder: dict[str, Any] | None) -> tuple[str | None, str | None]:
    if not holder:
        return None, None
    url = _normalize_chat_url(holder.get("chat_url"))
    chat_id = cse_id_from_url(url or "") or None
    reg = str(holder.get("registration_id") or "").strip() or None
    if url is None and reg:
        url = _normalize_chat_url(chat_url_for_registration(reg))
        if url:
            chat_id = cse_id_from_url(url) or chat_id
    return url, chat_id


def probe_registry_chat(
    registration_id: str | None,
) -> tuple[str, str | None, str | None]:
    """Return (probe_status, chat_url, chat_id)."""
    reg = (registration_id or "").strip()
    if not reg:
        return "ok", None, None
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
