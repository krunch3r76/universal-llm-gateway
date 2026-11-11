"""Append-only provenance episodes that close a CSE URL binding."""

from __future__ import annotations

from claude_bundles.cse_provenance import (
    ProvenanceEpisode,
    append_episode,
    episodes_for_chat_url,
    latest_episode,
)
from claude_bundles.cse_url import normalize_cse_url

BINDING_RETIRED_STATES = frozenset({"retired", "unbound"})


def binding_closed_by_episode(episode: ProvenanceEpisode) -> bool:
    """True when an episode marks the URL binding as closed."""
    return str(episode.state or "") in BINDING_RETIRED_STATES


def latest_binding_closed(*, chat_url: str | None = None) -> bool:
    """True when the newest journal episode for the URL is a retire/unbound row."""
    target = normalize_cse_url(chat_url or "")
    if not target:
        return False
    latest = latest_episode(chat_url=target)
    return latest is not None and binding_closed_by_episode(latest)


def per_registration_latest_episodes(
    chat_url: str,
) -> dict[str, ProvenanceEpisode]:
    """Latest journal row per ``registration_id`` for one URL (append order)."""
    by_reg: dict[str, ProvenanceEpisode] = {}
    target = normalize_cse_url(chat_url)
    if not target:
        return {}
    for episode in episodes_for_chat_url(target):
        prior = by_reg.get(episode.registration_id)
        if prior is None or episode.observed_at >= prior.observed_at:
            by_reg[episode.registration_id] = episode
    return by_reg


def open_binding_holder_episode(chat_url: str) -> ProvenanceEpisode | None:
    """Newest open binding among per-registration latest episodes for the URL."""
    open_eps = [
        episode
        for episode in per_registration_latest_episodes(chat_url).values()
        if not binding_closed_by_episode(episode)
    ]
    if not open_eps:
        return None
    return max(open_eps, key=lambda episode: episode.observed_at)


def open_binding_holder_registration(chat_url: str) -> str | None:
    episode = open_binding_holder_episode(chat_url)
    return episode.registration_id if episode is not None else None


def latest_open_binding_episode(chat_url: str) -> ProvenanceEpisode | None:
    """Alias for the per-registration holder episode (N1 shared helper)."""
    return open_binding_holder_episode(chat_url)


def append_binding_retired_episode(
    *,
    chat_url: str,
    registration_id: str,
    cdp_url: str = "",
    reason: str,
    attribution_source: str = "cdp-registry",
) -> ProvenanceEpisode:
    """Append a journal episode that closes provenance for one CSE URL binding."""
    return append_episode(
        chat_url=chat_url,
        registration_id=registration_id,
        cdp_url=cdp_url,
        lane_thread=None,
        lineage_state="unresolved",
        state="retired",
        reason=reason,
        attribution_source=attribution_source,
    )
