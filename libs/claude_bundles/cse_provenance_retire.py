"""Append-only provenance episodes that close a CSE URL binding."""

from __future__ import annotations

from typing import Any

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


def append_binding_retired_episode(
    *,
    chat_url: str,
    registration_id: str,
    cdp_url: str = "",
    reason: str,
    attribution_source: str = "cdp-registry",
    actor: str | None = None,
    audit: dict[str, Any] | None = None,
) -> ProvenanceEpisode:
    """Append a journal episode that closes provenance for one CSE URL binding."""
    detail = dict(audit or {})
    if actor:
        detail["actor"] = actor
    detail["reason"] = reason
    audit_reason = reason if not detail else f"{reason}|{detail!r}"
    return append_episode(
        chat_url=chat_url,
        registration_id=registration_id,
        cdp_url=cdp_url,
        lane_thread=None,
        lineage_state="unresolved",
        state="retired",
        reason=audit_reason,
        attribution_source=attribution_source,
    )


def episodes_for_chat_url_open(chat_url: str) -> list:
    """Episodes for a URL excluding those after the latest retire/unbound marker."""
    episodes = episodes_for_chat_url(chat_url)
    for index in range(len(episodes) - 1, -1, -1):
        if binding_closed_by_episode(episodes[index]):
            return episodes[: index + 1]
    return episodes
