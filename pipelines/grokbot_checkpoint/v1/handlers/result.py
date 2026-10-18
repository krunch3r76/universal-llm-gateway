"""Aggregate step outputs into the pipeline result object."""

from __future__ import annotations

import json
from typing import Any, override

from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput

from ._clients import step_output_json
from ._harvest import format_harvest_record


def assemble(options: dict[str, Any], outputs: dict[str, Any]) -> dict[str, Any]:
    steps = (
        "admit",
        "harvest_normalise",
        "harvest_adapter",
        "harvest_pour",
        "source",
        "envelope",
        "seal",
        "card",
        "post",
        "fact",
        "await_verify",
        "verify_resume",
    )
    completed: list[str] = []
    refused = None
    for name in steps:
        payload = step_output_json(outputs, name)
        if not payload or payload.get("_skipped"):
            continue
        completed.append(name)
        if payload.get("refused"):
            refused = payload["refused"]
            break
    admit = step_output_json(outputs, "admit")
    source = step_output_json(outputs, "source")
    if (not source or source.get("_skipped")) and options.get("mode") == "harvest_seal":
        source = step_output_json(outputs, "harvest_pour")
    harvest_normalise = step_output_json(outputs, "harvest_normalise")
    harvest_adapter = step_output_json(outputs, "harvest_adapter")
    envelope = step_output_json(outputs, "envelope")
    seal = step_output_json(outputs, "seal")
    card = step_output_json(outputs, "card")
    post = step_output_json(outputs, "post")
    fact = step_output_json(outputs, "fact")
    verify = step_output_json(outputs, "await_verify")
    verify_resume_step = step_output_json(outputs, "verify_resume")
    verify_resume = (
        verify_resume_step
        if verify_resume_step and not verify_resume_step.get("_skipped")
        else None
    )
    warnings = list(card.get("warnings") or [])
    notices = list(card.get("notices") or [])
    harvest_record = None
    if options.get("mode") == "harvest_seal" and not refused:
        if harvest_normalise.get("l0_ref") and source.get("ref"):
            cap = source.get("capture")
            capture_sha256 = ""
            if isinstance(cap, dict):
                capture_sha256 = str(cap.get("sha256") or "")
            if not capture_sha256:
                capture_sha256 = str(harvest_normalise.get("normalized_sha256") or "")
            verify_ok = None
            if verify_resume and not verify_resume.get("_skipped"):
                verify_ok = verify_resume.get("refused") is None
            harvest_record = format_harvest_record(
                l0_ref=str(harvest_normalise.get("l0_ref")),
                l0_sha256=str(harvest_normalise.get("l0_sha256")),
                pages=int(harvest_normalise.get("pages") or 0),
                l0_bytes=int(harvest_normalise.get("l0_bytes") or 0),
                truncated_parts=int(source.get("truncated_parts") or 0),
                l1_ref=str(source.get("ref")),
                l1_sha256=str(source.get("sha256")),
                messages_sha256=str(source.get("messages_sha256")),
                source_sha256=str(source.get("source_sha256")),
                adapter_read_sha256=str(
                    source.get("adapter_read_sha256")
                    or harvest_adapter.get("stats", {}).get("adapter_read_sha256")
                    or ""
                ),
                capture_sha256=capture_sha256,
                session_id=str(seal.get("session_id") or "") or None,
                cp_turn=post.get("turn"),
                fact_turn=fact.get("turn"),
                verify_resume_ok=verify_ok,
            )
    return {
        "house": options.get("house"),
        "n": options.get("n"),
        "mode": options.get("mode"),
        "replayed": bool(admit.get("replayed") or card.get("replayed")),
        "completed_steps": completed,
        "refused": refused,
        "envelope": {
            "snapshot_uri": envelope.get("snapshot_uri"),
            "sha256": envelope.get("sha256"),
            "messages_sha256": envelope.get("messages_sha256")
            or source.get("messages_sha256"),
            "provenance": envelope.get("provenance") or source.get("provenance"),
            "producer": envelope.get("producer") or source.get("producer"),
            "coverage": envelope.get("coverage") or source.get("coverage"),
            "turn_count": envelope.get("turn_count") or source.get("turn_count"),
            "ref": source.get("ref")
            or options.get("transcribed_ref")
            or options.get("raw_ref"),
            "capture": source.get("capture")
            if isinstance(source.get("capture"), dict)
            else None,
            "alternate": source.get("alternate"),
            "fallback": source.get("fallback"),
            "handoff_binding": envelope.get("handoff_binding"),
            "handoff_binding_source": envelope.get("handoff_binding_source"),
            "handoff.normalized": envelope.get("handoff.normalized")
            or admit.get("handoff.normalized")
            or [],
        },
        "seal": {
            "session_id": seal.get("session_id"),
            "already_closed": seal.get("already_closed"),
        },
        "card": {
            "uri": card.get("uri") or options.get("card_uri"),
            "prior_sha256": card.get("prior_sha256"),
            "written_sha256": card.get("written_sha256"),
        },
        "cp_turn": post.get("turn"),
        "fact_turn": fact.get("turn"),
        "verify": {"state": verify.get("state"), "turn": verify.get("turn")},
        "verify_resume": verify_resume,
        "proposals": seal.get("proposals") or [],
        "warnings": warnings,
        "notices": notices,
        "harvest_record": harvest_record,
    }


class GrokbotCheckpointResultHandler(BaseHandler):
    step_type = "grokbot_checkpoint_result_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        options = dict(getattr(context, "options", {}) or {})
        payload = assemble(options, getattr(context, "outputs", {}) or {})
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        return StepOutput(raw=raw, json=payload)
