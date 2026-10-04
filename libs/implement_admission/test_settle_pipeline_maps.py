"""Tests for functional settle pipeline map loader."""

from __future__ import annotations

from universal_workspace import get_workspace_root

from implement_admission.settle_pipeline_maps import functional_settle_pipeline_maps


def test_functional_settle_pipeline_maps_includes_known_pipeline() -> None:
    repo = get_workspace_root()
    maps = functional_settle_pipeline_maps(repo)
    assert maps.pipeline_sources
    assert any(
        path.endswith(".yaml") for paths in maps.pipeline_sources.values() for path in paths
    )
