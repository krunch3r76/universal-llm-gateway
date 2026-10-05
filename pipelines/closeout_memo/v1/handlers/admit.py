"""closeout_memo_admit_v1 — validate and INSERT OR IGNORE."""

from __future__ import annotations

import json
from typing import Any, override

from pydantic import ValidationError
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput

from closeout_memo.client import request_from_options
from closeout_memo.events import emit_closeout_memo

from . import _ledger
from .sweep import start_retry_sweep


def _step(payload: dict[str, Any], *, error: str | None = None) -> StepOutput:
    return StepOutput(raw=json.dumps(payload), json=payload, error=error)


class CloseoutMemoAdmitHandler(BaseHandler):
    step_type = "closeout_memo_admit_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        start_retry_sweep()
        opts = getattr(context, "options", {}) or {}
        try:
            request = request_from_options(opts)
        except ValidationError as exc:
            return _step({"ok": False, "error": "invalid_request"}, error=str(exc)[:400])
        for field_name in request.rejected_fields:
            emit_closeout_memo(
                "field_rejected",
                memo_id=request.memo_id,
                field=field_name,
            )
        outcome = _ledger.insert_admit(request.model_dump())
        if outcome == "deduped":
            emit_closeout_memo(
                "deduped",
                memo_id=request.memo_id,
                memo_key=request.memo_key,
                wake_lane=request.wake_lane,
            )
            return _step(
                {
                    "ok": True,
                    "deduped": True,
                    "memo_id": request.memo_id,
                    "wake_lane": request.wake_lane,
                }
            )
        emit_closeout_memo(
            "admitted",
            memo_id=request.memo_id,
            memo_key=request.memo_key,
            wake_lane=request.wake_lane,
            kind=request.kind,
        )
        return _step(
            {
                "ok": True,
                "deduped": False,
                "memo_id": request.memo_id,
                "wake_lane": request.wake_lane,
                "memo_key": request.memo_key,
            }
        )
