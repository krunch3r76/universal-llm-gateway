"""Session-close agent-bus thread disposition — advisory preflight warning."""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from typing import Any

from transport_utils import DEFAULT_AGENT_BUS_URL, make_sync_client

_AGENT_BUS_REF_RE = re.compile(
    r"^agent-bus:(?P<thread>\d+)(?:#turn-(?P<turn>\d+))?$"
)
_NUMERIC_THREAD_RE = re.compile(r"^\d+$")
_DEBRIEF_THREAD_ID = "480"
_OPEN_THREAD_STATUSES = frozenset({"active", "blocked", "waiting"})


def parse_bus_thread_refs(entity_ids: list[str] | None) -> list[str]:
    """Return unique bus thread ids cited in *entity_ids* (entity_ids-only scope)."""
    seen: set[str] = set()
    ordered: list[str] = []
    for raw in entity_ids or []:
        match = _AGENT_BUS_REF_RE.match(raw.strip())
        if match:
            thread_id = match.group("thread")
        elif _NUMERIC_THREAD_RE.match(raw.strip()):
            thread_id = raw.strip()
        else:
            continue
        if thread_id in seen:
            continue
        seen.add(thread_id)
        ordered.append(thread_id)
    return ordered


def _default_thread_status_lookup(thread_id: str) -> dict[str, Any] | None:
    token = os.environ.get("AGENT_BUS_TOKEN", "").strip()
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        with make_sync_client(DEFAULT_AGENT_BUS_URL, timeout=5.0) as client:
            resp = client.get(f"/threads/{thread_id}", headers=headers)
            if resp.status_code != 200:
                return None
            data = resp.json()
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def active_bus_threads_in_entity_ids(
    entity_ids: list[str] | None,
    *,
    status_lookup: Callable[[str], dict[str, Any] | None] | None = None,
) -> list[dict[str, str]]:
    """Return bus threads in *entity_ids* that are still open (not closed)."""
    lookup = status_lookup or _default_thread_status_lookup
    pending: list[dict[str, str]] = []
    for thread_id in parse_bus_thread_refs(entity_ids):
        if thread_id == _DEBRIEF_THREAD_ID:
            continue
        row = lookup(thread_id)
        if row is None:
            continue
        status = str(row.get("status") or "")
        if status not in _OPEN_THREAD_STATUSES:
            continue
        pending.append({"thread_id": thread_id, "status": status})
    return pending


def bus_thread_disposition_warning(pending: list[dict[str, str]]) -> str | None:
    if not pending:
        return None
    refs = ", ".join(
        f"agent-bus:{item['thread_id']} ({item['status']})" for item in pending
    )
    return (
        f"bus_thread_disposition.required: {refs} still open in entity_ids — "
        "include Agent-bus thread disposition in the close report "
        "(agent-bus-discipline § Session-close thread disposition): "
        "standing root ⇒ advise-close + checkpoint fields; one-off/dispatch ⇒ "
        "exactly one of advise-close | closed | leave-open+reason + thread id. "
        "Thread 480 is debrief-only, not a disposition target."
    )


def bus_thread_disposition_preflight_fields(
    entity_ids: list[str] | None,
    *,
    status_lookup: Callable[[str], dict[str, Any] | None] | None = None,
) -> dict[str, Any]:
    pending = active_bus_threads_in_entity_ids(
        entity_ids, status_lookup=status_lookup
    )
    out: dict[str, Any] = {"active_bus_threads_in_entity_ids": pending}
    warning = bus_thread_disposition_warning(pending)
    if warning:
        out["bus_thread_disposition_warning"] = warning
    return out


__all__ = [
    "active_bus_threads_in_entity_ids",
    "bus_thread_disposition_preflight_fields",
    "bus_thread_disposition_warning",
    "parse_bus_thread_refs",
]
