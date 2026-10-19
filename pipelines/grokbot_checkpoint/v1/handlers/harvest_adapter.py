"""Run grok L0 adapter twice and require stable ``messages_sha256``."""

from __future__ import annotations

from typing import Any, override

from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput

from ._clients import FsClientMissingError, fs_of, refuse, step_output_json
from ._harvest import run_adapter_twice


async def run_harvest_adapter(
    options: dict[str, Any], normalise: dict[str, Any], fs: Any
) -> dict[str, Any]:
    if normalise.get("refused"):
        return refuse("envelope.upstream_skipped", "normalise refused")
    norm_ref = str(normalise.get("normalized_ref") or "")
    norm_sha = str(normalise.get("normalized_sha256") or "")
    agent_id = str(options.get("agent_id") or "").strip()
    if not agent_id:
        return refuse("admit.agent_id", "agent_id required")
    try:
        raw = await fs.read(norm_ref)
    except FsClientMissingError as exc:
        return refuse(exc.code, str(exc))
    if raw is None:
        return refuse("source.missing", f"normalized capture {norm_ref} absent")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        return refuse("pour.schema", str(exc))
    bus_identity = str(options.get("bus_identity") or "") or None
    body, err = run_adapter_twice(
        text,
        agent_id=agent_id,
        capture_ref=norm_ref,
        capture_sha256=norm_sha,
        bus_identity=bus_identity,
        capture_kind="dom_harvest"
        if str(options.get("fallback") or "") == "electron"
        else "grok_export",
    )
    if err:
        return err
    assert body is not None
    return {
        "refused": None,
        "envelope": body["envelope"],
        "stats": body["stats"],
        "normalized_ref": norm_ref,
        "normalized_sha256": norm_sha,
    }


class GrokbotCheckpointHarvestAdapterHandler(BaseHandler):
    step_type = "grokbot_checkpoint_harvest_adapter_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        options = dict(getattr(context, "options", {}) or {})
        outputs = getattr(context, "outputs", {}) or {}
        normalise = step_output_json(outputs, "harvest_normalise")
        payload = await run_harvest_adapter(options, normalise, fs_of(context))
        return StepOutput(raw="", json=payload)
