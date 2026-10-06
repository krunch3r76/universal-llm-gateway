"""Serialize packet JSON as terminal raw output."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, override

from maestro_induct.assemble import dumps_packet
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput

from . import _clients


class MaestroInductEmitHandler(BaseHandler):
    step_type = "maestro_induct_emit_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        assemble = _clients.step_output_json(context.outputs, "assemble")
        resolve = _clients.step_output_json(context.outputs, "resolve")
        started = float(resolve.get("started_epoch") or _clients.now_epoch())
        now = _clients.now_epoch()
        meta = assemble.setdefault("meta", {})
        meta["generated_at"] = datetime.fromtimestamp(now, UTC).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        meta["latency_ms"] = min(99999999, max(0, int((now - started) * 1000)))
        raw = dumps_packet(assemble)
        return StepOutput(raw=raw, json=assemble)
