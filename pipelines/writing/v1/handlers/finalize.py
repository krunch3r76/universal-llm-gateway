"""Build the UNSENT writer-specialist envelope from prior step outputs.

``WritingFinalizeHandler`` always runs. A refused assemble, a packet-only
run, or a failed independence check returns a short envelope. Otherwise the
envelope carries the draft, both provenance passes, the review, an optional
revision, and a ship gate. Nothing here sends or writes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput

from .independence import load_writing_models

_GENERATE_STEPS = (
    ("draft", "writer"),
    ("review", "reviewer"),
    ("revise", "reviser"),
)


def _step(payload: dict[str, Any]) -> StepOutput:
    return StepOutput(raw=json.dumps(payload), json=payload)


def _raw_step(outputs: dict[str, Any], name: str) -> Any:
    return outputs.get(name)


def _step_ok(step: Any) -> bool:
    if step is None:
        return False
    if isinstance(step, dict):
        return not step.get("error")
    return not getattr(step, "error", None)


def _json_of(outputs: dict[str, Any], name: str) -> dict[str, Any] | None:
    step = _raw_step(outputs, name)
    if not _step_ok(step):
        return None
    data = step.get("json") if isinstance(step, dict) else getattr(step, "json", None)
    if isinstance(data, dict) and data.get("_skipped") is True:
        return None
    return data if isinstance(data, dict) else None


def _usage_of(step: Any) -> dict[str, int] | None:
    if not _step_ok(step):
        return None
    prompt = (
        getattr(step, "prompt_tokens", None)
        if not isinstance(step, dict)
        else step.get("prompt_tokens")
    )
    completion = (
        getattr(step, "completion_tokens", None)
        if not isinstance(step, dict)
        else step.get("completion_tokens")
    )
    calls = (
        getattr(step, "model_call_count", 0)
        if not isinstance(step, dict)
        else step.get("model_call_count", 0)
    ) or 0
    if prompt is None and completion is None and calls == 0:
        return None
    return {
        "prompt_tokens": int(prompt or 0),
        "completion_tokens": int(completion or 0),
    }


def _model_of(step: Any) -> str | None:
    if step is None:
        return None
    if isinstance(step, dict):
        model = step.get("model_id")
    else:
        model = getattr(step, "model_id", None)
    return str(model) if model else None


def _rates() -> dict[str, tuple[float, float]]:
    path = Path(__file__).resolve().parents[4] / "config" / "model_rates.yaml"
    from event_store.model_rate_table import load_manual_rows

    rows, _aliases = load_manual_rows(path)
    return {
        model_id: (row.input_rate_per_m, row.output_rate_per_m)
        for model_id, row in rows.items()
    }


def _cost(
    outputs: dict[str, Any],
    usage: dict[str, dict[str, int] | None],
    models: dict[str, str],
) -> tuple[float | None, str | None]:
    table = _rates()
    total = 0.0
    for step_name, role in _GENERATE_STEPS:
        spent = usage.get(step_name)
        if not spent:
            continue
        model = _model_of(_raw_step(outputs, step_name)) or models.get(role) or ""
        rate = table.get(model)
        if rate is None:
            return None, f"rate_missing:{model}"
        prompt_rate, completion_rate = rate
        total += (spent["prompt_tokens"] * prompt_rate) / 1_000_000
        total += (spent["completion_tokens"] * completion_rate) / 1_000_000
    return total, None


class WritingFinalizeHandler(BaseHandler):
    """Fold step outputs into one UNSENT envelope with a ship gate.

    Missing steps and steps whose ``error`` is set count as not ok. Token
    cost uses ``config/model_rates.yaml`` via the manual rate loader. A model
    with no row nulls ``est_cost_usd`` and sets ``cost_note``.
    """

    step_type = "writing_finalize_v1"

    async def execute(self, step: Any, context: Any) -> StepOutput:
        """Read ``context.outputs`` and return the envelope for this run.

        Assemble refusals and packet runs return before review fields are
        required. Every payload sets ``unsent`` true. The handler does not
        send, post, or write a draft.
        """
        del step
        outputs = getattr(context, "outputs", {}) or {}
        assemble = _json_of(outputs, "assemble")
        if assemble is None:
            return _step(
                {
                    "unsent": True,
                    "refused": "assemble_missing",
                    "error": "assemble step not ok",
                }
            )
        if assemble.get("refused"):
            return _step(
                {
                    "unsent": True,
                    "refused": assemble.get("refused"),
                    "error": assemble.get("error"),
                }
            )
        if assemble.get("output") == "packet":
            return _step(
                {
                    "unsent": True,
                    "output": "packet",
                    "packet": assemble.get("packet"),
                    "pin_ledger": assemble.get("pin_ledger"),
                }
            )
        models = load_writing_models()
        draft = _json_of(outputs, "draft")
        initial_payload = _json_of(outputs, "provenance_check")
        initial = list((initial_payload or {}).get("violations") or [])
        independence = _json_of(outputs, "independence")
        if independence is None or independence.get("refused"):
            return _step(
                {
                    "unsent": True,
                    "refused": "independence_violation",
                    "draft_v1": draft,
                    "provenance": {"initial": initial},
                }
            )
        review = _json_of(outputs, "review")
        review_ok = bool(review) and review.get("verdict") in {"ship", "revise"}
        revise = _json_of(outputs, "revise")
        revision_ran = revise is not None
        final_payload = _json_of(outputs, "provenance_check_final")
        final = (
            list(final_payload.get("violations") or [])
            if revision_ran and final_payload is not None
            else None
        )
        if review_ok and revision_ran:
            passed = final == []
        elif review_ok and initial_payload is not None:
            passed = initial == [] and review.get("verdict") == "ship"
        else:
            passed = False
        active = final if final is not None else initial
        unresolved = list(active)
        if not revision_ran and review_ok and review.get("verdict") == "revise":
            unresolved.extend(list(review.get("findings") or []))
        revision = None
        if revision_ran and revise is not None:
            revision = {
                "draft_v2": revise.get("draft"),
                "changed_claims": list(revise.get("changed_claims") or []),
                "dispositions": list(revise.get("dispositions") or []),
            }
        usage = {
            name: _usage_of(_raw_step(outputs, name)) for name, _role in _GENERATE_STEPS
        }
        est_cost, cost_note = _cost(outputs, usage, models)
        payload: dict[str, Any] = {
            "draft_v1": draft,
            "provenance": {
                "pin_ledger": assemble.get("pin_ledger"),
                "initial": initial,
                "final": final,
            },
            "review": {
                "status": "ok" if review_ok else "failed",
                "writer_seat": assemble.get("writer_seat"),
                "reviewer_seat": assemble.get("reviewer_seat"),
                "independence": independence.get("independence"),
                "findings": review.get("findings") if review_ok else None,
                "verdict": review.get("verdict") if review_ok else None,
            },
            "revision": revision,
            "ship_gate": {"pass": passed, "unresolved": unresolved},
            "seats_used": {
                "writer": models.get("writer"),
                "reviewer": models.get("reviewer"),
                "reviser": models.get("reviser") if revision_ran else None,
            },
            "usage": usage,
            "est_cost_usd": est_cost,
            "unsent": True,
        }
        if cost_note:
            payload["cost_note"] = cost_note
        return _step(payload)
