"""Break cases for rag-search step controls and pipeline home."""

import pytest

from systems.pipeline.core.conditions import (
    KNOWN_CONDITION_NAMES,
    extract_condition_deps,
)
from systems.pipeline.core.schemas import PipelineSpec, StepConfig
from systems.pipeline.core.step_controls import (
    StepCallerError,
    apply_request_step_controls,
    fold_legacy_enable_flags,
)


def _pipeline() -> PipelineSpec:
    steps = [
        StepConfig.model_validate(
            {
                "name": "generate_hyde",
                "type": "generate",
                "enabled": False,
                "allow_disable": True,
            }
        ),
        StepConfig.model_validate(
            {
                "name": "rerank",
                "type": "rerank_v1",
                "enabled": True,
                "allow_disable": True,
            }
        ),
        StepConfig.model_validate(
            {
                "name": "retrieval_metadata",
                "type": "retrieval_metadata_step",
                "enabled": True,
                "allow_disable": False,
            }
        ),
    ]
    return PipelineSpec.model_validate(
        {
            "id": "rag-search",
            "version": "1.0",
            "type": "rag_search",
            "category": "rag_search",
            "output": "retrieval_metadata",
            "steps": steps,
        }
    )


def test_enable_and_skip_same_step_is_caller_error() -> None:
    with pytest.raises(StepCallerError, match="skip_steps"):
        apply_request_step_controls(
            _pipeline(),
            _pipeline().steps,
            {
                "step_overrides": {"rerank": {"enabled": True}},
                "skip_steps": ["rerank"],
            },
        )


def test_unknown_step_is_caller_error() -> None:
    with pytest.raises(StepCallerError, match="unknown step"):
        apply_request_step_controls(
            _pipeline(),
            _pipeline().steps,
            {"step_overrides": {"missing": {"enabled": False}}},
        )


def test_disable_allow_disable_false_is_caller_error() -> None:
    with pytest.raises(StepCallerError, match="allow_disable"):
        apply_request_step_controls(
            _pipeline(),
            _pipeline().steps,
            {"skip_steps": ["retrieval_metadata"]},
        )


def test_named_condition_is_not_a_step_dep() -> None:
    assert "chunks_present" in KNOWN_CONDITION_NAMES
    assert extract_condition_deps("chunks_present") == set()


def test_legacy_flags_fold_into_step_overrides() -> None:
    folded = fold_legacy_enable_flags({"hyde_enabled": True, "rerank_enabled": False})
    assert "hyde_enabled" not in folded
    assert folded["step_overrides"]["generate_hyde"]["enabled"] is True
    assert folded["step_overrides"]["rerank"]["enabled"] is False
