"""Resolve dormant retire targets — local store and fleet document reads."""

from __future__ import annotations

from typing import Any

from claude_bundles import cdp_registry_store as store
from claude_bundles.cse_provenance import latest_episode
from claude_bundles.cse_url import normalize_cse_url
from claude_bundles.hop_cadence_seat_snap import _active_map_from_registry_document


def fleet_active_map() -> dict[str, dict[str, Any]]:
    from claude_bundles.cdp_registry_remote_read import read_fleet_registry

    doc = read_fleet_registry()
    if doc.get("availability") != "ok":
        return {}
    raw = _active_map_from_registry_document(doc)
    return {
        rid: dict(row) for rid, row in raw.items() if isinstance(row, dict)
    }


def local_row(registration_id: str) -> dict[str, Any] | None:
    row = store.load_active().get(registration_id)
    return dict(row) if isinstance(row, dict) else None


def fleet_row(registration_id: str) -> dict[str, Any] | None:
    return fleet_active_map().get(registration_id)


def resolve_retire_target(
    *,
    registration_id: str | None = None,
    chat_url: str | None = None,
) -> tuple[str | None, str | None, dict[str, Any] | None]:
    """Return ``(registration_id, normalized chat_url, registry row snapshot)``."""
    rid = (registration_id or "").strip() or None
    url = normalize_cse_url(chat_url or "") or None

    if rid:
        row = local_row(rid) or fleet_row(rid)
        bound = normalize_cse_url(str((row or {}).get("chat_url") or "")) or url
        if not url and bound:
            url = bound
        return rid, url, row

    if not url:
        return None, None, None

    from .dormant import dormant_for_chat_url

    seat = dormant_for_chat_url(url)
    if seat is not None:
        return seat.registration_id, url, local_row(seat.registration_id) or fleet_row(
            seat.registration_id
        )

    for candidate_id, row in store.load_active().items():
        if normalize_cse_url(str(row.get("chat_url") or "")) == url:
            return candidate_id, url, dict(row)

    for candidate_id, row in fleet_active_map().items():
        if normalize_cse_url(str(row.get("chat_url") or "")) == url:
            return candidate_id, url, row

    latest = latest_episode(chat_url=url)
    if latest is not None:
        return latest.registration_id, url, local_row(latest.registration_id)

    return None, url, None
