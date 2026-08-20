"""Typed CSE provenance resolution — claim versus proven lineage separation."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from claude_bundles import cdp_registry_store as store
from claude_bundles.cse_provenance import (
    HostListablePredicate,
    LaneLineageReader,
    ProvenanceEpisode,
    read_episodes,
)
from claude_bundles.cse_provenance_projection import (
    _claim_episode,
    _episode_projection,
)
from claude_bundles.cse_url import normalize_cse_url

_ROW_PRESENT_STATUSES = frozenset(
    {"active", "orphaned_alive", "retained", "dormant"}
)
_HISTORICAL_STATUSES = frozenset({"released", "orphaned_retry"})


def is_row_present(registration_id: str) -> bool:
    """Return True when a registry row still exists short of reclaim.

    Includes dormant: a parked operator CSE remains current identity even
    though no Chrome holds it. This is not a host-attachment predicate.
    """
    row = store.load_active().get(registration_id)
    if not isinstance(row, dict):
        return False
    status = str(row.get("status") or "")
    return status in _ROW_PRESENT_STATUSES if status else False


def _default_host_listable(_registration_id: str) -> bool:
    """Hermetic journal tests treat every host as listable when no predicate is passed."""
    return True


def _binding_state(
    registration_id: str,
    host_listable: HostListablePredicate | None,
    *,
    latest_registration_id: str | None = None,
) -> str:
    if host_listable is not None:
        return "current" if host_listable(registration_id) else "historical"
    if latest_registration_id is not None:
        return "current" if registration_id == latest_registration_id else "historical"
    return "current" if _default_host_listable(registration_id) else "historical"


def _host_state(
    registration_id: str,
    host_listable: HostListablePredicate | None,
) -> str | None:
    if host_listable is None:
        return None
    return "listable" if host_listable(registration_id) else "not_listable"


def _emit_conflict(chat_url: str, candidate_count: int, correlation_id: str | None) -> None:
    from claude_bundles import cdp_registry_events

    cdp_registry_events.emit(
        cdp_registry_events.cdp_provenance_conflict(
            chat_url=chat_url,
            candidate_count=candidate_count,
            correlation_id=correlation_id,
        )
    )


def _emit_unresolved(chat_url: str, reason: str, correlation_id: str | None) -> None:
    from claude_bundles import cdp_registry_events

    cdp_registry_events.emit(
        cdp_registry_events.cdp_provenance_unresolved(
            chat_url=chat_url,
            reason=reason,
            correlation_id=correlation_id,
        )
    )


def _candidate_evidence(
    episode: ProvenanceEpisode,
    host_listable: HostListablePredicate | None,
    episodes: list[ProvenanceEpisode],
    *,
    latest_registration_id: str | None = None,
) -> dict[str, Any]:
    binding = _binding_state(
        episode.registration_id,
        host_listable,
        latest_registration_id=latest_registration_id,
    )
    claim = _claim_episode(episode, episodes)
    candidate: dict[str, Any] = {
        "registration_id": episode.registration_id,
        "cdp_url": episode.cdp_url,
        "episode_id": episode.episode_id,
        "host_state": _host_state(episode.registration_id, host_listable),
        "lineage_state": episode.lineage_state or "unresolved",
        "claim_observed_at": claim.observed_at,
        "lineage_observed_at": episode.lineage_observed_at,
        "association_id": episode.association_id,
    }
    if binding == "historical":
        candidate["state"] = "historical"
    else:
        candidate["state"] = binding
    return candidate


def _candidates_for_url(
    chat_url: str,
    episodes: list[ProvenanceEpisode],
    host_listable: HostListablePredicate | None,
) -> list[dict[str, Any]]:
    latest_registration_id = episodes[-1].registration_id if episodes else None
    seen: set[str] = set()
    candidates: list[dict[str, Any]] = []
    for episode in reversed(episodes):
        if episode.registration_id in seen:
            continue
        seen.add(episode.registration_id)
        candidates.append(
            _candidate_evidence(
                episode,
                host_listable,
                episodes,
                latest_registration_id=latest_registration_id,
            )
        )
    return list(reversed(candidates))


def _reader_lineage(
    episode: ProvenanceEpisode,
    lineage_reader: LaneLineageReader,
) -> tuple[dict[str, Any] | None, str | None]:
    """Return reader lineage or an unresolved reason; never infer proof from claims."""
    if not episode.lane_thread:
        return None, "lane_lineage_missing"
    try:
        lineage = lineage_reader(episode.lane_thread)
    except Exception as exc:
        exc_name = exc.__class__.__name__
        if exc_name == "LaneLineageUnreachable":
            return None, "lane_lineage_unreachable"
        raise
    if isinstance(lineage, dict) and lineage.get("unreachable"):
        return None, "lane_lineage_unreachable"
    if lineage is None or lineage.get("state") in {None, "none"}:
        return None, "lane_lineage_missing"
    if lineage.get("state") != "associated":
        return None, "lane_lineage_missing"
    if lineage.get("association_id") is None:
        return None, "lane_lineage_missing"
    return lineage, None


def _resolve_lineage_state(
    episode: ProvenanceEpisode,
    *,
    binding_state: str,
    lineage_reader: LaneLineageReader | None,
) -> tuple[str, dict[str, Any] | None, str | None, str | None]:
    if binding_state == "historical":
        return "unresolved", None, None, None

    if (
        episode.lineage_state == "proven"
        and episode.association_id is not None
        and not lineage_reader
    ):
        return "proven", None, None, "registry-journal"

    if not lineage_reader:
        stored = episode.lineage_state or "unresolved"
        return stored if stored in {"claimed", "unresolved", "proven"} else "unresolved", None, None, None

    if not episode.lane_thread:
        _emit_unresolved(episode.chat_url, "lane_lineage_missing", episode.correlation_id)
        return "unresolved", None, "lane_lineage_missing", None

    lineage, unresolved_reason = _reader_lineage(episode, lineage_reader)
    if unresolved_reason:
        _emit_unresolved(episode.chat_url, unresolved_reason, episode.correlation_id)
        return "unresolved", None, unresolved_reason, None
    return "proven", lineage, None, "registry-journal+bus-overlay"


def resolve(
    *,
    chat_url: str | None = None,
    registration_id: str | None = None,
    lineage_reader: LaneLineageReader | None = None,
    host_listable: HostListablePredicate | None = None,
) -> dict[str, Any]:
    """Resolve the latest evidence-bearing episode or return a typed binding state."""
    target = normalize_cse_url(chat_url or "")
    all_episodes = read_episodes()
    if target:
        episodes = [episode for episode in all_episodes if episode.chat_url == target]
    elif registration_id:
        episodes = [
            episode for episode in all_episodes if episode.registration_id == registration_id
        ]
    else:
        episodes = []

    if not episodes:
        if target:
            return {
                "state": "unregistered_cse",
                "chat_url": target,
                "evidence_class": "observed",
                "reason": "no_episode",
            }
        return {
            "state": "unresolved",
            "chat_url": target,
            "evidence_class": "observed",
            "reason": "no_episode",
        }

    if target and len({episode.registration_id for episode in episodes}) > 1:
        candidates = _candidates_for_url(target, episodes, host_listable)
        current_regs = [
            candidate for candidate in candidates if candidate.get("state") == "current"
        ]
        if len(current_regs) > 1:
            _emit_conflict(target, len(current_regs), episodes[-1].correlation_id)
            return {
                "state": "conflict",
                "chat_url": target,
                "reason": "multiple_current_hosts",
                "candidate_count": len(current_regs),
                "candidates": candidates,
            }

    current = episodes[-1]

    if target and registration_id and current.registration_id != registration_id:
        candidate_count = len({episode.registration_id for episode in episodes})
        _emit_conflict(target, candidate_count, current.correlation_id)
        return {
            "state": "conflict",
            "chat_url": target,
            "reason": "registration_not_current_binding",
            "requested_registration_id": registration_id,
            "current_registration_id": current.registration_id,
            "candidate_count": candidate_count,
            "candidates": _candidates_for_url(target, episodes, host_listable),
        }

    binding_state = _binding_state(
        current.registration_id,
        host_listable,
        latest_registration_id=current.registration_id,
    )
    host_state = _host_state(current.registration_id, host_listable)
    lineage_state, overlay_lineage, unresolved_reason, overlay_source = _resolve_lineage_state(
        current,
        binding_state=binding_state,
        lineage_reader=lineage_reader,
    )

    claim_episode = _claim_episode(current, episodes)
    include_claim = binding_state != "historical"
    include_proven = (
        binding_state != "historical"
        and lineage_state == "proven"
        and (
            (current.lineage_state == "proven" and current.association_id is not None)
            or overlay_lineage is not None
        )
    )

    proven_episode: ProvenanceEpisode | None = None
    overlay_observed_at: float | None = None
    source: str | None = None

    if include_proven:
        if overlay_lineage is not None:
            proven_episode = replace(
                current,
                parent_thread=overlay_lineage.get("parent_thread"),
                lane_role=overlay_lineage.get("lane_role"),
                lane_thread=overlay_lineage.get("thread_id")
                or overlay_lineage.get("lane_thread")
                or current.lane_thread,
                association_id=int(overlay_lineage["association_id"]),
                lineage_observed_at=overlay_lineage.get("lineage_observed_at"),
            )
            overlay_observed_at = overlay_lineage.get("lineage_observed_at")
            source = overlay_source
        elif current.lineage_state == "proven" and current.association_id is not None:
            proven_episode = current
            source = overlay_source or "registry-journal"

    stored_source = "registry-journal"
    if not include_proven and include_claim:
        source = stored_source
    elif include_proven and source is None:
        source = stored_source

    return _episode_projection(
        current,
        binding_state=binding_state,
        host_state=host_state,
        lineage_state=lineage_state,
        claim_episode=claim_episode,
        include_claim=include_claim,
        include_proven=include_proven,
        proven_episode=proven_episode,
        source=source,
        reason=unresolved_reason,
        overlay_lineage_observed_at=overlay_observed_at,
    )
