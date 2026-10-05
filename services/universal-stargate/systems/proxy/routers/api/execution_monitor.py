"""Honest GET /api/v1/executions/{id} read ladder."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

from universal_protocol.status_basis import (
    SOURCE_PIPELINE_DISPATCH_JOURNAL,
    SOURCE_PIPELINE_TRACKER,
    status_basis,
)

from systems.pipeline.core.execution.async_tracker import PipelineExecutionRecord
from systems.pipeline.core.execution.dispatch_journal import fetch_record

from .dispatch_bus_recovery import recover_execution_from_bus_thread
from .execution_authority_read import map_authority_to_monitor, read_execution_authority

_FIVE_SOURCE_ORDER = (
    "pipeline_tracker",
    "pipeline_dispatch_journal",
    "cdp_registry.execution_state",
    "cdp_ask.execution_store",
    "thread_dispatch_links",
)


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _stargate_epoch() -> dict[str, Any]:
    return {"stargate_pid": os.getpid()}


def tracker_monitor_payload(record: PipelineExecutionRecord) -> dict[str, Any]:
    body = record.to_dict()
    return status_basis(
        "status",
        record.status,
        as_of=_utc_now_iso(),
        source=SOURCE_PIPELINE_TRACKER,
        scope=f"execution:{record.execution_id}",
        epoch=_stargate_epoch(),
        recovery={"consulted": ["pipeline_tracker"]},
        state=record.status,
        **{k: v for k, v in body.items() if k not in {"status", "state"}},
    )


async def resolve_execution_monitor(
    execution_id: str,
    *,
    tracker: Any,
    wait_seconds: float,
    event_bus: Any | None,
) -> tuple[int, dict[str, Any] | None]:
    """Return ``(http_status, body)`` for the monitor read ladder."""
    wait_clamped = min(max(0.0, wait_seconds), 60.0)
    record = await tracker.wait_for_terminal(execution_id, wait_clamped)
    if record is not None:
        return 200, tracker_monitor_payload(record)

    journal_record = await fetch_record(execution_id, event_bus=event_bus)
    if journal_record is not None:
        if journal_record.get("source") != SOURCE_PIPELINE_DISPATCH_JOURNAL:
            journal_record = {
                **journal_record,
                "source": SOURCE_PIPELINE_DISPATCH_JOURNAL,
                "scope": f"execution:{execution_id}",
                "recovery": {
                    "consulted": ["pipeline_tracker", "pipeline_dispatch_journal"],
                },
            }
        return 200, journal_record

    authority_result = await read_execution_authority(execution_id)
    authority_payload = authority_result.payload
    if authority_result.ok and authority_payload is not None:
        mapped = map_authority_to_monitor(
            authority_payload,
            execution_id=execution_id,
        )
        if mapped is not None:
            return 200, mapped

    if authority_result.degraded and event_bus is not None:
        import asyncio

        from systems.pipeline.core.events.dispatch.authority import (
            PipelineExecutionAuthorityUnreachable,
        )

        asyncio.create_task(
            event_bus.publish_nowait(
                PipelineExecutionAuthorityUnreachable(
                    execution_id=execution_id,
                    source_attempted="cdp_registry.execution_state",
                    reason=authority_result.reason or "unreachable",
                    fell_back_to="thread_dispatch_links",
                )
            )
        )

    recovered = await recover_execution_from_bus_thread(
        execution_id,
        url=tracker._agent_bus_url,
        auth_token=tracker._agent_bus_token,
        wait_seconds=wait_clamped,
    )
    bus_hit = recovered is not None
    if bus_hit:
        if authority_result.degraded and isinstance(recovered.get("recovery"), dict):
            recovered = {
                **recovered,
                "recovery": {
                    **recovered["recovery"],
                    "degraded": True,
                    "source_attempted": "cdp_registry.execution_state",
                },
            }
        return 200, recovered

    sources = sources_consulted_miss(
        authority_reason=authority_result.reason,
        authority_degraded=authority_result.degraded,
        authority_payload=authority_payload,
        bus_link_reason="miss" if bus_hit else "non_authoritative",
    )
    return 404, {
        "code": "execution_id_expired_or_unknown",
        "message": f"Unknown or expired execution_id '{execution_id}'.",
        "data": {"sources_consulted": sources},
    }


def sources_consulted_miss(
    *,
    authority_reason: str | None,
    authority_degraded: bool,
    authority_payload: dict[str, Any] | None,
    bus_link_reason: str,
) -> list[dict[str, Any]]:
    """Build the five-source miss list from actual ladder consultation."""
    satellite_rows: dict[str, dict[str, Any]] = {}
    if isinstance(authority_payload, dict):
        raw = authority_payload.get("sources_consulted")
        if isinstance(raw, list):
            for row in raw:
                if isinstance(row, dict) and row.get("source"):
                    satellite_rows[str(row["source"])] = row

    sources: list[dict[str, Any]] = [
        {"source": "pipeline_tracker", "hit": False, "reason": "miss"},
        {"source": "pipeline_dispatch_journal", "hit": False, "reason": "miss"},
    ]

    for name in ("cdp_registry.execution_state", "cdp_ask.execution_store"):
        sat = satellite_rows.get(name)
        if sat is not None:
            entry: dict[str, Any] = {
                "source": name,
                "hit": bool(sat.get("hit")),
            }
            if not entry["hit"]:
                entry["reason"] = str(sat.get("reason") or "miss")
            if name == "cdp_registry.execution_state" and authority_degraded:
                entry["degraded"] = True
            sources.append(entry)
            continue
        if name == "cdp_registry.execution_state":
            entry = {
                "source": name,
                "hit": False,
                "reason": authority_reason or "miss",
            }
            if authority_degraded:
                entry["degraded"] = True
            sources.append(entry)
        else:
            sources.append({"source": name, "hit": False, "reason": "miss"})

    sources.append(
        {
            "source": "thread_dispatch_links",
            "hit": False,
            "reason": bus_link_reason,
        }
    )
    assert [row["source"] for row in sources] == list(_FIVE_SOURCE_ORDER)
    return sources

