"""Stage L1 grok pour (if_absent or prefix-extend) for harvest_seal."""

from __future__ import annotations

from typing import Any, override

from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput

from ._clients import fs_of, refuse, step_output_json
from ._harvest import (
    checkpoint_raw_messages_uri,
    stage_grok_pour,
    write_verbatim_l0,
)


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


async def _prefix_check(
    fs: Any,
    *,
    prior_uri: str,
    new_msgs: object,
    code: str = "harvest.not_prefix",
) -> dict[str, Any] | None:
    import json

    from cortex_store.verbatim_succession import prefix_holds

    old_blob = await fs.read(prior_uri)
    if not old_blob:
        return None
    try:
        old_msgs = json.loads(old_blob.decode("utf-8")).get("messages")
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        return refuse("harvest.no_prior", str(exc))
    if not prefix_holds(
        "messages-v1",
        _canonical_messages(old_msgs),
        _canonical_messages(new_msgs),
    ):
        return refuse(code, "new messages do not prefix-extend")
    return None


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
    raw_envelope = adapter.get("raw_envelope")
    raw_stats = adapter.get("raw_stats")
    if not isinstance(envelope, dict) or not isinstance(stats, dict):
        return refuse("pour.schema", "adapter produced no envelope")
    if not isinstance(raw_envelope, dict) or not isinstance(raw_stats, dict):
        return refuse("pour.schema", "adapter produced no raw envelope")
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
        msg_uri = f"notes/system/checkpoints/grok/{agent_id}/{n - 1}.messages.json"
        prior_blob = await fs.read(msg_uri)
        if not prior_blob:
            return refuse("harvest.no_prior", f"missing {msg_uri}")
        err = await _prefix_check(
            fs,
            prior_uri=msg_uri,
            new_msgs=envelope.get("messages"),
        )
        if err:
            return err
        raw_prior = checkpoint_raw_messages_uri(agent_id, n - 1)
        raw_err = await _prefix_check(
            fs,
            prior_uri=raw_prior,
            new_msgs=raw_envelope.get("messages"),
        )
        if raw_err:
            return raw_err
    from continuity_tape.pour_contract import PourRefused, validate_pour

    try:
        validate_pour(raw_envelope)
    except PourRefused as exc:
        return refuse(exc.code, exc.message)
    l0_err = await write_verbatim_l0(fs, agent_id, n, raw_bytes)
    if l0_err:
        return l0_err
    raw_uri = checkpoint_raw_messages_uri(agent_id, n)
    raw_pour = await stage_grok_pour(
        fs,
        agent_id,
        raw_envelope,
        stats=raw_stats,
        n=n,
        raw_bytes=None,
        l0_ref=l0_ref,
        pour_uri=raw_uri,
    )
    if raw_pour.get("refused"):
        return raw_pour
    transcribed = await stage_grok_pour(
        fs,
        agent_id,
        envelope,
        stats=stats,
        n=n,
        raw_bytes=None,
        l0_ref=l0_ref,
    )
    if transcribed.get("refused"):
        return transcribed
    from .transcript_store import maybe_persist_canonical_transcript

    transcript_store = await maybe_persist_canonical_transcript(
        fs,
        options=options,
        raw_bytes=raw_bytes,
        raw_envelope=raw_envelope,
        l0_ref=l0_ref,
    )
    out: dict[str, Any] = {
        **transcribed,
        "raw_messages_ref": raw_uri,
        "raw_messages_sha256": raw_stats.get("messages_sha256"),
        "transcript_store": transcript_store,
    }
    if transcript_store.get("status") == "error":
        out.setdefault("warnings", [])
        if isinstance(out["warnings"], list):
            out["warnings"] = list(out["warnings"])
            out["warnings"].append(
                f"transcript_store: {transcript_store.get('error', 'unknown')}"
            )
    return out


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
