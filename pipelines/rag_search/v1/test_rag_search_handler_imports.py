"""Production registry loader: rag-search handlers and lazy imports resolve."""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest
from systems.pipeline.core.handlers.registry import HandlerRegistry
from systems.pipeline.registry.core import PipelineRegistry
from systems.pipeline.user_handlers import load_user_handlers

pytestmark = pytest.mark.offline

REPO = Path(__file__).resolve().parents[3]
_LOADER_PKG = "_pipeline_handlers_rag_search_v1"


def _load_rag_search_via_production_path() -> PipelineRegistry:
    HandlerRegistry._ensure_initialized()
    load_user_handlers(REPO / "pipelines")
    reg = PipelineRegistry(
        search_paths=[str(REPO / "pipelines")],
        config_base_dir=REPO,
    )
    reg.load()
    assert "rag-search" in reg.pipelines
    pipeline = reg.pipelines["rag-search"]
    assert pipeline.source_variant == "v1"
    return reg


def test_rag_search_step_handler_classes_importable() -> None:
    """Breaks when handlers/__init__.py or a sibling module is missing under v1/."""
    reg = _load_rag_search_via_production_path()
    pipeline = reg.pipelines["rag-search"]
    for step in pipeline.steps:
        if not step.get_domain_field("enabled", True):
            continue
        HandlerRegistry.get_class_or_raise(
            pipeline.type,
            step.type,
            variant=pipeline.source_variant,
        )


def test_rag_search_lazy_term_expansion_import() -> None:
    """Breaks when term_expansion.py is absent (retrieve step pool B facet path)."""
    _load_rag_search_via_production_path()
    retrieval = importlib.import_module(f"{_LOADER_PKG}.retrieval_execution")
    facets = retrieval.compute_facets_from_text(
        "pipeline retrieve step pool B facet expansion",
        max_idf_terms=0,
    )
    assert isinstance(facets, list)
