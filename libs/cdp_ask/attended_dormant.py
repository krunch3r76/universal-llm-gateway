"""Dormant attendance candidates — a seat is attended by URL, not by open tab.

A mission CSE keeps its identity when its Chrome process is released: the bound
``chat_url`` plus the retained profile are enough to reopen it. Reporting that
state as attended-but-dormant is what lets the fleet release idle hosts without
the resolver claiming nobody is attended.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from claude_bundles import cdp_registry
from claude_bundles.cse_provenance import resolve as resolve_provenance
from claude_bundles.cse_provenance_resolve import is_host_listable
from claude_bundles.cse_url import normalize_cse_url
from claude_bundles.operator_proxy_mission import OPERATOR_PROXY_MISSION_PURPOSES

__all__ = ["DormantCandidate", "candidate_dict", "dormant_candidates"]


@dataclass(frozen=True)
class DormantCandidate:
    """One mission-purpose CSE whose host is released but reattachable."""

    registration_id: str
    chat_url: str
    purpose: str
    dormant_at: float | None = None
    provenance: dict[str, Any] | None = None


def dormant_candidates() -> list[DormantCandidate]:
    """Mission-purpose dormant seats, one per CSE URL, newest binding first."""
    by_url: dict[str, DormantCandidate] = {}
    for seat in cdp_registry.list_dormant():
        purpose = (seat.purpose or "").strip()
        if purpose not in OPERATOR_PROXY_MISSION_PURPOSES:
            continue
        url = normalize_cse_url(seat.chat_url)
        if not url or url in by_url:
            continue
        by_url[url] = DormantCandidate(
            registration_id=seat.registration_id,
            chat_url=seat.chat_url,
            purpose=purpose,
            dormant_at=seat.dormant_at,
            provenance=resolve_provenance(
                chat_url=seat.chat_url,
                host_listable=is_host_listable,
            ),
        )
    return list(by_url.values())


def candidate_dict(candidate: DormantCandidate) -> dict[str, Any]:
    """Serialize a dormant candidate for an HTTP body or event payload."""
    return {
        "registration_id": candidate.registration_id,
        "cdp_url": None,
        "chat_url": candidate.chat_url,
        "purpose": candidate.purpose,
        "dormant_at": candidate.dormant_at,
        "provenance": candidate.provenance,
    }
