"""Sanctioned single-row retire for one dormant CSE URL binding."""

from __future__ import annotations

from typing import Any

from claude_bundles.cse_provenance import latest_episode
from claude_bundles.cse_provenance_retire import latest_binding_closed
from claude_bundles.cse_url import normalize_cse_url

from .binding_retire_commit import commit_dormant_binding_retire
from .binding_retire_guards import (
    require_dormant_status,
    require_registry_authority,
    require_url_holder,
    status_source_row,
)
from .binding_retire_models import (
    RetireBindingResult,
    refuse_retire,
    result_payload,
)
from .binding_retire_resolve import local_row, resolve_retire_target


def retire_dormant_binding(
    *,
    registration_id: str | None = None,
    chat_url: str | None = None,
    reason: str = "sanctioned_dormant_retire",
    dry_run: bool = False,
    caller_agent: str | None = None,
) -> dict[str, Any]:
    """Retire one dormant URL binding: unbind locally, audit, close provenance."""
    cleaned_reason = (reason or "").strip()
    if not cleaned_reason:
        refuse_retire(
            reason="reason_required",
            message="refusing retire: reason must be non-empty",
        )

    rid, url, resolved_row = resolve_retire_target(
        registration_id=registration_id,
        chat_url=chat_url,
    )

    if url and latest_binding_closed(chat_url=url):
        latest = latest_episode(chat_url=url)
        holder = (rid or "").strip() or None
        if latest and (holder is None or latest.registration_id == holder):
            return result_payload(
                RetireBindingResult(
                    outcome="noop",
                    registration_id=rid,
                    chat_url=url,
                    dry_run=dry_run,
                    idempotent=True,
                    detail={"reason": "binding_already_retired"},
                )
            )

    if not rid and not url:
        return result_payload(
            RetireBindingResult(
                outcome="noop",
                registration_id=None,
                chat_url=None,
                dry_run=dry_run,
                idempotent=True,
                detail={"reason": "target_absent"},
            )
        )

    if not url and rid:
        latest = latest_episode(registration_id=rid)
        url = normalize_cse_url(latest.chat_url) if latest else None

    if not url or not rid:
        return result_payload(
            RetireBindingResult(
                outcome="noop",
                registration_id=rid,
                chat_url=url,
                dry_run=dry_run,
                idempotent=True,
                detail={"reason": "no_binding_evidence"},
            )
        )

    local = local_row(rid)
    has_evidence = (
        local is not None
        or resolved_row is not None
        or latest_episode(chat_url=url) is not None
        or latest_episode(registration_id=rid) is not None
    )
    if not has_evidence:
        return result_payload(
            RetireBindingResult(
                outcome="noop",
                registration_id=rid,
                chat_url=url,
                dry_run=dry_run,
                idempotent=True,
                detail={"reason": "target_absent"},
            )
        )

    status_row = status_source_row(
        registration_id=rid,
        resolved_row=resolved_row,
    )
    require_dormant_status(status_row, registration_id=rid, chat_url=url)
    require_url_holder(registration_id=rid, chat_url=url)

    if dry_run:
        return result_payload(
            RetireBindingResult(
                outcome="dry_run",
                registration_id=rid,
                chat_url=url,
                dry_run=True,
                idempotent=False,
                detail={
                    "would_unbind_active": bool(
                        local and str(local.get("chat_url") or "").strip()
                    ),
                    "would_append_provenance": local is not None,
                    "fleet_row_seen": local is None and resolved_row is not None,
                },
            )
        )

    require_registry_authority(will_mutate=True)

    if local is None:
        if resolved_row is not None:
            refuse_retire(
                reason="not_registry_authority",
                message=(
                    "refusing retire: row exists only on fleet registry; "
                    "use cse_session retire_binding on the satellite"
                ),
                registration_id=rid,
                chat_url=url,
            )
        refuse_retire(
            reason="row_gone",
            message="refusing retire: registration row is not present locally",
            registration_id=rid,
            chat_url=url,
        )

    _, unbound = commit_dormant_binding_retire(
        registration_id=rid,
        chat_url=url,
        reason=cleaned_reason,
        caller_agent=caller_agent,
        cdp_url=str((local or resolved_row or {}).get("cdp_url") or ""),
    )
    return result_payload(
        RetireBindingResult(
            outcome="retired",
            registration_id=rid,
            chat_url=url,
            dry_run=False,
            idempotent=False,
            detail={"active_unbound": unbound, "reason": cleaned_reason},
        )
    )
