"""retrieve_exemplars pipeline_options must pass rag-search step_controls."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from systems.pipeline.core.handlers.registry import HandlerRegistry
from systems.pipeline.core.step_controls import apply_request_step_controls
from systems.pipeline.registry.core import PipelineRegistry
from systems.pipeline.user_handlers import load_user_handlers

pytestmark = pytest.mark.offline

REPO = Path(__file__).resolve().parents[3]
CHAIN_YAML = Path(__file__).resolve().parent / "writing-exemplar-v1.yaml"


def _retrieve_exemplar_pipeline_options() -> dict[str, object]:
    data = yaml.safe_load(CHAIN_YAML.read_text(encoding="utf-8"))
    for step in data.get("steps") or []:
        if step.get("name") == "retrieve_exemplars":
            options = step.get("pipeline_options")
            assert isinstance(options, dict), "retrieve_exemplars.pipeline_options missing"
            return dict(options)
    raise AssertionError("retrieve_exemplars step not found in writing-exemplar-v1.yaml")


def _load_rag_search_steps() -> tuple[object, list]:
    HandlerRegistry._ensure_initialized()
    load_user_handlers(REPO / "pipelines")
    reg = PipelineRegistry(
        search_paths=[str(REPO / "pipelines")],
        config_base_dir=REPO,
    )
    reg.load()
    pipeline = reg.pipelines["rag-search"]
    return pipeline, list(pipeline.steps)


def _step_enabled(steps: list, step_id: str) -> bool:
    for step in steps:
        if step.id == step_id:
            return bool(step.get_domain_field("enabled", True))
    raise AssertionError(f"step {step_id!r} not in rag-search")


def test_retrieve_exemplar_options_pass_rag_search_step_controls() -> None:
    """Break: legacy hyde_enabled/rerank_enabled keys → StepCallerError on rag-search."""
    runtime_options = _retrieve_exemplar_pipeline_options()
    assert "hyde_enabled" not in runtime_options
    assert "rerank_enabled" not in runtime_options

    pipeline, steps = _load_rag_search_steps()
    updated = apply_request_step_controls(pipeline, steps, runtime_options)

    assert _step_enabled(updated, "generate_hyde") is False
    assert _step_enabled(updated, "rerank") is False
