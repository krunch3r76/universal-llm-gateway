"""Read a poured envelope and validate the pour contract."""

from __future__ import annotations

import json
from typing import Any, override

from continuity_tape.pour_contract import PourRefused, validate_pour
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput

from ._clients import file_sha256, fs_of, refuse, rel_path_ok


async def run_source(options: dict[str, Any], fs: Any) -> dict[str, Any]:
    ref = str(options.get("envelope_ref") or "")
    expected = str(options.get("envelope_sha256") or "")
    if not rel_path_ok(ref):
        return refuse("admit.path_escape", f"refused path {ref}")
    raw = fs.read(ref)
    if raw is None:
        return refuse("source.missing", f"envelope {ref} absent")
    digest = file_sha256(raw)
    if digest != expected:
        return refuse("source.sha_mismatch", f"{digest} != {expected}")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        return refuse("pour.schema", str(exc))
    if not isinstance(payload, dict):
        return refuse("pour.schema", "envelope is not an object")
    try:
        envelope = validate_pour(payload)
    except PourRefused as exc:
        return refuse(exc.code, exc.message)
    meta = envelope.meta
    producer = (meta.sources or [{}])[0].get("producer") or {}
    return {
        "refused": None,
        "envelope": payload,
        "provenance": meta.provenance,
        "producer": producer.get("id"),
        "coverage": meta.coverage,
        "messages_sha256": meta.messages_sha256,
        "surface": meta.surface,
        "turn_count": meta.turn_count,
        "sha256": digest,
    }


class GrokbotCheckpointSourceHandler(BaseHandler):
    step_type = "grokbot_checkpoint_source_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        options = dict(getattr(context, "options", {}) or {})
        payload = await run_source(options, fs_of(context))
        return StepOutput(raw="", json=payload)
