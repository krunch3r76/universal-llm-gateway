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

from .independence import load_writing_models
from .review_schema import REVIEW_RESULT_SCHEMA
from .seat_dispatch import _prompts

_JSON_ONLY = "Return only the JSON object, with no prose before or after it."


def _step(payload: dict[str, Any]) -> StepOutput:
    return StepOutput(raw=json.dumps(payload), json=payload)


def _refusal(exc: BaseException) -> StepOutput:
    return _step(
        {
            "ok": False,
            "refused": "review_transport_failed",
            "error": type(exc).__name__,
        }
    )


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
        try:
            if self._caller is not None:
                content = await self._caller(json_schema=REVIEW_RESULT_SCHEMA)
            else:
                models = load_writing_models()
                result = await self._call_model(
                    models.get("reviewer") or "",
                    prompt,
                    step,
                    context,
                    system_prompt=system,
                    temperature=0.1,
                    max_tokens=2048,
                    json_schema=REVIEW_RESULT_SCHEMA,
                )
                content = result.content
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
        return _step(parsed)
