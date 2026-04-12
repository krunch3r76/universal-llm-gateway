"""Cortex dispatch op handlers — reflective journal (first-person agent introspection).

Plain handler functions consumed by the _OPS dispatch table in cortex.py.
Relays to cortex-api /reflective-journal/* endpoints.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlencode

from mcp_events import record

from ._cortex_relay import _cx

logger = logging.getLogger(__name__)


def _op_rj_write(
    agent: str | None = None,
    register: str | None = None,
    entry: str | None = None,
    kind: str = "entry",
    session_id: str | None = None,
    revises: int | None = None,
    links: list[dict[str, Any]] | None = None,
    consolidation_data: dict[str, Any] | None = None,
    **_: object,
) -> dict[str, Any]:
    required = {"agent": agent, "register": register, "entry": entry}
    for field, val in required.items():
        if not val:
            return {"error": f"{field} is required"}
    body: dict[str, Any] = {
        "agent": agent,
        "register": register,
        "entry": entry,
        "kind": kind,
    }
    if session_id is not None:
        body["session_id"] = session_id
    if revises is not None:
        body["revises"] = revises
    if links is not None:
        body["links"] = links
    if consolidation_data is not None:
        body["consolidation_data"] = consolidation_data

    result = _cx("POST", "/reflective-journal", body)
    if "error" not in result:
        logger.info(
            "rj_write: %s/%s (%s) — %s",
            agent,
            register,
            kind,
            str(entry)[:60],
        )
        record(
            "mcp.cortex.rj.written",
            agent=agent,
            register=register,
            kind=kind,
        )
    return result


def _op_rj_read(
    entry_id: int | None = None,
    **_: object,
) -> dict[str, Any]:
    if entry_id is None:
        return {"error": "entry_id is required"}
    return _cx("GET", f"/reflective-journal/{entry_id}")


def _op_rj_list(
    agent: str | None = None,
    kind: str | None = None,
    limit: int | None = None,
    offset: int | None = None,
    **_: object,
) -> dict[str, Any]:
    params: dict[str, object] = {}
    if agent is not None:
        params["agent"] = agent
    if kind is not None:
        params["kind"] = kind
    if limit is not None:
        params["limit"] = limit
    if offset is not None:
        params["offset"] = offset
    qs = f"?{urlencode(params)}" if params else ""
    return _cx("GET", f"/reflective-journal{qs}")


def _op_rj_link(
    entry_id: int | None = None,
    to_entry: int | None = None,
    to_entity: str | None = None,
    link_type: str = "related",
    **_: object,
) -> dict[str, Any]:
    if entry_id is None:
        return {"error": "entry_id is required"}
    if to_entry is None and to_entity is None:
        return {"error": "Either to_entry or to_entity is required"}
    body: dict[str, object] = {"link_type": link_type}
    if to_entry is not None:
        body["to_entry"] = to_entry
    if to_entity is not None:
        body["to_entity"] = to_entity
    return _cx("POST", f"/reflective-journal/{entry_id}/links", body)


def _op_rj_consolidate(
    agent: str | None = None,
    register: str | None = None,
    entry: str | None = None,
    session_id: str | None = None,
    throughline: str | None = None,
    before: str | None = None,
    now: str | None = None,
    tension_points: list[str] | None = None,
    contradiction_set: list[str] | None = None,
    falsifier: str | None = None,
    rendered_shift: str | None = None,
    confidence: str | None = None,
    source_entry_ids: list[int] | None = None,
    **_: object,
) -> dict[str, Any]:
    """Convenience wrapper: writes a consolidation entry with structured data."""
    required = {
        "agent": agent,
        "register": register,
        "entry": entry,
        "throughline": throughline,
        "before": before,
        "now": now,
    }
    for field, val in required.items():
        if not val:
            return {"error": f"{field} is required"}

    consolidation_data: dict[str, Any] = {
        "throughline": throughline,
        "before": before,
        "now": now,
    }
    if tension_points:
        consolidation_data["tension_points"] = tension_points
    if contradiction_set:
        consolidation_data["contradiction_set"] = contradiction_set
    if falsifier is not None:
        consolidation_data["falsifier"] = falsifier
    if rendered_shift is not None:
        consolidation_data["rendered_shift"] = rendered_shift
    if confidence is not None:
        consolidation_data["confidence"] = confidence
    if source_entry_ids:
        consolidation_data["source_entry_ids"] = source_entry_ids

    links: list[dict[str, Any]] = []
    if source_entry_ids:
        for eid in source_entry_ids:
            links.append({"to_entry": eid, "link_type": "refines"})

    return _op_rj_write(
        agent=agent,
        register=register,
        entry=entry,
        kind="consolidation",
        session_id=session_id,
        links=links or None,
        consolidation_data=consolidation_data,
    )
