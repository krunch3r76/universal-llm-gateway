"""closeout_memo_deliver_v1 — followup paste, harvest-before-retry, record receipt."""

from __future__ import annotations

import json
from typing import Any, override

from closeout_memo.events import emit_closeout_memo
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput

from . import _ledger, _transport
from .coalesce import _lane_lock
from .deliver_policy import DeliveryDecision, classify_followup, decide


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


def _address_retry_skip_reason(
    *,
    status_code: int,
    attended_body: dict[str, Any] | None,
) -> str:
    if status_code == 200:
        return "get_200_current"
    if status_code == 409:
        if isinstance(attended_body, dict):
            return str(attended_body.get("reason") or "lane_cse_ambiguous")
        return "lane_cse_ambiguous"
    if status_code == 503:
        return "lane_cse_probe_error"
    if status_code == 0:
        return "get_transport_failure"
    if status_code == 404:
        if not isinstance(attended_body, dict):
            return "missing_seat_holder"
        holder = attended_body.get("seat_holder")
        if not isinstance(holder, dict):
            return "missing_seat_holder"
        chat = str(holder.get("chat_url") or "").strip()
        if not chat:
            return "empty_chat_url"
    return f"get_status_{status_code}"


def _body_fields(body: dict[str, Any] | None) -> tuple[Any, ...]:
    if not isinstance(body, dict):
        return None, None, None, None, None, None
    registration_id = body.get("registration_id")
    url = body.get("url")
    resolution_path = body.get("resolution_path") or body.get("target_binding")
    streaming = body.get("streaming_at_paste")
    reattach_used = body.get("reattach_used")
    lane_created = body.get("lane_created")
    return registration_id, url, resolution_path, streaming, reattach_used, lane_created


async def apply_decision(
    *,
    memo_ids: list[str],
    wake_lane: str,
    prompt_text: str,
    followup: Any,
    harvest: Any,
    attended: Any | None = None,
    followup_by_address: Any | None = None,
) -> dict[str, Any]:
    """Run one delivery attempt. ``followup`` and ``harvest`` are async callables."""
    if not prompt_text:
        return {"needs_fallback": True, "error": "bus_only", "memo_ids": memo_ids}
    result = await followup(parent_thread=wake_lane, prompt_text=prompt_text)
    body = result.get("body") if isinstance(result, dict) else None
    kind = classify_followup(timed_out=bool(result.get("timed_out")), body=body)
    address_retry = False
    reattach_chat_url: str | None = None
    if (
        kind == "lane_cse_none"
        and attended is not None
        and followup_by_address is not None
    ):
        attended_result = await attended(parent_thread=wake_lane)
        status_code = int(attended_result.get("status_code") or 0)
        attended_body = attended_result.get("body")
        if not isinstance(attended_body, dict):
            attended_body = None
        holder_url: str | None = None
        holder_registration_id: str | None = None
        if status_code == 404 and isinstance(attended_body, dict):
            holder = attended_body.get("seat_holder")
            if isinstance(holder, dict):
                holder_url = str(holder.get("chat_url") or "").strip() or None
                holder_registration_id = (
                    str(holder.get("registration_id") or "").strip() or None
                )
        if holder_url:
            reattach_chat_url = holder_url
            address_retry = True
            emit_closeout_memo(
                "address_retry",
                memo_ids=memo_ids,
                wake_lane=wake_lane,
                outcome="attempted",
                reason="lane_cse_none",
                registration_id_sent=bool(holder_registration_id),
            )
            result = await followup_by_address(
                parent_thread=wake_lane,
                prompt_text=prompt_text,
                chat_url=holder_url,
                registration_id=holder_registration_id,
            )
            body = result.get("body") if isinstance(result, dict) else None
            kind = classify_followup(
                timed_out=bool(result.get("timed_out")), body=body
            )
        else:
            skip = _address_retry_skip_reason(
                status_code=status_code,
                attended_body=attended_body,
            )
            emit_closeout_memo(
                "address_retry",
                memo_ids=memo_ids,
                wake_lane=wake_lane,
                outcome="skipped",
                reason=skip,
            )
    last_error, attempts = _prior(memo_ids)
    decision = decide(kind, last_error=last_error, attempts=attempts)
    registration_id, url, resolution_path, streaming, reattach_used, lane_created = (
        _body_fields(body if isinstance(body, dict) else None)
    )
    if decision.action == "harvest":
        marker = memo_ids[0] if memo_ids else ""
        present = await harvest(
            marker=marker,
            registration_id=registration_id,
            parent_thread=wake_lane,
            chat_url=reattach_chat_url,
        )
        if present is None:
            decision = DeliveryDecision(action="fallback", error="harvest_no_target")
        else:
            decision = decide(
                kind,
                last_error=last_error,
                attempts=attempts,
                harvest_present=bool(present),
            )
    if decision.action == "delivered":
        ledger_url = url if url else reattach_chat_url
        _ledger.mark_delivered(
            memo_ids,
            receipt=decision.receipt or "dom_committed",
            registration_id=None if registration_id is None else str(registration_id),
            url=None if ledger_url is None else str(ledger_url),
            resolution_path=None if resolution_path is None else str(resolution_path),
            streaming_at_paste=None if streaming is None else bool(streaming),
        )
        delivered_reattach = (
            False
            if not address_retry
            else (False if reattach_used is None else bool(reattach_used))
        )
        emit_closeout_memo(
            "delivered",
            memo_ids=memo_ids,
            wake_lane=wake_lane,
            receipt=decision.receipt,
            resolution_path=resolution_path,
            streaming_at_paste=streaming,
            address_retry=address_retry,
            reattach_chat_url=reattach_chat_url if address_retry else None,
            reattach_used=delivered_reattach,
            lane_created=(
                False
                if not address_retry
                else (False if lane_created is None else bool(lane_created))
            ),
        )
        return {
            "needs_fallback": False,
            "delivered": True,
            "memo_ids": memo_ids,
            "receipt": decision.receipt,
            "registration_id": registration_id,
            "url": ledger_url,
            "resolution_path": resolution_path,
            "streaming_at_paste": streaming,
            "request_had_identity": address_retry,
            "address_retry": address_retry,
            "reattach_used": delivered_reattach,
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
        async with _lane_lock(wake_lane):
            outcome = await apply_decision(
                memo_ids=memo_ids,
                wake_lane=wake_lane,
                prompt_text=str(coalesced.get("text") or ""),
                followup=_transport.post_followup,
                harvest=_transport.harvest_marker,
                attended=_transport.get_lane_attended,
                followup_by_address=_transport.post_followup_by_address,
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
