"""Purpose-filtered CSE URL conflict sweep for attended resolution."""

from __future__ import annotations

from typing import Any

from claude_bundles import cdp_registry
from claude_bundles.cse_provenance import resolve as resolve_provenance
from claude_bundles.cse_provenance_resolve import is_host_listable
from claude_bundles.cse_url import normalize_cse_url
from claude_bundles.operator_proxy_mission import OPERATOR_PROXY_MISSION_PURPOSES


def purpose_filtered_url_conflicts() -> list[dict[str, Any]]:
    """Return every normalized URL held by two or more mission-purpose registrations."""
    by_url: dict[str, list[dict[str, Any]]] = {}
    for lane in cdp_registry.list_active():
        purpose = (lane.purpose or "").strip()
        if purpose not in OPERATOR_PROXY_MISSION_PURPOSES:
            continue
        chat_url = cdp_registry.chat_url_for_registration(lane.registration_id)
        if not chat_url:
            continue
        norm = normalize_cse_url(chat_url)
        by_url.setdefault(norm, []).append(
            {
                "registration_id": lane.registration_id,
                "cdp_url": lane.cdp_url,
                "chat_url": chat_url,
                "purpose": purpose,
                "provenance": resolve_provenance(
                    chat_url=chat_url,
                    registration_id=lane.registration_id,
                    host_listable=is_host_listable,
                ),
            }
        )
    conflicts: list[dict[str, Any]] = []
    for url, candidates in by_url.items():
        if len(candidates) < 2:
            continue
        resolved = resolve_provenance(
            chat_url=url,
            host_listable=is_host_listable,
        )
        conflicts.append(
            {
                "chat_url": url,
                "candidate_count": len(candidates),
                "candidates": candidates,
                "provenance": resolved,
            }
        )
    return conflicts


def shared_url_candidates(chat_url: str) -> list[dict[str, Any]] | None:
    """Return full candidate evidence when a URL maps to multiple mission hosts."""
    norm = normalize_cse_url(chat_url)
    matches: list[dict[str, Any]] = []
    for lane in cdp_registry.list_active():
        purpose = (lane.purpose or "").strip()
        if purpose not in OPERATOR_PROXY_MISSION_PURPOSES:
            continue
        bound = cdp_registry.chat_url_for_registration(lane.registration_id)
        if not bound or normalize_cse_url(bound) != norm:
            continue
        matches.append(
            {
                "registration_id": lane.registration_id,
                "cdp_url": lane.cdp_url,
                "chat_url": bound,
                "purpose": purpose,
                "provenance": resolve_provenance(
                    chat_url=bound,
                    registration_id=lane.registration_id,
                    host_listable=is_host_listable,
                ),
            }
        )
    return matches if len(matches) > 1 else None
