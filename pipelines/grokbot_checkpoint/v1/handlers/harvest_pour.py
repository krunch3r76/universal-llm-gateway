"""Stage L1 grok pour (if_absent or prefix-extend) for harvest_seal."""

from __future__ import annotations

from typing import Any, override

from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput

from ._clients import fs_of, refuse, step_output_json
from ._harvest import stage_grok_pour


async def run_harvest_pour(
    options: dict[str, Any], adapter: dict[str, Any], fs: Any
) -> dict[str, Any]:
    if adapter.get("refused"):
        return refuse("envelope.upstream_skipped", "adapter refused")
    agent_id = str(options.get("agent_id") or "").strip()
    if not agent_id:
        return refuse("admit.agent_id", "agent_id required")
    envelope = adapter.get("envelope")
    stats = adapter.get("stats")
    if not isinstance(envelope, dict) or not isinstance(stats, dict):
        return refuse("pour.schema", "adapter produced no envelope")
    n = int(options.get("n") or 1)
    l0_ref = str(options.get("l0_ref") or "").strip()
    raw_bytes = await fs.read(l0_ref) if l0_ref else None
    return await stage_grok_pour(
        fs,
        agent_id,
        envelope,
        stats=stats,
        n=n,
        raw_bytes=raw_bytes,
        l0_ref=l0_ref,
    )


class GrokbotCheckpointHarvestPourHandler(BaseHandler):
    step_type = "grokbot_checkpoint_harvest_pour_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        options = dict(getattr(context, "options", {}) or {})
        outputs = getattr(context, "outputs", {}) or {}
        adapter = step_output_json(outputs, "harvest_adapter")
        payload = await run_harvest_pour(options, adapter, fs_of(context))
        return StepOutput(raw="", json=payload)
