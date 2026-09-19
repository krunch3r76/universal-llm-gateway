"""Systematic substrate-rot feedback to the operator seat after implement terminals."""

from __future__ import annotations

import json
from typing import Any

from bus_watch.substrate_feedback import (
    extract_substrate_findings,
    resolve_substrate_feedback_entity_id,
    write_substrate_feedback_claim,
)

from services.git_integration_worker.cursor_auto.queue import AutoJob
from services.git_integration_worker.cursor_bus import CursorBusClient

__all__ = [
    "extract_substrate_findings",
    "resolve_substrate_feedback_entity_id",
    "maybe_post_substrate_feedback",
    "write_substrate_feedback_claim",
]


async def maybe_post_substrate_feedback(
    job: AutoJob,
    *,
    sdk_body: str | None,
    closeout_body: str | None,
    bus: CursorBusClient | None = None,
) -> dict[str, Any] | None:
    """Post friction-style finding turn when implement closeout cites substrate rot."""
    if job.contract not in {"implement", "verify"}:
        return None
    blob = "\n".join(part for part in (sdk_body, closeout_body) if part)
    findings = extract_substrate_findings(blob)
    if not findings:
        return None

    entity_id = resolve_substrate_feedback_entity_id(subject=job.subject, body=job.body)
    graph_write: dict[str, Any] | None = None
    if entity_id:
        graph_write = write_substrate_feedback_claim(
            entity_id=entity_id,
            findings=findings,
            evidence_uris=[f"agent-bus:{job.thread_id}"],
        )

    if entity_id and graph_write and "error" not in graph_write and not graph_write.get(
        "blocked"
    ):
        note = (
            "Substrate rot observed during implement — graph write via "
            "agent_bus(tool=\"substrate_graph_write\")."
        )
    elif entity_id and graph_write and graph_write.get("blocked"):
        note = (
            "Substrate rot observed during implement — graph write blocked as "
            f"near-duplicate (score={graph_write.get('score')})."
        )
    elif entity_id:
        note = (
            "Substrate rot observed during implement — "
            f"agent_bus(tool=\"substrate_graph_write\") failed for entity_id={entity_id!r}."
        )
    else:
        note = (
            "Substrate rot observed during implement — resolve entity_id (todo: or "
            "entity_id: line) then agent_bus(tool=\"substrate_graph_write\", "
            "entity_id=…, claim=…)."
        )

    client = bus or CursorBusClient()
    body = json.dumps(
        {
            "TYPE": "SUBSTRATE_FEEDBACK",
            "findings": findings,
            "thread_id": job.thread_id,
            "request_turn": job.turn_number,
            "note": note,
            "entity_id": entity_id,
            "graph_write": graph_write,
        },
        indent=2,
    )
    resp = await client.reply(
        thread_id=job.thread_id,
        to_agent=job.from_agent,
        from_agent="cursor-auto",
        subject="status:substrate-feedback — implement rot surfaced",
        body=body,
        allow_long_body=True,
    )
    return {
        "ok": resp.status_code < 400,
        "status_code": resp.status_code,
        "findings": findings,
        "entity_id": entity_id,
        "graph_write": graph_write,
    }
