"""Hub-side provenance enrichment — sole writer of ``lineage_state=proven``."""

from __future__ import annotations

from typing import Any

from claude_bundles.cse_provenance import (
    ProvenanceEpisode,
    append_episode,
    latest_episode,
)
from claude_bundles.cse_url import normalize_cse_url

from .cse_lineage_reader import LaneLineageUnreachable, read_lane_lineage


def _append_unresolved_overlay(
    prior: ProvenanceEpisode,
    *,
    reason: str,
) -> ProvenanceEpisode:
    from claude_bundles import cdp_registry_events

    episode = append_episode(
        chat_url=prior.chat_url,
        registration_id=prior.registration_id,
        cdp_url=prior.cdp_url,
        lane_thread=prior.lane_thread,
        lineage_state="unresolved",
        reason=reason,
        correlation_id=prior.correlation_id,
        evidence_class=prior.evidence_class,
        attribution_source="cse-provenance-enrich",
        state=prior.state,
    )
    cdp_registry_events.emit(
        cdp_registry_events.cdp_provenance_unresolved(
            chat_url=prior.chat_url,
            reason=reason,
            correlation_id=prior.correlation_id,
        )
    )
    return episode


def enrich_request_provenance(
    *,
    lane_thread: str,
    chat_url: str | None = None,
    registration_id: str | None = None,
    lineage_reader: Any | None = None,
) -> dict[str, Any]:
    """Append a supersede-linked enrichment episode when bus lineage is provable.

    This module is the only production writer of ``lineage_state=proven``.
    Prior journal bytes remain immutable; an overlay appends whenever it
    differs from the latest episode for the URL — it always does after a
    registry bind, because ``attribution_source`` alone distinguishes it.  A
    repeat of an identical overlay (same lineage, same reason) is a
    re-observation and writes nothing (``cse_provenance.append_episode``).
    """
    if not lane_thread or not chat_url:
        return {"ok": False, "reason": "insufficient_identity"}
    normalized = normalize_cse_url(chat_url)
    if not normalized or "/cowork/cse_" not in normalized:
        return {"ok": False, "reason": "insufficient_identity"}

    prior = latest_episode(chat_url=chat_url, registration_id=registration_id)
    if prior is None:
        return {"ok": False, "reason": "no_episode"}

    reader = lineage_reader or read_lane_lineage
    try:
        lineage = reader(lane_thread)
    except LaneLineageUnreachable:
        _append_unresolved_overlay(
            prior,
            reason="lane_lineage_unreachable",
        )
        return {"ok": False, "reason": "lane_lineage_unreachable"}

    if lineage is None or lineage.get("state") in {None, "none"}:
        _append_unresolved_overlay(
            prior,
            reason="lane_lineage_none",
        )
        return {"ok": False, "reason": "lane_lineage_none"}

    if lineage.get("state") != "associated":
        _append_unresolved_overlay(
            prior,
            reason="lane_lineage_none",
        )
        return {"ok": False, "reason": "lane_lineage_none"}

    association_id = lineage.get("association_id")
    if association_id is None:
        _append_unresolved_overlay(
            prior,
            reason="lane_lineage_none",
        )
        return {"ok": False, "reason": "lane_lineage_none"}
    association_id = int(association_id)

    episode = append_episode(
        chat_url=prior.chat_url,
        registration_id=prior.registration_id,
        cdp_url=prior.cdp_url,
        lane_thread=lane_thread,
        lineage={
            "parent_thread": lineage.get("parent_thread"),
            "lane_role": lineage.get("lane_role"),
        },
        association_id=association_id,
        lineage_state="proven",
        lineage_observed_at=lineage.get("lineage_observed_at"),
        correlation_id=prior.correlation_id,
        evidence_class=prior.evidence_class,
        attribution_source="cse-provenance-enrich",
        state="current",
        reason="bus_lane_current",
    )
    return {
        "ok": True,
        "episode_id": episode.episode_id,
        "lineage_state": "proven",
        "association_id": association_id,
        "state": "current",
    }
