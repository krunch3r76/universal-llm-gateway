"""Evidence-bearing CSE URL, registry, and lane-lineage projections.

The CDP registry owns the durable episode log while callers provide a
read-only lane-lineage lookup.  Episodes are append-only so rebinding a URL
cannot erase the evidence for an earlier host or mission.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass
from typing import Any, Protocol

from claude_bundles import cdp_registry_store as store
from claude_bundles.cse_url import normalize_cse_url


class LaneLineageReader(Protocol):
    """Read current agent-bus parentage and role for an explicit lane thread.

    Implementations are read-only adapters; a missing association must remain
    visible to the resolver instead of being inferred from registry history.
    """

    def __call__(self, lane_thread: str) -> dict[str, Any] | None: ...


@dataclass(frozen=True, slots=True)
class ProvenanceEpisode:
    """Immutable evidence record joining one CSE URL to one registry host."""

    episode_id: str
    chat_url: str
    registration_id: str
    cdp_url: str
    lane_thread: str | None
    parent_thread: str | None
    lane_role: str | None
    state: str
    evidence_class: str
    attribution_source: str
    correlation_id: str | None
    observed_at: float
    supersedes: str | None = None
    reason: str | None = None


def _episode_record(episode: ProvenanceEpisode) -> dict[str, Any]:
    return {"event": "cse.provenance.episode", **asdict(episode)}


def _episodes_for_chat_url(chat_url: str) -> list[ProvenanceEpisode]:
    """Return every episode bound to one normalized CSE URL in append order."""
    return [episode for episode in read_episodes() if episode.chat_url == chat_url]


def append_episode(
    *,
    chat_url: str,
    registration_id: str,
    cdp_url: str,
    lane_thread: str | None,
    lineage: dict[str, Any] | None = None,
    correlation_id: str | None = None,
    evidence_class: str = "observed",
    attribution_source: str = "cdp-registry",
    state: str = "bound",
    reason: str | None = None,
) -> ProvenanceEpisode:
    """Append and fsync one immutable provenance episode to the registry journal.

    A prior episode for the same URL is linked through ``supersedes`` rather
    than replaced, so an earlier host stays retrievable as evidence.  Moving a
    URL to a different host additionally reports the prior episode historical.
    """
    normalized = normalize_cse_url(chat_url)
    if not normalized:
        raise ValueError("chat_url must be a CSE URL")
    if "/cowork/cse_" not in normalized:
        raise ValueError("chat_url must identify a Cowork CSE")
    prior = _episodes_for_chat_url(normalized)
    superseded = prior[-1] if prior else None
    episode = ProvenanceEpisode(
        episode_id=uuid.uuid4().hex,
        chat_url=normalized,
        registration_id=registration_id,
        cdp_url=cdp_url,
        lane_thread=lane_thread,
        parent_thread=(lineage or {}).get("parent_thread"),
        lane_role=(lineage or {}).get("lane_role"),
        state=state,
        evidence_class=evidence_class,
        attribution_source=attribution_source,
        correlation_id=correlation_id,
        observed_at=time.time(),
        supersedes=superseded.episode_id if superseded else None,
        reason=reason,
    )
    store.append_log("cse_provenance_episode", _episode_record(episode))
    from claude_bundles import cdp_registry_events

    cdp_registry_events.emit(
        cdp_registry_events.cdp_provenance_bound(
            episode_id=episode.episode_id,
            chat_url=episode.chat_url,
            registration_id=episode.registration_id,
            cdp_url=episode.cdp_url,
            lane_thread=episode.lane_thread,
            parent_thread=episode.parent_thread,
            lane_role=episode.lane_role,
            evidence_class=episode.evidence_class,
            attribution_source=episode.attribution_source,
            correlation_id=episode.correlation_id,
        )
    )
    if superseded is not None and superseded.registration_id != registration_id:
        cdp_registry_events.emit(
            cdp_registry_events.cdp_provenance_historical(
                episode_id=superseded.episode_id,
                chat_url=normalized,
                reason="rebound_to_new_host",
            )
        )
    return episode


def read_episodes() -> list[ProvenanceEpisode]:
    """Read durable episodes from the registry journal and ignore other records."""
    rows: list[ProvenanceEpisode] = []
    if not store.REGISTRY_LOG.exists():
        return rows
    for raw in store.REGISTRY_LOG.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        record = json.loads(raw)
        if record.get("event") != "cse.provenance.episode":
            continue
        fields = {key: record.get(key) for key in ProvenanceEpisode.__dataclass_fields__}
        rows.append(ProvenanceEpisode(**fields))
    return rows


def _emit_conflict(chat_url: str, candidate_count: int, correlation_id: str | None) -> None:
    """Report competing hosts for one URL without deciding the winner here."""
    from claude_bundles import cdp_registry_events

    cdp_registry_events.emit(
        cdp_registry_events.cdp_provenance_conflict(
            chat_url=chat_url,
            candidate_count=candidate_count,
            correlation_id=correlation_id,
        )
    )


def _emit_unresolved(chat_url: str, reason: str, correlation_id: str | None) -> None:
    """Report evidence that exists but cannot complete a unique lineage join."""
    from claude_bundles import cdp_registry_events

    cdp_registry_events.emit(
        cdp_registry_events.cdp_provenance_unresolved(
            chat_url=chat_url,
            reason=reason,
            correlation_id=correlation_id,
        )
    )


def resolve(
    *,
    chat_url: str | None = None,
    registration_id: str | None = None,
    lineage_reader: LaneLineageReader | None = None,
) -> dict[str, Any]:
    """Resolve the latest evidence-bearing episode or return a typed state.

    A URL identifies the binding when supplied, so a caller naming a host that
    no longer holds the URL receives ``conflict`` instead of another host's
    evidence.  Absent episodes stay a silent typed state because an unbound
    lane is the ordinary case before its first bind.
    """
    target = normalize_cse_url(chat_url or "")
    all_episodes = read_episodes()
    if target:
        episodes = [e for e in all_episodes if e.chat_url == target]
    elif registration_id:
        episodes = [e for e in all_episodes if e.registration_id == registration_id]
    else:
        episodes = []
    if not episodes:
        return {
            "state": "unresolved",
            "chat_url": target,
            "evidence_class": "observed",
            "reason": "no_episode",
        }
    current = episodes[-1]
    if target and registration_id and current.registration_id != registration_id:
        candidate_count = len({e.registration_id for e in episodes})
        _emit_conflict(target, candidate_count, current.correlation_id)
        return {
            "state": "conflict",
            "chat_url": target,
            "reason": "registration_not_current_binding",
            "requested_registration_id": registration_id,
            "current_registration_id": current.registration_id,
            "candidate_count": candidate_count,
        }
    if lineage_reader and current.lane_thread:
        lineage = lineage_reader(current.lane_thread)
        if lineage is None and current.parent_thread is not None:
            _emit_unresolved(
                current.chat_url,
                "lane_lineage_missing",
                current.correlation_id,
            )
            return {
                "state": "unresolved",
                "episode_id": current.episode_id,
                "chat_url": current.chat_url,
                "registration_id": current.registration_id,
                "reason": "lane_lineage_missing",
            }
        current = ProvenanceEpisode(
            **{
                **asdict(current),
                "parent_thread": (lineage or {}).get("parent_thread", current.parent_thread),
                "lane_role": (lineage or {}).get("lane_role", current.lane_role),
            }
        )
    return {"state": current.state, **asdict(current)}
