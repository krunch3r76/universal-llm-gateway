"""Repo-root jobs category is in pipeline vocabulary and matches the jobs satellite."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from capability_tree.vocabulary import CategoryVocabulary
from jobs.spec import load_registry

from systems.proxy.routers.api.capability_vocabulary import load_vocabulary

pytestmark = pytest.mark.offline

_REPO = Path(__file__).resolve().parents[5]


def test_pipeline_categories_yaml_includes_jobs() -> None:
    path = _REPO / "pipelines" / "categories.yaml"
    vocabulary = CategoryVocabulary.load([path])
    assert "jobs" in vocabulary
    assert "operator" in vocabulary.descriptions["jobs"].lower()


def test_capabilities_yaml_jobs_upstream_is_jobs_satellite() -> None:
    path = _REPO / "config" / "capabilities.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    jobs = raw["categories"]["jobs"]
    assert jobs["implementation"] == "satellite"
    assert jobs["upstream"] == "jobs"
    assert jobs["origin_prefix"] == "/api/v1/jobs"

    loaded = load_vocabulary()
    names = {cat.name for cat in loaded.categories}
    assert "jobs" in names
    by_name = {cat.name: cat for cat in loaded.categories}
    assert by_name["jobs"].upstream_symbol == "jobs"


def test_production_jobs_registry_handles_use_jobs_category() -> None:
    specs = load_registry()
    assert specs
    for spec in specs:
        assert spec.handle.startswith("capability:jobs/"), spec.name
