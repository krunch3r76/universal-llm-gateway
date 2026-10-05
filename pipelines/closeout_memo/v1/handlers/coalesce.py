"""closeout_memo_coalesce_v1 — per-lane gather, pointer render, event."""

from __future__ import annotations

import asyncio
import json
from typing import Any, override

from closeout_memo.events import emit_closeout_memo
from closeout_memo.models import CloseoutMemoRequest
from closeout_memo.render import render_memos
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput

from . import _ledger

_LANE_LOCKS: dict[str, asyncio.Lock] = {}


def _lane_lock(wake_lane: str) -> asyncio.Lock:
    lock = _LANE_LOCKS.get(wake_lane)
    if lock is None:
        lock = asyncio.Lock()
        _LANE_LOCKS[wake_lane] = lock
    return lock


def _step(payload: dict[str, Any], *, error: str | None = None) -> StepOutput:
    return StepOutput(raw=json.dumps(payload), json=payload, error=error)


class CloseoutMemoCoalesceHandler(BaseHandler):
    step_type = "closeout_memo_coalesce_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        outputs = getattr(context, "outputs", {}) or {}
        admit = getattr(outputs.get("admit"), "json", None) or {}
        if not isinstance(admit, dict):
            admit = {}
        wake_lane = str(admit.get("wake_lane") or "")
        if not wake_lane:
            return _step({"ok": False, "error": "missing_wake_lane"}, error="missing_wake_lane")
        async with _lane_lock(wake_lane):
            claimed = _ledger.claim_admitted(wake_lane, limit=5)
        if not claimed:
            return _step({"ok": True, "rendered": False, "memo_ids": []})
        memos: list[CloseoutMemoRequest] = []
        for row in claimed:
            payload = json.loads(row["payload_json"])
            memos.append(CloseoutMemoRequest.model_validate(payload))
        rendered = render_memos(memos)
        for field_name in rendered.rejected_fields:
            emit_closeout_memo("field_rejected", field=field_name, wake_lane=wake_lane)
        ids = [row["memo_id"] for row in claimed]
        _ledger.store_render(
            ids,
            text=rendered.text,
            sha256=rendered.sha256,
            overflow=rendered.overflow_bus_text,
        )
        emit_closeout_memo(
            "rendered",
            memo_ids=ids,
            bytes=rendered.byte_len,
            sha256=rendered.sha256,
            text=rendered.text,
            wake_lane=wake_lane,
        )
        return _step(
            {
                "ok": True,
                "rendered": True,
                "memo_ids": ids,
                "text": rendered.text,
                "sha256": rendered.sha256,
                "bytes": rendered.byte_len,
                "bus_only": rendered.bus_only,
                "overflow_bus_text": rendered.overflow_bus_text,
                "wake_lane": wake_lane,
            }
        )
