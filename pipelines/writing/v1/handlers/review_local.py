"""Local reviewer call that cannot fail the pipeline run.

The engine cancels a run when any step raises. This handler performs the
local reviewer model call and returns ``review_transport_failed`` instead of
raising, so finalize can keep draft_v1.

Review transport or model failure after draft -> envelope with draft_v1,
provenance, review.status=failed, a review_unresolved row in
ship_gate.unresolved, no revision, ship_gate.pass=false; the run completes.
"""

from __future__ import annotations

import json
from typing import Any

from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput
from systems.pipeline.core.prompts import PromptBuilder
from writing_intake.fact_inventory import intake_answer_in_text

from .independence import (
    is_independent,
    load_review_fallback_models,
    load_writing_models,
)
from .review_schema import REVIEW_RESULT_SCHEMA
from .seat_dispatch import _prompts

_JSON_ONLY = "Return only the JSON object, with no prose before or after it."


def _step(payload: dict[str, Any]) -> StepOutput:
    return StepOutput(raw=json.dumps(payload), json=payload)


def _intake_answers_from_assemble(assemble: dict[str, Any]) -> list[str]:
    intake_block = assemble.get("intake") or {}
    if not isinstance(intake_block, dict):
        return []
    raw_answers = intake_block.get("intake_answers")
    if isinstance(raw_answers, list):
        return [str(item) for item in raw_answers]
    return []


def _guard_intake_answer_promoted(
    parsed: dict[str, Any], *, draft_text: str, intake_answers: list[str]
) -> dict[str, Any]:
    findings_in = parsed.get("findings") or []
    kept: list[Any] = []
    suppressed: list[dict[str, str]] = []
    for row in findings_in:
        if not isinstance(row, dict):
            kept.append(row)
            continue
        if row.get("type") != "intake_answer_promoted":
            kept.append(row)
            continue
        ref = row.get("ref")
        if isinstance(ref, str) and ref.strip() and ref.strip() in draft_text:
            haystack = ref.strip()
        else:
            haystack = draft_text
        if intake_answer_in_text(haystack, intake_answers):
            kept.append(row)
            continue
        suppressed.append(
            {
                "ref": str(row.get("ref") or ""),
                "type": str(row.get("type") or ""),
                "fix": str(row.get("fix") or ""),
                "reason": "intake_answer_uncorroborated",
            }
        )
    verdict = parsed.get("verdict")
    if verdict == "revise" and suppressed and not kept:
        verdict = "ship"
    out = dict(parsed)
    out["findings"] = kept
    out["verdict"] = verdict
    out["suppressed_findings"] = suppressed
    return out


def _refusal(exc: BaseException) -> StepOutput:
    return _step(
        {
            "ok": False,
            "refused": "review_transport_failed",
            "error": type(exc).__name__,
        }
    )


def _independent_review_candidates(
    independence_json: dict[str, Any] | None,
    draft: dict[str, Any] | None,
    models: dict[str, Any],
) -> tuple[str, str, list[str]]:
    """Resolve writer model, primary reviewer, and cross-family fallback candidates."""
    writer_model = ""
    if isinstance(independence_json, dict) and independence_json.get("writer_model"):
        writer_model = str(independence_json["writer_model"])
    elif isinstance(draft, dict):
        writer_model = str(draft.get("model_id") or draft.get("model") or "")
    primary = ""
    if isinstance(independence_json, dict) and independence_json.get("reviewer_model"):
        primary = str(independence_json["reviewer_model"])
    if not primary:
        primary = models.get("reviewer") or models.get("verifier") or ""
    raw_candidates = [primary, *load_review_fallback_models()]
    candidates = [
        c
        for c in raw_candidates
        if c and (not writer_model or is_independent(writer_model, c))
    ]
    return writer_model, primary, candidates


class WritingReviewLocalHandler(BaseHandler):
    """Call the local reviewer and convert transport or model errors to JSON.

    ``caller`` is a test double. Production uses ``_call_model``. Neither
    path sets ``StepOutput.error``. The step output never includes the prompt.
    """

    step_type = "writing_review_local_v1"

    def __init__(self, caller: Any | None = None) -> None:
        super().__init__()
        self._caller = caller

    async def execute(self, step: Any, context: Any) -> StepOutput:
        """Render the reviewer prompt, call the model, return findings JSON.

        ``httpx.ConnectError`` and any other exception become
        ``review_transport_failed``. Parse failures return JSON missing
        ``findings`` so the select step refuses ``local_result_invalid``.
        """
        outputs = getattr(context, "outputs", {}) or {}
        assemble = getattr(outputs.get("assemble"), "json", None)
        draft = getattr(outputs.get("draft"), "json", None)
        if not isinstance(assemble, dict):
            assemble = {}
        draft_text = ""
        if isinstance(draft, dict) and isinstance(draft.get("draft"), str):
            draft_text = draft["draft"]
        row = _prompts().get("reviewer") or {}
        fields = {
            "documents": assemble.get("documents_block") or "",
            "brief": assemble.get("brief_block") or "",
            "draft": draft_text,
        }
        prompt = PromptBuilder().render(str(row.get("template") or ""), fields)
        prompt = prompt.rstrip() + "\n" + _JSON_ONLY
        system = str(row.get("system_prompt") or "")
        independence = outputs.get("independence")
        independence_json = (
            independence.get("json")
            if isinstance(independence, dict)
            else getattr(independence, "json", None)
        )
        draft_dict = draft if isinstance(draft, dict) else None
        used_model = ""
        try:
            models = load_writing_models()
            _writer, primary, candidates = _independent_review_candidates(
                independence_json if isinstance(independence_json, dict) else None,
                draft_dict,
                models,
            )
            if self._caller is not None:
                if primary and primary in candidates:
                    used_model = primary
                elif candidates:
                    used_model = candidates[0]
                content = await self._caller(json_schema=REVIEW_RESULT_SCHEMA)
            else:
                seen: set[str] = set()
                content = None
                last_exc: BaseException | None = None
                for model_id in candidates:
                    if not model_id or model_id in seen:
                        continue
                    seen.add(model_id)
                    try:
                        result = await self._call_model(
                            model_id,
                            prompt,
                            step,
                            context,
                            system_prompt=system,
                            temperature=0.1,
                            json_schema=REVIEW_RESULT_SCHEMA,
                        )
                        content = result.content
                        used_model = model_id
                        break
                    except Exception as exc:  # noqa: BLE001 — try fallback chain
                        last_exc = exc
                if content is None and last_exc is not None:
                    raise last_exc
        except Exception as exc:  # noqa: BLE001 — must not escape to the executor
            return _refusal(exc)
        if not isinstance(content, str):
            return _step({"ok": False, "refused": "review_transport_failed"})
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            return _step({"ok": False})
        if not isinstance(parsed, dict):
            return _step({"ok": False})
        if "clauses" not in parsed:
            parsed = dict(parsed)
            parsed["clauses"] = []
        intake_answers = _intake_answers_from_assemble(assemble)
        parsed = _guard_intake_answer_promoted(
            parsed, draft_text=draft_text, intake_answers=intake_answers
        )
        if used_model:
            parsed = dict(parsed)
            parsed["reviewer_model"] = used_model
        return _step(parsed)
