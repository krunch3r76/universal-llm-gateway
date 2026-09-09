"""continuity_tape_read v1 handler — wraps Stargate fetch_tape_envelope."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, override

from systems.continuity.tape_read import fetch_tape_envelope
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput

if TYPE_CHECKING:
    from systems.pipeline.core.domain_router import DomainRouter


class ContinuityTapeReadHandler(BaseHandler):
    """Pipeline step that renders agent-bus tape as ContinuityMessagesEnvelope."""

    step_type = "continuity_tape_read_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        options: dict[str, Any] = getattr(context, "options", {}) or {}
        thread = str(options.get("thread") or context.dispatch_thread_id or "")
        scope = str(options.get("scope", "full"))
        include_extras = bool(options.get("include_extras", False))
        tools = str(options.get("tools", "none"))
        budget_bytes = int(options.get("budget_bytes", 512_000))
        harvest = bool(options.get("harvest", False))
        envelope = options.get("envelope")
        caller_agent = str(options.get("from_agent") or "continuity")
        execution_id = str(context.execution_id or "")

        if envelope is not None:
            payload = {"envelope": envelope}
            return StepOutput(raw=json.dumps(payload, default=str), json=payload)

        wire, status = await fetch_tape_envelope(
            thread,
            scope=scope,
            include_extras=include_extras,
            tools=tools,
            budget_bytes=budget_bytes,
            harvest=harvest,
            caller_agent=caller_agent,
            door="pipeline",
            execution_id=execution_id,
        )
        if status >= 400:
            payload = {"envelope": None, "error": wire}
            return StepOutput(raw=json.dumps(payload, default=str), json=payload)
        payload = {"envelope": wire}
        return StepOutput(raw=json.dumps(payload, default=str), json=payload)


def register_handlers(router: DomainRouter) -> None:
    router.register_domain_handler_class(
        "continuity_tape_read",
        "continuity_tape_read_v1",
        ContinuityTapeReadHandler,
    )
    router.register_domain_handler_class(
        "continuity_checkpoint",
        "continuity_tape_read_v1",
        ContinuityTapeReadHandler,
    )


__all__ = ["ContinuityTapeReadHandler", "register_handlers"]
