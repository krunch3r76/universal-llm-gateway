"""closeout_memo_fallback_v1 — one bus turn per undelivered lane (no phone page)."""

from __future__ import annotations

import json
from typing import Any, override

from closeout_memo.events import emit_closeout_memo
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput

from . import _ledger, _transport


def _step(payload: dict[str, Any], *, error: str | None = None) -> StepOutput:
    return StepOutput(raw=json.dumps(payload), json=payload, error=error)


def _blocks(deliver: dict[str, Any]) -> str:
    """Primary paste text and the overflow pointers. Dropping either loses a memo."""
    text = str(deliver.get("text") or "").strip()
    overflow = str(deliver.get("overflow_bus_text") or "").strip()
    if text and overflow:
        return f"{text}\n{overflow}"
    return text or overflow


class CloseoutMemoFallbackHandler(BaseHandler):
    step_type = "closeout_memo_fallback_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        outputs = getattr(context, "outputs", {}) or {}
        deliver = getattr(outputs.get("deliver"), "json", None) or {}
        coalesce = getattr(outputs.get("coalesce"), "json", None) or {}
        if not isinstance(deliver, dict):
            deliver = {}
        if not deliver.get("needs_fallback"):
            return _step({"ok": True, "skipped": True})
        memo_ids = list(deliver.get("memo_ids") or [])
        wake_lane = str(deliver.get("wake_lane") or coalesce.get("wake_lane") or "")
        kind = "sdk_closeout"
        status = "failed"
        if memo_ids:
            row = _ledger.load(memo_ids[0])
            if row is not None:
                kind = str(row.get("kind") or kind)
                status = str(row.get("status") or status)
        memo8 = memo_ids[0][:8] if memo_ids else "unknown"
        subject = f"closeout memo — undelivered {memo8} {kind} status:{status}"
        body = _blocks({**coalesce, **deliver})
        posted = await _transport.post_bus_turn(
            wake_lane=wake_lane, subject=subject, body=body
        )
        # Phone page retired: undelivered memos are interagent (bus → wake
        # lane). harvest_no_target / untracked Maestro tab is a separate
        # tracking fix — paging the human was the wrong place.
        _ledger.mark_undelivered(
            memo_ids, error=str(deliver.get("error") or "undelivered")
        )
        emit_closeout_memo(
            "undelivered",
            memo_ids=memo_ids,
            wake_lane=wake_lane,
            bus_posted=posted,
            paged=False,
        )
        return _step(
            {
                "ok": True,
                "undelivered": True,
                "memo_ids": memo_ids,
                "bus_posted": posted,
                "paged": False,
                "subject": subject,
            }
        )
