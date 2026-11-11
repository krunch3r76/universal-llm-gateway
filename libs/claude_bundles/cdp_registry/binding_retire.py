"""Sanctioned single-row retire for one dormant CSE URL binding."""

from __future__ import annotations

import os
import time
from dataclasses import asdict, dataclass
from typing import Any

from claude_bundles import cdp_registry_store as store
from claude_bundles.cse_provenance import latest_episode
from claude_bundles.cse_provenance_retire import (
    append_binding_retired_episode,
    latest_binding_closed,
)
from claude_bundles.cse_url import normalize_cse_url
from claude_bundles.hop_cadence_seat_snap import _active_map_from_registry_document

from .models import STATUS_DORMANT

_RETIRE_LOG_EVENT = "dormant_binding_retired"


def _result_payload(result: RetireBindingResult) -> dict[str, Any]:
    return asdict(result)


@dataclass(frozen=True, slots=True)
class RetireBindingResult:
    """Outcome of ``retire_dormant_binding``."""

    outcome: str
    registration_id: str | None
    chat_url: str | None
    dry_run: bool
    idempotent: bool
    detail: dict[str, Any]


def _actor_label() -> str:
    return (os.environ.get("CDP_REGISTRY_RETIRE_ACTOR") or "cdp-registry").strip()


def _fleet_active_map() -> dict[str, dict[str, Any]]:
    from claude_bundles.cdp_registry_remote_read import read_fleet_registry

    doc = read_fleet_registry()
    if doc.get("availability") != "ok":
        return {}
    raw = _active_map_from_registry_document(doc)
    return {
        rid: dict(row) for rid, row in raw.items() if isinstance(row, dict)
    }


def _local_row(registration_id: str) -> dict[str, Any] | None:
    row = store.load_active().get(registration_id)
    return dict(row) if isinstance(row, dict) else None


def _fleet_row(registration_id: str) -> dict[str, Any] | None:
    return _fleet_active_map().get(registration_id)


def resolve_retire_target(
    *,
    registration_id: str | None = None,
    chat_url: str | None = None,
) -> tuple[str | None, str | None, dict[str, Any] | None]:
    """Resolve ``(registration_id, normalized chat_url, registry row)`` for retire.

  Local ``active.json`` wins; when the row is absent locally, the fleet registry
  document supplies read-only confirmation (hub → cdp_ask HTTP, no SSH).
    """
    rid = (registration_id or "").strip() or None
    url = normalize_cse_url(chat_url or "") or None

    if rid:
        row = _local_row(rid) or _fleet_row(rid)
        bound = normalize_cse_url(str((row or {}).get("chat_url") or "")) or url
        if not url and bound:
            url = bound
        return rid, url, row

    if not url:
        return None, None, None

    from .dormant import dormant_for_chat_url

    seat = dormant_for_chat_url(url)
    if seat is not None:
        row = _local_row(seat.registration_id)
        return seat.registration_id, url, row

    for candidate_id, row in store.load_active().items():
        if normalize_cse_url(str(row.get("chat_url") or "")) == url:
            return candidate_id, url, dict(row)

    for candidate_id, row in _fleet_active_map().items():
        if normalize_cse_url(str(row.get("chat_url") or "")) == url:
            return candidate_id, url, row

    latest = latest_episode(chat_url=url)
    if latest is not None:
        return latest.registration_id, url, _local_row(latest.registration_id)

    return None, url, None


def retire_dormant_binding(
    *,
    registration_id: str | None = None,
    chat_url: str | None = None,
    reason: str = "sanctioned_dormant_retire",
    dry_run: bool = False,
) -> dict[str, Any]:
    """Retire one dormant URL binding: unbind locally, audit, close provenance.

    Idempotent: already-retired journal, absent binding, or repeat calls return
    ``outcome=noop`` without error. Never opens Cowork or kills Chrome.
    """
    rid, url, fleet_or_local = resolve_retire_target(
        registration_id=registration_id,
        chat_url=chat_url,
    )

    if url and latest_binding_closed(chat_url=url):
        return _result_payload(
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
        return _result_payload(
            RetireBindingResult(
                outcome="noop",
                registration_id=None,
                chat_url=None,
                dry_run=dry_run,
                idempotent=True,
                detail={"reason": "target_absent"},
            )
        )

    if not url:
        latest = latest_episode(registration_id=rid) if rid else None
        url = normalize_cse_url(latest.chat_url) if latest else None

    if not url and not rid:
        return _result_payload(
            RetireBindingResult(
                outcome="noop",
                registration_id=None,
                chat_url=None,
                dry_run=dry_run,
                idempotent=True,
                detail={"reason": "no_binding_evidence"},
            )
        )

    row = _local_row(rid) if rid else None
    actor = _actor_label()
    audit_before: dict[str, Any] = {}
    unbound = False

    has_evidence = (
        row is not None
        or fleet_or_local is not None
        or latest_episode(chat_url=url) is not None
        or (rid and latest_episode(registration_id=rid) is not None)
    )
    if not has_evidence:
        return _result_payload(
            RetireBindingResult(
                outcome="noop",
                registration_id=rid,
                chat_url=url,
                dry_run=dry_run,
                idempotent=True,
                detail={"reason": "target_absent"},
            )
        )

    if row is not None:
        audit_before = {
            "status": row.get("status"),
            "chat_url": row.get("chat_url"),
        }
        status = str(row.get("status") or "")
        prior_url = str(row.get("chat_url") or "").strip() or None
        if status != STATUS_DORMANT:
            if not prior_url and url and latest_binding_closed(chat_url=url):
                pass
            elif status and status != STATUS_DORMANT:
                return _result_payload(
                    RetireBindingResult(
                        outcome="refused",
                        registration_id=rid,
                        chat_url=url,
                        dry_run=dry_run,
                        idempotent=False,
                        detail={
                            "reason": "not_dormant",
                            "status": status,
                        },
                    )
                )
        if prior_url and not dry_run:
            with store.ports_lock():
                active = store.load_active()
                current = active.get(rid or "")
                if not isinstance(current, dict):
                    current = row
                updated = dict(current)
                updated.pop("chat_url", None)
                updated.pop("chat_url_bound_at", None)
                active[rid or ""] = updated
                store.write_active(active)
                store.append_log(
                    _RETIRE_LOG_EVENT,
                    {
                        "registration_id": rid,
                        "prior_chat_url": prior_url,
                        "reason": reason,
                        "actor": actor,
                        "before": audit_before,
                        "after": {
                            "status": updated.get("status"),
                            "chat_url": updated.get("chat_url"),
                        },
                        "ts_audit": time.time(),
                    },
                )
            unbound = True
        elif not prior_url:
            unbound = True

    if dry_run:
        return _result_payload(
            RetireBindingResult(
                outcome="dry_run",
                registration_id=rid,
                chat_url=url,
                dry_run=True,
                idempotent=False,
                detail={
                    "would_unbind_active": bool(
                        row and str(row.get("chat_url") or "").strip()
                    ),
                    "would_append_provenance": bool(url and rid),
                    "fleet_row_seen": fleet_or_local is not None and row is None,
                },
            )
        )

    if url and rid:
        append_binding_retired_episode(
            chat_url=url,
            registration_id=rid,
            cdp_url=str((row or fleet_or_local or {}).get("cdp_url") or ""),
            reason=reason,
            actor=actor,
            audit={
                "before": audit_before,
                "after": {"chat_url": None},
                "active_unbound": unbound,
            },
        )
    elif url and not rid:
        latest = latest_episode(chat_url=url)
        reg = latest.registration_id if latest else None
        if reg:
            append_binding_retired_episode(
                chat_url=url,
                registration_id=reg,
                reason=reason,
                actor=actor,
                audit={"before": audit_before, "journal_only": True},
            )
            rid = reg

    if not unbound and url and latest_binding_closed(chat_url=url):
        outcome = "noop"
    else:
        outcome = "retired"

    return _result_payload(
        RetireBindingResult(
            outcome=outcome,
            registration_id=rid,
            chat_url=url,
            dry_run=False,
            idempotent=outcome == "noop",
            detail={"active_unbound": unbound, "reason": reason, "actor": actor},
        )
    )
