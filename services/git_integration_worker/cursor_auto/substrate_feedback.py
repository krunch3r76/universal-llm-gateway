"""Systematic substrate-rot feedback to the operator seat after implement terminals."""

from __future__ import annotations

import json
from typing import Any

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


def extract_substrate_findings(text: str | None) -> list[str]:
    """Return plain-language lines that look like substrate rot from closeout text."""
    if not text:
        return []
    findings: list[str] = []
    for line in text.splitlines():
        low = line.lower()
        if any(marker in low for marker in _SUBSTRATE_MARKERS):
            stripped = line.strip()
            if stripped and stripped not in findings:
                findings.append(stripped)
    return findings


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
    client = bus or CursorBusClient()
    body = json.dumps(
        {
            "TYPE": "SUBSTRATE_FEEDBACK",
            "findings": findings,
            "thread_id": job.thread_id,
            "request_turn": job.turn_number,
            "note": (
                "Substrate rot observed during implement — operator carries graph write."
            ),
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
    }
