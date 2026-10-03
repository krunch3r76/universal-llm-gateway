"""Registry load: rag-search pipeline is present under pipelines/."""

from __future__ import annotations

from pathlib import Path

import pytest
from systems.pipeline.core.handlers.registry import HandlerRegistry
from systems.pipeline.registry.core import PipelineRegistry
from systems.pipeline.registry.validator import PipelineValidator
from systems.pipeline.user_handlers import load_user_handlers

pytestmark = pytest.mark.offline

REPO = Path(__file__).resolve().parents[3]


def test_rag_search_registered_in_pipelines_tree() -> None:
    HandlerRegistry._ensure_initialized()
    load_user_handlers(REPO / "pipelines")
    reg = PipelineRegistry(
        search_paths=[str(REPO / "pipelines")],
        config_base_dir=REPO,
    )
    reg.load()
    assert "rag-search" in reg.pipelines
    pipeline = reg.pipelines["rag-search"]
    errors = PipelineValidator(reg)._validate_pipeline(pipeline)
    assert errors == []
    assert (REPO / "pipelines" / "rag" / "models.yaml").is_file()
    assert (REPO / "pipelines" / "rag_search" / "models.yaml").is_file()
    assert not (REPO / "pipelines" / "rag_search" / "v1" / "models.yaml").exists()
