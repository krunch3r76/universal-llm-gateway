"""Category vocabulary union and catalog-skip rows."""

from __future__ import annotations

from pathlib import Path

import pytest

from systems.pipeline.core.handlers.registry import HandlerRegistry
from systems.pipeline.registry.core import PipelineRegistry

pytestmark = pytest.mark.offline


@pytest.fixture(autouse=True)
def _handlers() -> None:
    HandlerRegistry._ensure_initialized()


def _spec(pipeline_id: str, category: str | None) -> str:
    category_line = f"category: {category}\n" if category is not None else ""
    return (
        "schema_version: 6\n"
        f"id: {pipeline_id}\n"
        'version: "1.0"\n'
        "type: demo\n"
        f"{category_line}"
        "output: author\n"
        "steps:\n"
        "  - name: author\n"
        "    type: generate\n"
        "    model_ref: ok\n"
        "    prompt_ref: demo.dummy\n"
    )


def _support(domain: Path) -> None:
    domain.mkdir(parents=True, exist_ok=True)
    (domain / "models.yaml").write_text(
        "models:\n  ok:\n    model: phi-4-q4-k-m-16384\n", encoding="utf-8"
    )
    (domain / "prompts.yaml").write_text(
        "prompts:\n  dummy:\n    description: fixture\n    template: hello\n",
        encoding="utf-8",
    )


def test_union_loads_member_from_second_root(tmp_path: Path) -> None:
    first = tmp_path / "a"
    second = tmp_path / "b"
    _support(first / "demo")
    _support(second / "demo")
    (first / "categories.yaml").write_text(
        "categories:\n  demo:\n    description: one\n", encoding="utf-8"
    )
    (second / "categories.yaml").write_text(
        "categories:\n  other:\n    description: two\n", encoding="utf-8"
    )
    (second / "demo" / "pipe.yaml").write_text(
        _spec("from-second", "other"), encoding="utf-8"
    )
    registry = PipelineRegistry(
        search_paths=[str(first), str(second)],
        config_base_dir=tmp_path,
    )
    registry.load()
    assert "from-second" in registry.pipelines
    assert "other" in registry._category_vocabulary


def test_derived_category_registers_under_domain_directory(tmp_path: Path) -> None:
    root = tmp_path / "root"
    _support(root / "demo")
    (root / "categories.yaml").write_text(
        "categories:\n  other:\n    description: d\n", encoding="utf-8"
    )
    (root / "demo" / "good.yaml").write_text(
        _spec("good-pipe", "other"), encoding="utf-8"
    )
    (root / "demo" / "bare.yaml").write_text(
        _spec("bare-pipe", None), encoding="utf-8"
    )
    registry = PipelineRegistry(
        search_paths=[str(root)],
        config_base_dir=tmp_path,
    )
    registry.load()
    assert "good-pipe" in registry.pipelines
    assert "bare-pipe" in registry.pipelines
    assert registry.pipelines["bare-pipe"].category == "demo"
    assert "demo" in registry._category_vocabulary


def test_domain_field_wins_over_version_directory(tmp_path: Path) -> None:
    """A version segment (v + digits) must not become the category."""
    root = tmp_path / "root"
    _support(root / "demo")
    version = root / "demo" / "v1"
    version.mkdir()
    (version / "prompts.yaml").write_text(
        "prompts:\n  dummy:\n    description: fixture\n    template: hello\n",
        encoding="utf-8",
    )
    body = _spec("chat-dispatch", None).replace(
        "prompt_ref: demo.dummy", "prompt_ref: demo.v1.dummy"
    )
    (version / "chat.yaml").write_text(
        body + "domain: frontier_dispatch\n",
        encoding="utf-8",
    )
    (version / "team.yaml").write_text(
        body.replace("id: chat-dispatch", "id: team-dispatch")
        + "domain: frontier_dispatch\n",
        encoding="utf-8",
    )
    registry = PipelineRegistry(
        search_paths=[str(root)],
        config_base_dir=tmp_path,
    )
    registry.load()
    assert registry.pipelines["chat-dispatch"].category == "frontier_dispatch"
    assert registry.pipelines["team-dispatch"].category == "frontier_dispatch"
    assert "v1" not in registry._category_vocabulary


def test_overlay_without_domain_uses_top_directory_not_tone(tmp_path: Path) -> None:
    root = tmp_path / "root"
    _support(root / "transformation")
    tone = root / "transformation" / "neutral"
    tone.mkdir()
    (tone / "prompts.yaml").write_text(
        "prompts:\n  dummy:\n    description: fixture\n    template: hello\n",
        encoding="utf-8",
    )
    body = (
        _spec("tone-overlay", None)
        .replace("type: demo", "type: transformation")
        .replace("prompt_ref: demo.dummy", "prompt_ref: transformation.neutral.dummy")
    )
    (tone / "overlay.yaml").write_text(body, encoding="utf-8")
    registry = PipelineRegistry(
        search_paths=[str(root)],
        config_base_dir=tmp_path,
    )
    registry.load()
    assert registry.pipelines["tone-overlay"].category == "transformation"
    assert "neutral" not in registry._category_vocabulary


def test_unknown_category_records_skip(tmp_path: Path) -> None:
    root = tmp_path / "root"
    _support(root / "demo")
    (root / "categories.yaml").write_text(
        "categories:\n  demo:\n    description: d\n", encoding="utf-8"
    )
    (root / "demo" / "odd.yaml").write_text(_spec("odd-pipe", "nope"), encoding="utf-8")
    registry = PipelineRegistry(
        search_paths=[str(root)],
        config_base_dir=tmp_path,
    )
    registry.load()
    assert "odd-pipe" not in registry.pipelines
    row = next(
        item for item in registry.catalog_skips if item["pipeline_id"] == "odd-pipe"
    )
    assert row["reason"] == "unknown_category"
    assert row["category"] == "nope"
    assert "alias" not in row
