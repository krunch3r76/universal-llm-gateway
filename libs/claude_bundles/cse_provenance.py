"""Evidence-bearing CSE URL, registry, and lane-lineage projections.

The CDP registry owns the durable episode log while callers provide a
read-only lane-lineage lookup.  Episodes are append-only so rebinding a URL
cannot erase the evidence for an earlier host or mission.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any, Protocol

from claude_bundles import cdp_registry_store as store
from claude_bundles.cse_url import normalize_cse_url

HostListablePredicate = Callable[[str], bool]


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
    lineage_state: str | None = None
    association_id: int | None = None
    lineage_observed_at: float | None = None


_EPISODE_FIELDS = frozenset(ProvenanceEpisode.__dataclass_fields__)


def _episode_record(episode: ProvenanceEpisode) -> dict[str, Any]:
    return {"event": "cse.provenance.episode", **asdict(episode)}


def _episodes_for_chat_url(chat_url: str) -> list[ProvenanceEpisode]:
    """Return every episode bound to one normalized CSE URL in append order."""
    return [episode for episode in read_episodes() if episode.chat_url == chat_url]


def _legacy_episode_fields(record: dict[str, Any]) -> dict[str, Any]:
    """Map pre-lineage_state journal rows onto the current episode shape."""
    fields = {key: record.get(key) for key in _EPISODE_FIELDS}
    if fields.get("parent_thread") is None and record.get("parent_thread"):
        fields["parent_thread"] = record.get("parent_thread")
    if fields.get("lane_role") is None and record.get("lane_role"):
        fields["lane_role"] = record.get("lane_role")
    if fields.get("lineage_state") == "proven" and fields.get("association_id") is None:
        fields["lineage_state"] = "claimed" if fields.get("lane_thread") else "unresolved"
        fields["association_id"] = None
    elif fields.get("lineage_state") is None:
        fields["lineage_state"] = "claimed" if fields.get("lane_thread") else "unresolved"
    return fields


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


def append_episode(
    *,
    chat_url: str,
    registration_id: str,
    cdp_url: str,
    lane_thread: str | None = None,
    lineage: dict[str, Any] | None = None,
    lineage_state: str | None = None,
    association_id: int | None = None,
    lineage_observed_at: float | None = None,
    correlation_id: str | None = None,
    evidence_class: str = "observed",
    attribution_source: str = "cdp-registry",
    state: str = "bound",
    reason: str | None = None,
) -> ProvenanceEpisode:
    """Append and fsync one immutable provenance episode to the registry journal.

    ``lane_thread`` is a registry claim; ``lineage`` copies ``parent_thread``
    and ``lane_role`` from an explicit proof writer.  Prior bytes stay immutable
    via ``supersedes`` linkage rather than in-place mutation.
    """
    normalized = normalize_cse_url(chat_url)
    if not normalized:
        raise ValueError("chat_url must be a CSE URL")
    if "/cowork/cse_" not in normalized:
        raise ValueError("chat_url must identify a Cowork CSE")
    if lineage_state is None:
        lineage_state = "claimed" if lane_thread else "unresolved"
    if lineage_state == "proven":
        if association_id is None:
            raise ValueError("proven episodes require association_id")
    elif association_id is not None:
        raise ValueError("association_id requires lineage_state=proven")
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
        lineage_state=lineage_state,
        association_id=association_id if lineage_state == "proven" else None,
        lineage_observed_at=lineage_observed_at,
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
            lineage_state=episode.lineage_state,
            association_id=episode.association_id,
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
    if not lane_thread and lineage_state == "unresolved":
        _emit_unresolved(normalized, reason or "lane_less_bind", correlation_id)
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
        fields = _legacy_episode_fields(record)
        rows.append(ProvenanceEpisode(**fields))
    return rows


def resolve(
    *,
    chat_url: str | None = None,
    registration_id: str | None = None,
    lineage_reader: LaneLineageReader | None = None,
    host_listable: HostListablePredicate | None = None,
) -> dict[str, Any]:
    """Resolve the latest evidence-bearing episode or return a typed state."""
    from claude_bundles.cse_provenance_resolve import resolve as _resolve

    return _resolve(
        chat_url=chat_url,
        registration_id=registration_id,
        lineage_reader=lineage_reader,
        host_listable=host_listable,
    )
