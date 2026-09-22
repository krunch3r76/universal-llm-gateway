"""Derive CONSULT_PENDING wait block without long-poll."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, override

from claude_bundles.conductor_stop import is_consult_pending_wait
from operator_hop_harvest.parse import parse_wait_block
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput

from ._clients import step_output_json


def _recon_body_from_closeout(closeout: str) -> tuple[str | None, str | None]:
    import re

    match = re.search(
        r"cortex://notes/system/recon/[^\s\"']+\.md",
        closeout or "",
    )
    if not match:
        return None, None
    uri = match.group(0)
    rel = uri[len("cortex://") :]
    path = Path("/mnt/torus/mcp-data/files") / rel
    if not path.is_file():
        return uri, None
    return uri, path.read_text(encoding="utf-8")


class OperatorHopHarvestFetchWaitHandler(BaseHandler):
    step_type = "operator_hop_harvest_fetch_wait_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        outputs = getattr(context, "step_outputs", {}) or {}
        bus = step_output_json(outputs, "fetch_bus")
        closeout = str(bus.get("closeout_turn_body") or "")
        sidecar_uri, recon_body = _recon_body_from_closeout(closeout)
        wait = parse_wait_block(
            closeout_body=closeout,
            recon_sidecar_body=recon_body,
        )
        if sidecar_uri:
            wait["sidecar_uri"] = sidecar_uri
        closeout_turn = bus.get("closeout_turn")
        if isinstance(closeout_turn, int) and is_consult_pending_wait(closeout):
            wait["since"] = closeout_turn
        out = {"wait": wait}
        return StepOutput(raw=json.dumps(out), json=out)
