"""Two-call semantic judge for pin coverage and correspondence quality."""

from __future__ import annotations

import json
from typing import Any

from model_id import ModelId
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput
from systems.pipeline.core.prompts import PromptBuilder
from writing_pipeline.events import WritingJudgeCompleted, WritingJudgeFailed

from .judge_schema import (
    PIN_CANDIDATES_SCHEMA,
    PIN_SINGLE_SCHEMA,
    QUALITY_JUDGMENT_SCHEMA,
    flatten_pin_judgment,
    flatten_quality_judgment,
    pin_judgment_error,
    quality_judgment_error,
)
from .seat_dispatch import _prompts
from .seat_wait import sanitize_plain_text_draft

_JSON_ONLY = "Return only the JSON object, with no prose before or after it."
_DRAFTER_STEPS = (
    "drafter_openai",
    "drafter_google",
    "drafter_xai",
    "drafter_local_hermes",
    "drafter_local_ernie",
    "drafter_anthropic",
)


def _step(payload: dict[str, Any], *, model_id: str | None = None) -> StepOutput:
    return StepOutput(raw=json.dumps(payload), json=payload, model_id=model_id)


def _unavailable(invocation: str, reason: str) -> StepOutput:
    return _step(
        {
            "ok": False,
            "judge_unavailable": True,
            "refused": "judge_unavailable",
            "reason": reason,
            "invocation": invocation,
            "pins": [],
            "clauses": [],
            "quality_findings": [],
        }
    )


def _raw_text(step: Any) -> str:
    if step is None:
        return ""
    raw = step.get("raw") if isinstance(step, dict) else getattr(step, "raw", None)
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    if not isinstance(raw, str):
        return ""
    text = raw.strip()
    if text.startswith("{") and '"draft"' in text:
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict) and isinstance(parsed.get("draft"), str):
                return parsed["draft"]
        except json.JSONDecodeError:
            pass
    return text


def _intake_directives(assemble: dict[str, Any]) -> str:
    intake = assemble.get("intake")
    if not isinstance(intake, dict):
        intake = {}
    parts = [
        str(assemble.get("objective") or intake.get("objective") or ""),
        str(intake.get("standing_limits") or ""),
        str(intake.get("purpose") or ""),
    ]
    notes = intake.get("verbatim_notes") or []
    if isinstance(notes, list):
        parts.extend(str(n) for n in notes)
    return "\n".join(p for p in parts if p.strip())


def _eligible_candidates(outputs: dict[str, Any]) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    for step_name in _DRAFTER_STEPS:
        out = outputs.get(step_name)
        err = out.get("error") if isinstance(out, dict) else getattr(out, "error", None)
        if err:
            continue
        text = sanitize_plain_text_draft(_raw_text(out))
        if not text.strip():
            continue
        found.append({"candidate_id": step_name, "draft": text})
    return found


class WritingSemanticJudgeHandler(BaseHandler):
    """Run pin and quality judgments via the verifier alias only."""

    step_type = "writing_semantic_judge_v1"

    def __init__(self, caller: Any | None = None) -> None:
        super().__init__()
        self._caller = caller

    async def execute(self, step: Any, context: Any) -> StepOutput:
        invocation = str(step.get_domain_field("invocation") or "candidates")
        outputs = getattr(context, "outputs", {}) or {}
        assemble_out = outputs.get("assemble")
        assemble = (
            assemble_out.get("json")
            if isinstance(assemble_out, dict)
            else getattr(assemble_out, "json", None)
        )
        assemble = assemble if isinstance(assemble, dict) else {}
        ledger = list(assemble.get("pin_ledger") or [])
        pins_block = json.dumps(ledger, ensure_ascii=False)
        directives = _intake_directives(assemble)
        prompts = _prompts().get("semantic_judge") or {}
        pin_row = prompts.get("pin") or {}
        quality_row = prompts.get("quality") or {}
        multi = invocation == "candidates"
        if multi:
            candidates = _eligible_candidates(outputs)
            if not candidates:
                return _unavailable(invocation, "no_candidates")
            candidates_block = json.dumps(candidates, ensure_ascii=False)
        else:
            rhythm = outputs.get("prose_rhythm_pass")
            err = (
                rhythm.get("error")
                if isinstance(rhythm, dict)
                else getattr(rhythm, "error", None)
            )
            if err:
                return _unavailable(invocation, "reviser_failed")
            draft = _raw_text(rhythm)
            if not draft.strip():
                return _unavailable(invocation, "empty_draft")
            candidates_block = json.dumps([{"draft": draft}], ensure_ascii=False)

        pin_prompt = PromptBuilder().render(
            str(pin_row.get("template") or ""),
            {
                "pins": pins_block,
                "candidates": candidates_block,
                "intake_directives": directives,
            },
        )
        pin_prompt = pin_prompt.rstrip() + "\n" + _JSON_ONLY
        quality_prompt = PromptBuilder().render(
            str(quality_row.get("template") or ""),
            {
                "candidates": candidates_block,
                "intake_directives": directives,
            },
        )
        quality_prompt = quality_prompt.rstrip() + "\n" + _JSON_ONLY
        pin_system = str(pin_row.get("system_prompt") or "")
        quality_system = str(quality_row.get("system_prompt") or "")
        pin_schema = PIN_CANDIDATES_SCHEMA if multi else PIN_SINGLE_SCHEMA
        ModelId.parse("qwen3-5-9b")
        model_id = "qwen3-5-9b"

        try:
            if self._caller is not None:
                pin_content = await self._caller(
                    which="pin", json_schema=pin_schema
                )
                quality_content = await self._caller(
                    which="quality", json_schema=QUALITY_JUDGMENT_SCHEMA
                )
            else:
                pin_result = await self._call_model(
                    model_id,
                    pin_prompt,
                    step,
                    context,
                    system_prompt=pin_system,
                    temperature=0.1,
                    json_schema=pin_schema,
                )
                quality_result = await self._call_model(
                    model_id,
                    quality_prompt,
                    step,
                    context,
                    system_prompt=quality_system,
                    temperature=0.1,
                    json_schema=QUALITY_JUDGMENT_SCHEMA,
                )
                pin_content = pin_result.content
                quality_content = quality_result.content
        except Exception as exc:  # noqa: BLE001
            self._publish_bus_event(
                context,
                WritingJudgeFailed(invocation=invocation, reason=type(exc).__name__),
            )
            return _unavailable(invocation, type(exc).__name__)

        try:
            pin_parsed = json.loads(pin_content) if isinstance(pin_content, str) else {}
            quality_parsed = (
                json.loads(quality_content) if isinstance(quality_content, str) else {}
            )
        except json.JSONDecodeError:
            self._publish_bus_event(
                context,
                WritingJudgeFailed(invocation=invocation, reason="json_decode"),
            )
            return _unavailable(invocation, "json_decode")

        pin_err = pin_judgment_error(pin_parsed, multi_candidate=multi)
        quality_err = quality_judgment_error(quality_parsed, multi_candidate=multi)
        if pin_err or quality_err:
            self._publish_bus_event(
                context,
                WritingJudgeFailed(
                    invocation=invocation,
                    reason=pin_err or quality_err or "schema_invalid",
                ),
            )
            return _unavailable(invocation, pin_err or quality_err or "schema_invalid")

        flat_pins, flat_clauses = flatten_pin_judgment(pin_parsed, multi_candidate=multi)
        quality = flatten_quality_judgment(quality_parsed, multi_candidate=multi)
        if multi:
            eligible_ids = {row["candidate_id"] for row in _eligible_candidates(outputs)}
            seen_ids = {str(row.get("candidate_id") or "") for row in flat_pins} | {
                str(row.get("candidate_id") or "") for row in flat_clauses
            }
            seen_ids |= {str(row.get("candidate_id") or "") for row in quality}
            seen_ids.discard("")
            if eligible_ids and not seen_ids:
                self._publish_bus_event(
                    context,
                    WritingJudgeFailed(invocation=invocation, reason="all_omitted"),
                )
                return _unavailable(invocation, "all_omitted")

        self._publish_bus_event(
            context,
            WritingJudgeCompleted(invocation=invocation, model_id=model_id),
        )
        return _step(
            {
                "ok": True,
                "judge_unavailable": False,
                "invocation": invocation,
                "pins": flat_pins,
                "clauses": flat_clauses,
                "quality_findings": quality,
                "model_id": model_id,
            },
            model_id=model_id,
        )
