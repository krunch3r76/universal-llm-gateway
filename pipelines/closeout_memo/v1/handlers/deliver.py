"""closeout_memo_deliver_v1 — followup paste, harvest-before-retry, record receipt."""

from __future__ import annotations

import json
from typing import Any, override

from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput

from closeout_memo.events import emit_closeout_memo

from . import _ledger, _transport
from .deliver_policy import classify_followup, decide


def _step(payload: dict[str, Any], *, error: str | None = None) -> StepOutput:
    return StepOutput(raw=json.dumps(payload), json=payload, error=error)


def _prior(memo_ids: list[str]) -> tuple[str, int]:
    last_error = ""
    attempts = 0
    for memo_id in memo_ids:
        row = _ledger.load(memo_id)
        if row is None:
            continue
        last_error = str(row.get("last_error") or last_error)
        attempts = max(attempts, int(row.get("attempts") or 0))
    return last_error, attempts


async def apply_decision(
    *,
    memo_ids: list[str],
    wake_lane: str,
    prompt_text: str,
    followup: Any,
    harvest: Any,
) -> dict[str, Any]:
    """Run one delivery attempt. ``followup`` and ``harvest`` are async callables."""
    if not prompt_text:
        return {"needs_fallback": True, "error": "bus_only", "memo_ids": memo_ids}
    result = await followup(parent_thread=wake_lane, prompt_text=prompt_text)
    body = result.get("body") if isinstance(result, dict) else None
    kind = classify_followup(timed_out=bool(result.get("timed_out")), body=body)
    last_error, attempts = _prior(memo_ids)
    decision = decide(kind, last_error=last_error, attempts=attempts)
    registration_id = None
    url = None
    resolution_path = None
    streaming = None
    if isinstance(body, dict):
        registration_id = body.get("registration_id")
        url = body.get("url")
        resolution_path = body.get("resolution_path") or body.get("target_binding")
        streaming = body.get("streaming_at_paste")
    if decision.action == "harvest":
        marker = memo_ids[0] if memo_ids else ""
        present = await harvest(marker=marker, registration_id=registration_id)
        decision = decide(
            kind,
            last_error=last_error,
            attempts=attempts,
            harvest_present=bool(present),
        )
    if decision.action == "delivered":
        _ledger.mark_delivered(
            memo_ids,
            receipt=decision.receipt or "dom_committed",
            registration_id=None if registration_id is None else str(registration_id),
            url=None if url is None else str(url),
            resolution_path=None if resolution_path is None else str(resolution_path),
            streaming_at_paste=None if streaming is None else bool(streaming),
        )
        emit_closeout_memo(
            "delivered",
            memo_ids=memo_ids,
            wake_lane=wake_lane,
            receipt=decision.receipt,
            resolution_path=resolution_path,
            streaming_at_paste=streaming,
        )
        return {
            "needs_fallback": False,
            "delivered": True,
            "memo_ids": memo_ids,
            "receipt": decision.receipt,
            "registration_id": registration_id,
            "url": url,
            "resolution_path": resolution_path,
            "streaming_at_paste": streaming,
            "request_had_identity": False,
        }
    if decision.action == "retry":
        _ledger.schedule_retry(
            memo_ids, delay_s=decision.delay_s, error=decision.error
        )
        emit_closeout_memo(
            "retry_scheduled",
            memo_ids=memo_ids,
            wake_lane=wake_lane,
            error=decision.error,
            delay_s=decision.delay_s,
        )
        return {
            "needs_fallback": False,
            "delivered": False,
            "retry": True,
            "memo_ids": memo_ids,
            "error": decision.error,
        }
    return {
        "needs_fallback": True,
        "delivered": False,
        "memo_ids": memo_ids,
        "error": decision.error or kind,
        "wake_lane": wake_lane,
    }


class CloseoutMemoDeliverHandler(BaseHandler):
    step_type = "closeout_memo_deliver_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        outputs = getattr(context, "outputs", {}) or {}
        coalesced = getattr(outputs.get("coalesce"), "json", None) or {}
        if not isinstance(coalesced, dict) or not coalesced.get("rendered"):
            return _step({"ok": True, "needs_fallback": False, "skipped": True})
        memo_ids = list(coalesced.get("memo_ids") or [])
        wake_lane = str(coalesced.get("wake_lane") or "")
        if coalesced.get("bus_only"):
            return _step(
                {
                    "ok": True,
                    "needs_fallback": True,
                    "memo_ids": memo_ids,
                    "wake_lane": wake_lane,
                    "error": "bus_only",
                    "overflow_bus_text": coalesced.get("overflow_bus_text") or "",
                }
            )
        outcome = await apply_decision(
            memo_ids=memo_ids,
            wake_lane=wake_lane,
            prompt_text=str(coalesced.get("text") or ""),
            followup=_transport.post_followup,
            harvest=_transport.harvest_marker,
        )
        overflow = str(coalesced.get("overflow_bus_text") or "")
        if outcome.get("delivered") and overflow:
            await _transport.post_bus_turn(
                wake_lane=wake_lane,
                subject=f"closeout memo — overflow {memo_ids[0][:8]}",
                body=overflow,
            )
        outcome["overflow_bus_text"] = overflow
        outcome["wake_lane"] = wake_lane
        return _step({"ok": True, **outcome})
