"""web-tab-write pipeline step — grant-gated CDP actuation."""

from __future__ import annotations

import json
from typing import Any, override

from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput
from web_tab.write_ops import dispatch_write_op


class WebTabWriteHandler(BaseHandler):
    step_type = "web_tab_write_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        opts = getattr(context, "options", {}) or {}
        op_raw = str(opts.get("op") or "").strip()
        if op_raw not in {
            "open",
            "bind",
            "paste",
            "submit",
            "verify_committed",
            "read_reply",
        }:
            bad = {"ok": False, "error": f"unsupported op {op_raw!r}"}
            return StepOutput(raw=json.dumps(bad), json=bad)
        result = await dispatch_write_op(op_raw, opts)  # type: ignore[arg-type]
        return StepOutput(raw=json.dumps(result), json=result)
