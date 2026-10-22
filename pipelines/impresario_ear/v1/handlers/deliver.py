"""impresario_ear_deliver_v1 — post EAR | turn unless dry_run."""

from __future__ import annotations

import json
from typing import Any, override

from bus_watch.impresario_ear_common import normalize_grok_to_agent
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput

_FROM = "impresario-ear"


def _compose_subject(root: str, verdict: str, signal: str | None = None) -> str:
    if verdict == "remiss_duty" and signal:
        return f"EAR | {root} — remiss_duty ({signal})"
    return f"EAR | {root} — {verdict}"


def _compose_degraded_subject(root: str, reason_token: str) -> str:
    """Subject line for SDK-degraded harvest notices (not EAR verdict posts)."""
    return f"DEGRADED | {root} — {reason_token}"


async def deliver_impresario_ear(
    *,
    resolve: dict[str, Any],
    evaluate: dict[str, Any],
    bus_send: Any | None = None,
) -> dict[str, Any]:
    if evaluate.get("skip"):
        return {"skip": True, "reason": "evaluate_skipped"}
    root = str(resolve.get("root_thread") or "")
    body = str(evaluate.get("body") or "").strip()
    dry_run = bool(resolve.get("dry_run"))
    raw_to = resolve.get("to_agent") or "cursor"
    to_agent = normalize_grok_to_agent(str(raw_to)) or str(raw_to).strip() or "cursor"
    if to_agent == _FROM:
        to_agent = "cursor"
    if evaluate.get("degraded_notice"):
        token = str(evaluate.get("degraded_reason_token") or "degraded")
        subject = _compose_degraded_subject(root, token)
    else:
        verdict = str(evaluate.get("verdict") or "unknown")
        subject = _compose_subject(root, verdict)
    if dry_run:
        return {
            "skip": False,
            "dry_run": True,
            "subject": subject,
            "body": body,
            "to_agent": to_agent,
            "from_agent": _FROM,
            "posted": False,
        }
    if bus_send is None:
        from pipelines.continuity_checkpoint.v1.handlers._clients import (
            bus_send as _send,
        )

        bus_send = _send
    payload, status = await bus_send(
        thread=root,
        from_agent=_FROM,
        to_agent=to_agent,
        subject=subject,
        body=body,
        after_turn=0,
    )
    if status >= 400:
        return {
            "skip": True,
            "error": {"code": "ear.deliver_failed", "status": status, "payload": payload},
        }
    turn_raw = payload.get("turn_number") if isinstance(payload, dict) else None
    return {
        "skip": False,
        "posted": True,
        "subject": subject,
        "body": body,
        "to_agent": to_agent,
        "from_agent": _FROM,
        "turn_number": turn_raw,
    }


class ImpresarioEarDeliverHandler(BaseHandler):
    step_type = "impresario_ear_deliver_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        outputs = getattr(context, "outputs", {}) or {}
        resolve = (outputs.get("resolve") or {}).json or {}
        evaluate = (outputs.get("evaluate") or {}).json or {}
        options = dict(getattr(context, "options", {}) or {})
        bus_send = options.get("_bus_send")
        payload = await deliver_impresario_ear(
            resolve=resolve,
            evaluate=evaluate,
            bus_send=bus_send,
        )
        return StepOutput(raw=json.dumps(payload, default=str), json=payload)
