"""Participation manifest for writer-specialist drafter steps."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import yaml
from systems.pipeline.core.conditions import ConditionEvaluator
from systems.pipeline.core.handlers.step_output import StepOutput
from systems.pipeline.core.pipeline_config import PipelineSpec

from .finalize_support import _raw_step, _step_ok
from .independence import load_writing_models

_DRAFTER_ROWS: tuple[tuple[str, str], ...] = (
    ("drafter_openai", "drafter_openai"),
    ("drafter_google", "drafter_google"),
    ("drafter_xai", "drafter_xai"),
    ("drafter_local_hermes", "drafter_local_hermes"),
    ("drafter_local_ernie", "drafter_local_ernie"),
    ("drafter_anthropic", "drafter_anthropic"),
)

_PIPELINE_YAML = (
    Path(__file__).resolve().parents[1] / "writer-specialist-v1.yaml"
)
_CONDITIONS: dict[str, str] | None = None


def _load_conditions() -> dict[str, str]:
    global _CONDITIONS
    if _CONDITIONS is not None:
        return _CONDITIONS
    raw = yaml.safe_load(_PIPELINE_YAML.read_text(encoding="utf-8")) or {}
    by_name = {step.name: step for step in PipelineSpec(**raw).steps}
    _CONDITIONS = {
        name: str(by_name[name].condition or "")
        for name, _alias in _DRAFTER_ROWS
        if name in by_name
    }
    return _CONDITIONS


def persona_refused_reason(persona_step: dict[str, Any] | None) -> str | None:
    """Top-level persona_refused string for the finalize envelope."""
    if not isinstance(persona_step, dict) or persona_step.get("ok") is not False:
        return None
    return str(
        persona_step.get("persona_refused")
        or persona_step.get("refused")
        or "persona_failed"
    )


def _json_of(step: Any) -> dict[str, Any] | None:
    if step is None:
        return None
    if isinstance(step, dict):
        return step.get("json") if isinstance(step.get("json"), dict) else step
    payload = getattr(step, "json", None)
    return payload if isinstance(payload, dict) else None


def _raw_text(step: Any) -> str:
    if step is None:
        return ""
    if isinstance(step, dict):
        raw = step.get("raw")
    else:
        raw = getattr(step, "raw", None)
    return str(raw or "").strip()


def _tokens(step: Any) -> tuple[int | None, int | None]:
    if step is None:
        return None, None
    if isinstance(step, dict):
        prompt = step.get("prompt_tokens")
        completion = step.get("completion_tokens")
    else:
        prompt = getattr(step, "prompt_tokens", None)
        completion = getattr(step, "completion_tokens", None)
    return (
        int(prompt) if prompt is not None else None,
        int(completion) if completion is not None else None,
    )


def _candidate_sha256(step: Any) -> str | None:
    text = _raw_text(step)
    if not text:
        body = _json_of(step)
        if isinstance(body, dict):
            for key in ("draft", "content", "text"):
                val = body.get(key)
                if isinstance(val, str) and val.strip():
                    text = val.strip()
                    break
    if not text:
        return None
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _outputs_for_conditions(
    outputs: dict[str, Any],
) -> dict[str, StepOutput]:
    mapped: dict[str, StepOutput] = {}
    for name, step in outputs.items():
        payload = _json_of(step) or {}
        raw = _raw_text(step)
        mapped[name] = StepOutput(raw=raw, json=payload)
    return mapped


def _persona_dependency(persona_step: dict[str, Any] | None) -> str | None:
    reason = persona_refused_reason(persona_step)
    if not reason:
        return None
    return f"local_persona_author:{reason}"


def _classify_state(
    *,
    name: str,
    condition: str,
    outputs: dict[str, Any],
    options: dict[str, Any],
    persona_step: dict[str, Any] | None,
    evaluator: ConditionEvaluator,
) -> tuple[str, str | None]:
    ctx = _outputs_for_conditions(outputs)
    opts = dict(options or {})
    if not condition:
        return "failed", None
    active = evaluator.evaluate(condition, ctx, opts)
    if active:
        raw = _raw_step(outputs, name)
        if raw is None:
            return "failed", None
        if not _step_ok(raw):
            return "failed", None
        text = _raw_text(raw)
        if not text:
            return "failed", None
        _, completion = _tokens(raw)
        if completion is not None and completion > 0:
            return "executed", None
        return "executed_unmetered", None
    persona_ok = dict(persona_step or {})
    persona_ok["ok"] = True
    patched = dict(ctx)
    patched["local_persona_author"] = StepOutput(
        raw="",
        json=persona_ok,
    )
    if evaluator.evaluate(condition, patched, opts):
        dep = _persona_dependency(persona_step)
        return "skipped_dependency", dep
    return "skipped_policy", None


def build_participation_manifest(
    outputs: dict[str, Any],
    *,
    options: dict[str, Any] | None,
    models: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """One row per YAML drafter in fixed order."""
    table = models if models is not None else load_writing_models()
    persona_json = _json_of(_raw_step(outputs, "local_persona_author"))
    conditions = _load_conditions()
    evaluator = ConditionEvaluator()
    opts = dict(options or {})
    rows: list[dict[str, Any]] = []
    for step_name, alias in _DRAFTER_ROWS:
        raw = _raw_step(outputs, step_name)
        prompt_tokens, completion_tokens = _tokens(raw)
        state, dependency = _classify_state(
            name=step_name,
            condition=conditions.get(step_name, ""),
            outputs=outputs,
            options=opts,
            persona_step=persona_json,
            evaluator=evaluator,
        )
        resolved = None
        if raw is not None:
            if isinstance(raw, dict):
                mid = raw.get("model_id")
            else:
                mid = getattr(raw, "model_id", None)
            resolved = str(mid) if mid else None
        rows.append(
            {
                "step": step_name,
                "alias": alias,
                "requested_model": table.get(alias),
                "resolved_model": resolved,
                "state": state,
                "dependency": dependency,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "candidate_sha256": _candidate_sha256(raw),
            }
        )
    return rows


def build_participation_summary(rows: list[dict[str, Any]]) -> str:
    """Human-readable participation rollup for finalize."""
    executed = [r for r in rows if r.get("state") == "executed"]
    unmetered = [r for r in rows if r.get("state") == "executed_unmetered"]
    run_count = len(executed) + len(unmetered)
    parts = [f"{run_count}/6 executed"]
    if unmetered:
        names = ",".join(str(r.get("step") or "") for r in unmetered)
        parts.append(f"unmetered:{names}")
    skipped = [
        r
        for r in rows
        if str(r.get("state") or "").startswith("skipped_")
    ]
    for row in skipped:
        step = str(row.get("step") or "")
        state = str(row.get("state") or "")
        dep = row.get("dependency")
        if dep:
            parts.append(f"{step} {state}({dep})")
        else:
            parts.append(f"{step} {state}")
    failed = [r for r in rows if r.get("state") == "failed"]
    if failed:
        parts.append("failed:" + ",".join(str(r.get("step") or "") for r in failed))
    return "; ".join(parts)
