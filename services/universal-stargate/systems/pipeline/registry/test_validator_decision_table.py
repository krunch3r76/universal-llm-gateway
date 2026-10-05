"""Load-time rejection of decision_table on pipeline steps."""

from __future__ import annotations

from pathlib import Path

import pytest

from systems.pipeline.core.execution.errors.timeout import StepTimeoutError
from systems.pipeline.core.handlers.registry import HandlerRegistry
from systems.pipeline.core.schemas import PipelineSpec, StepConfig
from systems.pipeline.registry.core import PipelineRegistry
from systems.pipeline.registry.validator import PipelineValidator
from systems.pipeline.core.step_controls import (
    StepCallerError,
    StepDefinitionError,
    failure_is_retryable,
)

pytestmark = pytest.mark.offline


@pytest.fixture(autouse=True)
def _handlers() -> None:
    HandlerRegistry._ensure_initialized()


def _support(domain: Path) -> None:
    domain.mkdir(parents=True, exist_ok=True)
    (domain / "models.yaml").write_text(
        "models:\n  ok:\n    model: phi-4-q4-k-m-16384\n", encoding="utf-8"
    )
    (domain / "prompts.yaml").write_text(
        "prompts:\n  dummy:\n    description: fixture\n    template: hello\n",
        encoding="utf-8",
    )


def _pipeline_yaml(*, step_extra: str = "") -> str:
    return (
        "schema_version: 6\n"
        "id: branch-test\n"
        'version: "1.0"\n'
        "type: demo\n"
        "category: demo\n"
        "output: author\n"
        "steps:\n"
        "  - name: author\n"
        "    type: generate\n"
        "    model_ref: ok\n"
        "    prompt_ref: demo.dummy\n"
        f"{step_extra}"
    )


def test_decision_table_empty_mapping_fails_validation_and_names_step(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    _support(root / "demo")
    (root / "categories.yaml").write_text(
        "categories:\n  demo:\n    description: d\n", encoding="utf-8"
    )
    (root / "demo" / "pipe.yaml").write_text(
        _pipeline_yaml(step_extra="    decision_table: {}\n"),
        encoding="utf-8",
    )
    registry = PipelineRegistry(
        search_paths=[str(root)],
        config_base_dir=tmp_path,
    )
    registry.load()
    assert "branch-test" not in registry.pipelines
    joined = " ".join(registry._validation_errors)
    assert "author" in joined
    assert "decision_table is not supported" in joined


def test_decision_table_validator_error_names_step_via_validate_pipeline(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    _support(root / "demo")
    registry = PipelineRegistry(
        search_paths=[str(root)],
        config_base_dir=tmp_path,
    )
    step = StepConfig.model_validate(
        {
            "name": "rerank",
            "type": "generate",
            "model_ref": "ok",
            "prompt_ref": "demo.dummy",
            "decision_table": {},
        }
    )
    pipeline = PipelineSpec.model_validate(
        {
            "schema_version": 6,
            "id": "inline",
            "version": "1.0",
            "type": "demo",
            "category": "demo",
            "output": "rerank",
            "steps": [step],
        }
    )
    errors = PipelineValidator(registry)._validate_pipeline(pipeline)
    assert any("rerank" in err and "decision_table is not supported" in err for err in errors)


def test_decision_table_error_names_step_and_is_not_retryable() -> None:
    assert failure_is_retryable(StepDefinitionError("rerank", "x")) is False
    assert failure_is_retryable(StepCallerError("rerank", "x")) is False
    assert (
        failure_is_retryable(
            StepTimeoutError(step_name="rerank", timeout_seconds=1)
        )
        is True
    )
