"""Single-step Grok Bot computer control for status, wake, and update.

The grokbot-computer-update pipeline is the caller. Display numbers resolve
to CDP ports through ``config/grok-windows.json``.
"""

from __future__ import annotations

import json
from typing import Any, override

from cursor_bridge.grok_computer import (
    probe_grok_computer,
    resolve_grok_cdp_port,
    run_grok_computer_update,
    wake_grok_computer,
)
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput


class GrokbotComputerUpdateHandler(BaseHandler):
    """Dispatch ``pipeline_options.action`` to the Grok Bot computer helpers.

    Display numbers use ``config/grok-windows.json`` for the CDP port when
    the caller leaves the pipeline default of 9339 in place.
    """

    step_type = "grokbot_computer_update_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        opts = getattr(context, "options", {}) or {}
        action = str(opts.get("action") or "status").strip().lower()
        display = str(opts.get("display") or "9")
        cdp_port = resolve_grok_cdp_port(display, opts.get("cdp_port"))
        require_idle = True if opts.get("require_idle") is None else bool(opts.get("require_idle"))

        if action == "wake":
            result = await wake_grok_computer(cdp_port=cdp_port, display=display)
        elif action == "update":
            result = await run_grok_computer_update(
                cdp_port=cdp_port,
                display=display,
                require_idle=require_idle,
            )
        else:
            result = await probe_grok_computer(cdp_port=cdp_port, display=display)

        raw = json.dumps(result)
        return StepOutput(raw=raw, json=result)
