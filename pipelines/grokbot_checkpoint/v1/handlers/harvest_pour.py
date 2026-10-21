"""Stage L1 grok pour (if_absent or prefix-extend) for harvest_seal."""

from __future__ import annotations

from typing import Any, override

from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput

from ._clients import fs_of, refuse, step_output_json
from ._harvest import stage_grok_pour


def _canonical_messages(messages: object) -> list[dict[str, Any]]:
    """Sorted-key copies so prefix_holds sees the same dict the pour file stores."""
    import json

    if not isinstance(messages, list):
        return []
    out: list[dict[str, Any]] = []
    for message in messages:
        if isinstance(message, dict):
            out.append(json.loads(json.dumps(message, sort_keys=True)))
    return out


async def run_harvest_pour(
    options: dict[str, Any],
    adapter: dict[str, Any],
    fs: Any,
    normalise: dict[str, Any] | None = None,
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
    from .harvest_normalise import load_raw_for_pour

    loaded = await load_raw_for_pour(options, fs)
    if isinstance(loaded, dict) and loaded.get("refused"):
        return loaded
    raw_bytes = loaded if isinstance(loaded, bytes) else None
    if raw_bytes is None:
        return refuse("source.missing", f"L0 capture {l0_ref} absent")
    from ._clients import file_sha256

    digest = file_sha256(raw_bytes)
    sealed = str((normalise or {}).get("l0_sha256") or "").strip()
    if sealed and digest != sealed:
        return refuse("source.sha_mismatch", f"{digest} != {sealed}")
    if n > 1:
        import json

        from cortex_store.verbatim_succession import prefix_holds

        msg_uri = f"notes/system/checkpoints/grok/{agent_id}/{n - 1}.messages.json"
        old_blob = await fs.read(msg_uri)
        if not old_blob:
            return refuse("harvest.no_prior", f"missing {msg_uri}")
        try:
            old_msgs = json.loads(old_blob.decode("utf-8")).get("messages")
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            return refuse("harvest.no_prior", str(exc))
        new_msgs = envelope.get("messages")
        if not prefix_holds(
            "messages-v1",
            _canonical_messages(old_msgs),
            _canonical_messages(new_msgs),
        ):
            return refuse("harvest.not_prefix", "new messages do not prefix-extend")
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
        from .admit import bind_admitted_n

        options = bind_admitted_n(
            dict(getattr(context, "options", {}) or {}),
            getattr(context, "outputs", {}) or {},
        )
        outputs = getattr(context, "outputs", {}) or {}
        adapter = step_output_json(outputs, "harvest_adapter")
        normalise = step_output_json(outputs, "harvest_normalise")
        payload = await run_harvest_pour(options, adapter, fs_of(context), normalise)
        return StepOutput(raw="", json=payload)
