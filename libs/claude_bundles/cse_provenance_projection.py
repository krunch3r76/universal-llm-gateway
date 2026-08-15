"""Claim and proven payload projections for CSE provenance resolution."""

from __future__ import annotations

from typing import Any

from claude_bundles.cse_provenance import ProvenanceEpisode


def _episodes_by_id(episodes: list[ProvenanceEpisode]) -> dict[str, ProvenanceEpisode]:
    return {episode.episode_id: episode for episode in episodes}


def _claim_episode(
    current: ProvenanceEpisode,
    episodes: list[ProvenanceEpisode],
) -> ProvenanceEpisode:
    """Return the latest non-proven receipt in the supersede chain."""
    if current.lineage_state != "proven":
        return current
    by_id = _episodes_by_id(episodes)
    walk = current
    while walk.supersedes:
        prior = by_id.get(walk.supersedes)
        if prior is None:
            break
        if prior.lineage_state != "proven":
            return prior
        walk = prior
    return current


def _claim_projection(episode: ProvenanceEpisode) -> dict[str, Any]:
    payload: dict[str, Any] = {"claim_observed_at": episode.observed_at}
    if episode.lane_thread:
        payload["lane_thread_claim"] = episode.lane_thread
    if episode.parent_thread:
        payload["parent_thread_claim"] = episode.parent_thread
    if episode.lane_role:
        payload["lane_role_claim"] = episode.lane_role
    return payload


def _proven_projection(episode: ProvenanceEpisode) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if episode.lane_thread:
        payload["lane_thread_proven"] = episode.lane_thread
    if episode.parent_thread:
        payload["parent_thread_proven"] = episode.parent_thread
    if episode.lane_role:
        payload["lane_role_proven"] = episode.lane_role
    if episode.association_id is not None:
        payload["association_id"] = episode.association_id
    if episode.lineage_observed_at is not None:
        payload["lineage_observed_at"] = episode.lineage_observed_at
    return payload


def _freshness(
    episode: ProvenanceEpisode,
    *,
    lineage_observed_at: float | None = None,
) -> dict[str, float | None]:
    return {
        "episode_observed_at": episode.observed_at,
        "lineage_observed_at": lineage_observed_at
        if lineage_observed_at is not None
        else episode.lineage_observed_at,
    }


def _episode_projection(
    episode: ProvenanceEpisode,
    *,
    binding_state: str,
    host_state: str | None,
    lineage_state: str,
    claim_episode: ProvenanceEpisode,
    include_claim: bool,
    include_proven: bool,
    proven_episode: ProvenanceEpisode | None = None,
    source: str | None = None,
    reason: str | None = None,
    overlay_lineage_observed_at: float | None = None,
) -> dict[str, Any]:
    """Project one episode using dense-spec claim/proven keys only."""
    proven = proven_episode or episode
    payload: dict[str, Any] = {
        "state": binding_state,
        "host_state": host_state,
        "lineage_state": lineage_state,
        "episode_id": episode.episode_id,
        "chat_url": episode.chat_url,
        "registration_id": episode.registration_id,
        "cdp_url": episode.cdp_url,
        "evidence_class": episode.evidence_class,
        "attribution_source": episode.attribution_source,
        "correlation_id": episode.correlation_id,
        "observed_at": episode.observed_at,
        "freshness": _freshness(
            episode,
            lineage_observed_at=overlay_lineage_observed_at
            if overlay_lineage_observed_at is not None
            else proven.lineage_observed_at,
        ),
    }
    if include_claim:
        payload.update(_claim_projection(claim_episode))
    if include_proven and proven_episode is not None:
        payload.update(_proven_projection(proven_episode))
    payload["lineage_observed_at"] = (
        overlay_lineage_observed_at
        if overlay_lineage_observed_at is not None
        else (proven_episode.lineage_observed_at if proven_episode is not None else None)
        if include_proven
        else episode.lineage_observed_at
    )
    if source:
        payload["source"] = source
    payload["reason"] = reason or episode.reason
    if payload.get("reason") is None:
        payload.pop("reason", None)
    return payload
