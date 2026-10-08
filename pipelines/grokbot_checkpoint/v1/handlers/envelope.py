"""Immutable snapshot of a validated pour. Prefix-extend or refuse."""

from __future__ import annotations

import json
from typing import Any, override

from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput

from ._clients import file_sha256, fs_of, refuse, rel_path_ok, step_output_json


def _producer_id(meta: dict[str, Any]) -> str:
    sources = meta.get("sources")
    if isinstance(sources, list) and sources and isinstance(sources[0], dict):
        producer = sources[0].get("producer")
        if isinstance(producer, dict):
            return str(producer.get("id") or "")
    return ""


def _same_pour_identity(old_meta: dict[str, Any], source: dict[str, Any]) -> bool:
    """Same messages hash is not the same snapshot when provenance differs."""
    old_prov = str(old_meta.get("provenance") or "")
    new_prov = str(source.get("provenance") or "")
    if old_prov and new_prov and old_prov != new_prov:
        return False
    old_prod = _producer_id(old_meta)
    new_prod = str(source.get("producer") or "")
    if old_prod and new_prod and old_prod != new_prod:
        return False
    old_cap = old_meta.get("capture")
    new_cap = source.get("capture")
    old_ref = old_cap.get("ref") if isinstance(old_cap, dict) else None
    new_ref = new_cap.get("ref") if isinstance(new_cap, dict) else None
    return old_ref == new_ref


def _prefix_holds(old: list[Any], new: list[Any]) -> bool:
    from cortex_store.verbatim_succession import prefix_holds

    return bool(prefix_holds("messages-v1", old, new))


async def run_envelope(
    options: dict[str, Any], source: dict[str, Any], fs: Any
) -> dict[str, Any]:
    if source.get("_skipped") or source.get("refused") or not source.get("messages_sha256"):
        return refuse("envelope.upstream_skipped", "source did not yield a pour")
    uri = str(
        options.get("snapshot_uri")
        or (
            "notes/system/transcripts/grok/"
            f"{options.get('house')}-{str(options.get('agent_id') or '')[:8]}."
            f"{source.get('provenance')}.messages.json"
        )
    )
    if not rel_path_ok(uri):
        return refuse("admit.path_escape", f"refused path {uri}")
    envelope = source.get("envelope")
    if not isinstance(envelope, dict):
        return refuse("envelope.missing", "source produced no envelope")
    raw = json.dumps(envelope, ensure_ascii=False, sort_keys=True).encode("utf-8")
    existing = await fs.read(uri)
    messages_sha = str(source.get("messages_sha256") or "")
    if existing is None:
        written = await fs.write(uri, raw, if_absent=True)
        if not written.get("ok"):
            return refuse("envelope.conflict", "if_absent lost the race")
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
    old_meta = old.get("meta") if isinstance(old.get("meta"), dict) else {}
    old_sha = str(old_meta.get("messages_sha256") or "")
    if old_sha == messages_sha and not _same_pour_identity(old_meta, source):
        return refuse(
            "envelope.provenance_mismatch",
            "snapshot messages match but provenance, producer, or capture differ",
        )
    if old_sha == messages_sha and _same_pour_identity(old_meta, source):
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
    written = await fs.write(uri, raw, expected_sha256=file_sha256(existing))
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
