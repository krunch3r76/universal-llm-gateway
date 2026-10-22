"""impresario_ear_deliver_v1 — post EAR | turn unless dry_run."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
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


_PATH_CITE_RE = re.compile(r"(?:[\w.~-]+/)+[\w.~-]+\.\w+")
_SHA_CITE_RE = re.compile(r"\b[0-9a-f]{7,64}\b")
_DQ_CITE_RE = re.compile(r'"([^"]{12,})"')
_BQ_CITE_RE = re.compile(r"`([^`]{12,})`")


def admit_friction_cite(complaint: str, gather: Mapping[str, Any]) -> bool:
    """True when complaint cites a path, SHA, or quote present in serialized gather."""
    blob = json.dumps(gather, sort_keys=True, default=str)
    paths = _PATH_CITE_RE.findall(complaint)
    shas = _SHA_CITE_RE.findall(complaint)
    quotes = _DQ_CITE_RE.findall(complaint) + _BQ_CITE_RE.findall(complaint)
    for path in paths:
        if path not in blob:
            return False
    for sha in shas:
        if sha not in blob:
            return False
    for path in paths:
        if path in blob:
            return True
    for sha in shas:
        if sha in blob:
            return True
    for quote in quotes:
        if quote in blob:
            return True
    return False


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
    raw_gather = evaluate.get("gather")
    gather = raw_gather if isinstance(raw_gather, dict) else None
    friction_admitted = bool(gather) and admit_friction_cite(body, gather)
    if dry_run:
        return {
            "skip": False,
            "dry_run": True,
            "subject": subject,
            "body": body,
            "to_agent": to_agent,
            "from_agent": _FROM,
            "posted": False,
            "friction_admitted": friction_admitted,
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
        "friction_admitted": friction_admitted,
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
