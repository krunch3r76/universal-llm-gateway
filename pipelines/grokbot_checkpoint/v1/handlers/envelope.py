"""Immutable snapshot of a validated pour. Prefix-extend or refuse."""

from __future__ import annotations

import json
from typing import Any, override

from continuity_tape.messages import seal_messages_canonical_bytes
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput

from ._clients import file_sha256, fs_of, refuse, rel_path_ok, step_output_json


def _prefix_holds(old: list[Any], new: list[Any]) -> bool:
    try:
        from cortex_store.verbatim_succession import prefix_holds

        return bool(prefix_holds("messages-v1", old, new))
    except TypeError:
        if len(new) < len(old):
            return False
        return seal_messages_canonical_bytes(old) in seal_messages_canonical_bytes(new)


async def run_envelope(
    options: dict[str, Any], source: dict[str, Any], fs: Any
) -> dict[str, Any]:
    uri = str(
        options.get("snapshot_uri")
        or (
            f"notes/system/threads/{options.get('house')}-"
            f"{str(options.get('agent_id') or '')[:8]}.{source.get('provenance')}.messages.json"
        )
    )
    if not rel_path_ok(uri):
        return refuse("admit.path_escape", f"refused path {uri}")
    envelope = source.get("envelope")
    if not isinstance(envelope, dict):
        return refuse("envelope.missing", "source produced no envelope")
    raw = json.dumps(envelope, ensure_ascii=False, sort_keys=True).encode("utf-8")
    existing = fs.read(uri)
    messages_sha = str(source.get("messages_sha256") or "")
    if existing is None:
        written = fs.write(uri, raw, expected_sha256=None)
        return {
            "refused": None,
            "snapshot_uri": uri,
            "sha256": written["sha256"],
            "messages_sha256": messages_sha,
            "action": "write",
            "provenance": source.get("provenance"),
            "producer": source.get("producer"),
            "coverage": source.get("coverage"),
            "turn_count": source.get("turn_count"),
            "surface": source.get("surface"),
            "capture": source.get("capture"),
        }
    try:
        old = json.loads(existing.decode("utf-8"))
    except json.JSONDecodeError:
        return refuse("envelope.not_prefix", "existing snapshot is not json")
    old_sha = str((old.get("meta") or {}).get("messages_sha256") or "")
    if old_sha == messages_sha:
        return {
            "refused": None,
            "snapshot_uri": uri,
            "sha256": file_sha256(existing),
            "messages_sha256": messages_sha,
            "action": "skip",
            "provenance": source.get("provenance"),
            "producer": source.get("producer"),
            "coverage": source.get("coverage"),
            "turn_count": source.get("turn_count"),
            "surface": source.get("surface"),
            "capture": source.get("capture"),
        }
    old_msgs = old.get("messages") if isinstance(old, dict) else None
    new_msgs = envelope.get("messages")
    if not isinstance(old_msgs, list) or not isinstance(new_msgs, list):
        return refuse("envelope.not_prefix", "messages missing")
    if not _prefix_holds(old_msgs, new_msgs):
        return refuse("envelope.not_prefix", "new messages do not prefix-extend")
    written = fs.write(uri, raw, expected_sha256=file_sha256(existing))
    if not written.get("ok"):
        return refuse("envelope.not_prefix", "guarded overwrite lost the race")
    return {
        "refused": None,
        "snapshot_uri": uri,
        "sha256": written["sha256"],
        "messages_sha256": messages_sha,
        "action": "overwrite",
        "provenance": source.get("provenance"),
        "producer": source.get("producer"),
        "coverage": source.get("coverage"),
            "turn_count": source.get("turn_count"),
            "surface": source.get("surface"),
            "capture": source.get("capture"),
        }


class GrokbotCheckpointEnvelopeHandler(BaseHandler):
    step_type = "grokbot_checkpoint_envelope_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        options = dict(getattr(context, "options", {}) or {})
        source = step_output_json(getattr(context, "outputs", {}) or {}, "source")
        payload = await run_envelope(options, source, fs_of(context))
        return StepOutput(raw="", json=payload)
