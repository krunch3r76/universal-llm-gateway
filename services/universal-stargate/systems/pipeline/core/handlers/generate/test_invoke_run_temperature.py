"""Per-run ``step_generation_parameters`` temperature override tests.

Covers ``resolve_execution_config_for_model`` run override wiring in
``invoke._resolve_temperature``: absent option preserves YAML/step defaults,
named generate steps override for one run only, and invalid ids/keys/values
fail before any model call.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from systems.pipeline.core.handlers.generate.invoke import resolve_execution_config_for_model
from systems.pipeline.core.pipeline_config import PipelineSpec
from systems.pipeline.core.step_config import StepConfig


class _FakeHandler:
    def _build_prompt_context(self, _step: Any, _context: Any) -> dict[str, Any]:
        return {}

    _prompt_builder = SimpleNamespace(
        render_safe=lambda template, _ctx: template,
    )

    def _resolve_max_tokens(self, _step: Any, _context: Any) -> int | None:
        return 512


def _step(
    step_id: str,
    *,
    temperature: float | None = 0.75,
) -> StepConfig:
    gen: dict[str, Any] = {}
    if temperature is not None:
        gen["temperature"] = temperature
    return StepConfig.model_validate(
        {
            "id": step_id,
            "type": "generate",
            "prompt_ref": "p1",
            "generation_parameters": gen,
        }
    )


def _pipeline(*step_ids: str) -> PipelineSpec:
    steps = [_step(sid) for sid in step_ids]
    steps.append(
        StepConfig.model_validate(
            {"id": "parse_out", "type": "parse_json", "handler_inputs": {}}
        )
    )
    return PipelineSpec.model_validate(
        {
            "id": "test-pipeline",
            "version": "1",
            "type": "test",
            "category": "test",
            "steps": [s.model_dump(by_alias=True) for s in steps],
            "output": "draft",
        }
    )


def _context(
    pipeline: PipelineSpec,
    runtime_options: dict[str, Any] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        pipeline=pipeline,
        runtime_options=runtime_options or {},
    )


def _resolve(
    step: StepConfig,
    context: SimpleNamespace,
) -> float | None:
    cfg = resolve_execution_config_for_model(
        _FakeHandler(),
        step,
        SimpleNamespace(system_prompt=""),
        "openai/gpt-4",
        context,  # type: ignore[arg-type]
    )
    return cfg["temperature"]


def test_step_generation_parameters_absent_unchanged() -> None:
    pipeline = _pipeline("draft", "revise")
    step = pipeline.steps[0]
    ctx = _context(pipeline)
    assert _resolve(step, ctx) == 0.75


def test_named_step_temperature_overridden() -> None:
    pipeline = _pipeline("draft", "revise")
    step = pipeline.steps[0]
    ctx = _context(
        pipeline,
        {"step_generation_parameters": {"draft": {"temperature": 0.2}}},
    )
    assert _resolve(step, ctx) == 0.2


def test_other_generate_step_unchanged_in_same_run() -> None:
    pipeline = _pipeline("draft", "revise")
    revise = pipeline.steps[1]
    ctx = _context(
        pipeline,
        {"step_generation_parameters": {"draft": {"temperature": 0.2}}},
    )
    assert _resolve(revise, ctx) == 0.75


def test_unknown_step_id_raises_with_valid_ids() -> None:
    pipeline = _pipeline("draft", "revise")
    step = pipeline.steps[0]
    ctx = _context(
        pipeline,
        {"step_generation_parameters": {"not_a_step": {"temperature": 0.5}}},
    )
    with pytest.raises(ValueError, match="Unknown step id 'not_a_step'"):
        _resolve(step, ctx)
    with pytest.raises(ValueError, match="Valid generate step ids: \\['draft', 'revise'\\]"):
        _resolve(step, ctx)


def test_non_number_temperature_raises() -> None:
    pipeline = _pipeline("draft")
    step = pipeline.steps[0]
    ctx = _context(
        pipeline,
        {"step_generation_parameters": {"draft": {"temperature": "hot"}}},
    )
    with pytest.raises(ValueError, match="must be a number"):
        _resolve(step, ctx)


def test_unsupported_parameter_key_raises() -> None:
    pipeline = _pipeline("draft")
    step = pipeline.steps[0]
    ctx = _context(
        pipeline,
        {"step_generation_parameters": {"draft": {"top_p": 0.9}}},
    )
    with pytest.raises(ValueError, match="Only 'temperature' is allowed"):
        _resolve(step, ctx)


def test_temperature_out_of_range_raises() -> None:
    pipeline = _pipeline("draft")
    step = pipeline.steps[0]
    ctx = _context(
        pipeline,
        {"step_generation_parameters": {"draft": {"temperature": 2.5}}},
    )
    with pytest.raises(ValueError, match="must be in \\[0, 2\\]"):
        _resolve(step, ctx)
