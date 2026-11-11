"""Atomic journal-then-active commit for dormant binding retire."""

from __future__ import annotations

import time
from typing import Any

from claude_bundles import cdp_registry_store as store
from claude_bundles.cse_provenance import ProvenanceEpisode
from claude_bundles.cse_provenance_retire import append_binding_retired_episode

from .binding_retire_models import RETIRE_LOG_EVENT, refuse_retire
from .models import STATUS_DORMANT


def commit_dormant_binding_retire(
    *,
    registration_id: str,
    chat_url: str,
    reason: str,
    caller_agent: str | None,
    cdp_url: str = "",
    extra_audit: dict[str, Any] | None = None,
) -> tuple[ProvenanceEpisode, bool]:
    """Journal retire episode, then unbind ``active.json`` — all under ``ports_lock``."""
    rid = registration_id.strip()
    url = chat_url.strip()
    agent = (caller_agent or "cdp-registry").strip() or "cdp-registry"
    unbound = False
    audit_before: dict[str, Any] = {}

    with store.ports_lock():
        active = store.load_active()
        row = active.get(rid)
        if not isinstance(row, dict):
            refuse_retire(
                reason="row_gone",
                message=f"refusing retire: registration {rid!r} disappeared under lock",
                registration_id=rid,
                chat_url=url,
            )
        status = str(row.get("status") or "")
        if status != STATUS_DORMANT:
            refuse_retire(
                reason="not_dormant",
                message=f"refusing retire: status {status!r} is not dormant",
                registration_id=rid,
                chat_url=url,
                status=status or None,
            )
        prior_url = str(row.get("chat_url") or "").strip() or None
        audit_before = {"status": status, "chat_url": prior_url}

        episode = append_binding_retired_episode(
            chat_url=url,
            registration_id=rid,
            cdp_url=cdp_url or str(row.get("cdp_url") or ""),
            reason=reason,
        )

        if prior_url:
            updated = dict(row)
            updated.pop("chat_url", None)
            updated.pop("chat_url_bound_at", None)
            active[rid] = updated
            store.write_active(active)
            unbound = True

        store.append_log(
            RETIRE_LOG_EVENT,
            {
                "registration_id": rid,
                "prior_chat_url": prior_url,
                "reason": reason,
                "caller_agent": agent,
                "episode_id": episode.episode_id,
                "before": audit_before,
                "after": {
                    "status": status,
                    "chat_url": None if prior_url else prior_url,
                },
                "ts_audit": time.time(),
                **(extra_audit or {}),
            },
        )
    return episode, unbound
