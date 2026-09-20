"""Boot-time reconciliation of CSE holder rows against CDP registry snapshots."""

from __future__ import annotations

import sqlite3
from typing import Any

from claude_bundles.cse_url import normalize_cse_url
from claude_bundles.holder_strings import holder_id_from_chat_url

from services.git_integration_worker.cse_session_holders import (
    _OCCUPANCY_STATES,
    _emit,
    _now,
    transition_seat_state,
    upsert_holder,
)


def boot_reconcile(conn: sqlite3.Connection) -> dict[str, Any]:
    """Reconcile holder seat fields from registry; GIW wins lane fields."""
    from claude_bundles.cdp_registry.dormant import list_dormant
    from claude_bundles.cdp_registry.session_address import (
        chat_url_for_registration,
        list_active,
    )

    active_urls: set[str] = set()
    dormant_urls: set[str] = set()
    registry_by_url: dict[str, dict[str, Any]] = {}
    for reg in list_active():
        url = (chat_url_for_registration(reg.registration_id) or "").strip()
        if not url:
            continue
        norm = normalize_cse_url(url)
        active_urls.add(norm)
        registry_by_url[norm] = {
            "registration_id": reg.registration_id,
            "execution_id": getattr(reg, "execution_id", None),
        }
    for seat in list_dormant():
        url = normalize_cse_url(str(seat.chat_url or ""))
        if not url:
            continue
        dormant_urls.add(url)
        registry_by_url.setdefault(
            url,
            {
                "registration_id": seat.registration_id,
                "execution_id": getattr(seat, "execution_id", None),
            },
        )
    registry_urls = active_urls | dormant_urls
    registry_snapshot_empty = not registry_urls
    kept = released = updated = adopted = skipped_release = 0
    rows = conn.execute(
        "SELECT holder_id, chat_url, seat_state FROM cse_session_holders"
    ).fetchall()
    for row in rows:
        url = normalize_cse_url(str(row["chat_url"] or ""))
        state = str(row["seat_state"] or "")
        holder_id = str(row["holder_id"])
        if url in active_urls and state in _OCCUPANCY_STATES:
            reg = registry_by_url.get(url, {})
            conn.execute(
                "UPDATE cse_session_holders SET registration_id=?, "
                "execution_id=?, last_transition_at=? WHERE holder_id=?",
                (
                    reg.get("registration_id"),
                    reg.get("execution_id"),
                    _now(),
                    holder_id,
                ),
            )
            updated += 1
            kept += 1
        elif state == "driving" and url not in registry_urls:
            if registry_snapshot_empty:
                skipped_release += 1
                kept += 1
                _emit(
                    "cse.holder.reconcile_skip_release",
                    {
                        "holder_id": holder_id,
                        "chat_url": url,
                        "reason": "empty_registry_snapshot",
                    },
                )
            else:
                transition_seat_state(
                    conn,
                    holder_id,
                    to_state="released",
                    reason="boot_reconcile_absent_registry",
                )
                released += 1
        else:
            kept += 1
    existing_ids = {
        str(row["holder_id"])
        for row in conn.execute("SELECT holder_id FROM cse_session_holders").fetchall()
    }
    for url, reg in registry_by_url.items():
        hid = holder_id_from_chat_url(url)
        if not hid or hid in existing_ids:
            continue
        upsert_holder(
            conn,
            chat_url=url,
            registration_id=reg.get("registration_id"),
            execution_id=reg.get("execution_id"),
        )
        if url in dormant_urls and url not in active_urls:
            transition_seat_state(conn, hid, to_state="dormant")
        adopted += 1
        existing_ids.add(hid)
    summary = {
        "kept": kept,
        "released": released,
        "registry_updated": updated,
        "adopted": adopted,
        "skipped_release_empty_snapshot": skipped_release,
    }
    _emit("cse.holder.reconcile", summary)
    return summary


__all__ = ["boot_reconcile"]
