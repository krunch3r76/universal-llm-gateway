"""Systematic substrate-rot feedback to the operator seat after implement terminals."""

from __future__ import annotations

import json
import re
from typing import Any

from substrate_graph_write import write_claim

from services.git_integration_worker.cursor_auto.queue import AutoJob
from services.git_integration_worker.cursor_bus import CursorBusClient

_SUBSTRATE_MARKERS = (
    "audit",
    "infra rot",
    "substrate",
    "warning",
    "lint debt",
    "pre-existing",
)

_ENTITY_ID_LINE_RE = re.compile(r"(?im)^entity_id:\s*(\S+)")
_TODO_TOKEN_RE = re.compile(r"\btodo:[a-z0-9][a-z0-9._-]*", re.IGNORECASE)


def _line_carries_identical_true(line: str) -> bool:
    """True when a scraped probe line already carries ``\"identical\": true``."""
    try:
        payload = json.loads(line.strip())
    except json.JSONDecodeError:
        return False
    return isinstance(payload, dict) and payload.get("identical") is True


def extract_substrate_findings(text: str | None) -> list[str]:
    """Return plain-language lines that look like substrate rot from closeout text.

    Lines whose JSON already marks ``identical: true`` are not findings — the
    probe already distinguished byte-identical dirt from rot.
    """
    if not text:
        return []
    findings: list[str] = []
    for line in text.splitlines():
        if _line_carries_identical_true(line):
            continue
        low = line.lower()
        if any(marker in low for marker in _SUBSTRATE_MARKERS):
            stripped = line.strip()
            if stripped and stripped not in findings:
                findings.append(stripped)
    return findings


def resolve_substrate_feedback_entity_id(*, subject: str, body: str) -> str | None:
    """Resolve a graph-write target from directive subject/body."""
    blob = "\n".join(part for part in (subject, body) if part)
    entity_match = _ENTITY_ID_LINE_RE.search(blob)
    if entity_match:
        return entity_match.group(1).strip()
    todo_match = _TODO_TOKEN_RE.search(blob)
    if todo_match:
        return todo_match.group(0)
    return None


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
        claim = "Substrate rot observed during implement: " + "; ".join(findings[:5])
        graph_write = write_claim(
            entity_id=entity_id,
            claim=claim,
            evidence_uris=[f"agent-bus:{job.thread_id}"],
        )

    if entity_id and graph_write and "error" not in graph_write:
        note = (
            "Substrate rot observed during implement — graph write via "
            "agent_bus(tool=\"substrate_graph_write\")."
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
