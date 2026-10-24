"""impresario_ear_resolve_v1 — bind root, mode, hop watermark, echo guard."""

from __future__ import annotations

import json
from typing import Any, override

from bus_watch.impresario_ear_common import (
    ear_echo_should_skip,
    hop_watermark_key,
    hop_watermark_present,
    normalize_grok_to_agent,
    try_claim_hop_watermark,
)
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput


def resolve_impresario_ear(
    *,
    options: dict[str, Any],
    dispatch_thread_id: str | None,
    fetch_turns: Any = None,
) -> dict[str, Any]:
    root = str(
        options.get("root_thread") or options.get("root") or dispatch_thread_id or ""
    ).strip()
    if not dispatch_thread_id or not str(dispatch_thread_id).strip():
        return {
            "skip": True,
            "error": {"code": "ear.dispatch_thread_missing", "message": "missing"},
        }
    if not root:
        root = str(dispatch_thread_id).strip()
    mode = str(options.get("mode") or "deep").strip().lower()
    if mode not in {"patrol", "deep"}:
        return {
            "skip": True,
            "error": {"code": "ear.mode_invalid", "message": f"mode={mode!r}"},
        }
    trigger = str(options.get("trigger") or "on_demand")
    force = bool(options.get("force"))
    dry_run = bool(options.get("dry_run"))
    departing = str(options.get("departing_transcript_id") or "").strip() or None
    hop_turn = options.get("hop_turn")
    hop_turn_i = int(hop_turn) if hop_turn is not None else None
    watermark = hop_watermark_key(
        root, departing_transcript_id=departing, hop_turn=hop_turn_i
    )
    raw_to = options.get("to_agent") or options.get("to")
    to_agent = normalize_grok_to_agent(str(raw_to)) if raw_to else None
    if to_agent is None and isinstance(raw_to, str) and raw_to.strip():
        to_agent = str(raw_to).strip()
    if fetch_turns and ear_echo_should_skip(
        root_thread=root, force=force, fetch_turns=fetch_turns
    ):
        return {"skip": True, "reason": "ear_echo_guard"}
    if mode == "deep":
        return {"skip": True, "reason": "deep_mode_disabled"}
    if trigger == "hop":
        if hop_watermark_present(watermark):
            return {"skip": True, "reason": "hop_watermark_present", "watermark": watermark}
        if not try_claim_hop_watermark(watermark):
            return {"skip": True, "reason": "hop_watermark_claim_failed", "watermark": watermark}
    return {
        "skip": False,
        "root_thread": root,
        "mode": mode,
        "trigger": trigger,
        "dry_run": dry_run,
        "force": force,
        "to_agent": to_agent,
        "departing_transcript_id": departing,
        "hop_turn": hop_turn_i,
        "watermark": watermark,
    }


class ImpresarioEarResolveHandler(BaseHandler):
    step_type = "impresario_ear_resolve_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        options = dict(getattr(context, "options", {}) or {})
        dispatch_thread_id = getattr(context, "dispatch_thread_id", None)
        fetch_turns = options.get("_fetch_turns")
        if fetch_turns is None:
            from bus_watch.endpoint_wake_transport import StoreClient

            _store = StoreClient()
            fetch_turns = _store.turns
        payload = resolve_impresario_ear(
            options=options,
            dispatch_thread_id=dispatch_thread_id,
            fetch_turns=fetch_turns,
        )
        return StepOutput(raw=json.dumps(payload), json=payload)
