"""Tests for PipelineSpec.is_stream_passthrough_eligible.

The predicate gates the terminal-passthrough streaming path. These tests pin
its strict-by-default contract: True only when the pipeline is structurally a
single generate step whose output IS the response, with no aggregation
options enabled.
"""

from __future__ import annotations

import sys
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parents[5])
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from systems.pipeline.core.pipeline_config import (  # noqa: E402
    PipelineOptions,
    PipelineSpec,
)
from systems.pipeline.core.step_config import StepConfig  # noqa: E402


def _step(
    *,
    name: str = "reply",
    type_: str = "generate",
    model_ref: str | None = "default",
    map_config: dict | None = None,
) -> StepConfig:
    """Build a StepConfig with defaults appropriate for a generate step."""
    data: dict = {"id": name, "type": type_}
    if model_ref is not None:
        data["model_ref"] = model_ref
    if map_config is not None:
        data["map_config"] = map_config
    return StepConfig.model_validate(data)


def _pipeline(
    *,
    steps: list[StepConfig] | None = None,
    output: str = "reply",
    options: PipelineOptions | None = None,
) -> PipelineSpec:
    """Build a PipelineSpec with sensible defaults for these tests."""
    if steps is None:
        steps = [_step()]
    if options is None:
        options = PipelineOptions()
    return PipelineSpec(
        id="test-pipeline",
        version="1",
        type="test",
        options=options,
        steps=steps,
        output=output,
    )


def test_eligible_when_single_generate_step_with_matching_output() -> None:
    pipeline = _pipeline()
    assert pipeline.is_stream_passthrough_eligible is True


def test_ineligible_when_zero_steps() -> None:
    # Defensive: malformed pipeline must not accidentally stream.
    # Zero-step pipelines are rejected upstream by other validation; the
    # predicate must still return False on that branch independently.
    pipeline = PipelineSpec(
        id="x", version="1", type="test", steps=[], output="reply"
    )
    assert pipeline.is_stream_passthrough_eligible is False


def test_ineligible_when_multiple_steps() -> None:
    pipeline = _pipeline(
        steps=[_step(name="prep", type_="rag_search"), _step(name="reply")],
        output="reply",
    )
    assert pipeline.is_stream_passthrough_eligible is False


def test_ineligible_when_step_type_is_not_generate() -> None:
    pipeline = _pipeline(
        steps=[_step(name="reply", type_="frontier_dispatch", model_ref=None)],
        output="reply",
    )
    assert pipeline.is_stream_passthrough_eligible is False


def test_ineligible_when_map_step() -> None:
    pipeline = _pipeline(
        steps=[
            _step(
                name="reply",
                map_config={"map_over": {"model": "${step.candidates.text}"}},
            )
        ]
    )
    assert pipeline.is_stream_passthrough_eligible is False


def test_ineligible_when_output_does_not_reference_terminal_step() -> None:
    pipeline = _pipeline(
        steps=[_step(name="reply")],
        output="something_else",
    )
    assert pipeline.is_stream_passthrough_eligible is False


def test_ineligible_when_include_alternates() -> None:
    pipeline = _pipeline(options=PipelineOptions(include_alternates=True))
    assert pipeline.is_stream_passthrough_eligible is False


def test_ineligible_when_include_step_stats() -> None:
    pipeline = _pipeline(options=PipelineOptions(include_step_stats=True))
    assert pipeline.is_stream_passthrough_eligible is False


def test_predicate_is_pure_no_state_mutation() -> None:
    pipeline = _pipeline()
    snapshot_before = pipeline.model_dump()
    _ = pipeline.is_stream_passthrough_eligible
    _ = pipeline.is_stream_passthrough_eligible
    snapshot_after = pipeline.model_dump()
    assert snapshot_before == snapshot_after
