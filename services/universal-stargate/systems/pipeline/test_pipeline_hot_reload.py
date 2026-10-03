"""PipelineHotReload must rebuild on YAML delete, not only on modify (a:37652)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from systems.pipeline.core.handlers.registry import HandlerRegistry
from systems.pipeline.hot_reload import PipelineHotReload
from systems.pipeline.registry.core import PipelineRegistry

pytestmark = pytest.mark.offline

_OK_MODEL = "phi-4-q4-k-m-16384"

_OK_YAML = """
schema_version: 6
id: ok-pipe
version: "1.0"
type: ok_domain
output: author
steps:
  - name: author
    type: generate
    model_ref: ok
    prompt_ref: ok_domain.dummy
"""

_OK_MODELS = f"""
models:
  ok:
    model: {_OK_MODEL}
"""

_OK_PROMPTS = """
prompts:
  dummy:
    description: fixture
    template: "hello"
"""


@pytest.fixture(autouse=True)
def _handlers() -> None:
    HandlerRegistry._ensure_initialized()


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.strip() + "\n", encoding="utf-8")


def _write_tree(root: Path) -> None:
    domain = root / "ok_domain"
    _write(domain / "ok-v1.yaml", _OK_YAML)
    _write(domain / "models.yaml", _OK_MODELS)
    _write(domain / "prompts.yaml", _OK_PROMPTS)


@pytest.mark.asyncio
async def test_hot_reload_unregisters_deleted_pipeline_yaml(tmp_path: Path) -> None:
    root = tmp_path / "pipelines"
    _write_tree(root)
    yaml_path = root / "ok_domain" / "ok-v1.yaml"
    registry = PipelineRegistry(
        search_paths=[str(root)],
        config_base_dir=root.parent,
    )
    registry.load()
    assert "ok-pipe" in registry.pipelines

    hot = PipelineHotReload(registry=registry, debounce_ms=80, enabled=True)
    assert await hot.start()
    try:
        await asyncio.sleep(0.2)
        yaml_path.unlink()
        await asyncio.sleep(0.5)
        assert "ok-pipe" not in registry.pipelines
    finally:
        await hot.stop()


@pytest.mark.asyncio
async def test_hot_reload_registers_added_pipeline_yaml(tmp_path: Path) -> None:
    root = tmp_path / "pipelines"
    _write_tree(root)
    yaml_path = root / "ok_domain" / "ok-v1.yaml"
    yaml_text = yaml_path.read_text(encoding="utf-8")
    yaml_path.unlink()
    registry = PipelineRegistry(
        search_paths=[str(root)],
        config_base_dir=root.parent,
    )
    registry.load()
    assert "ok-pipe" not in registry.pipelines

    hot = PipelineHotReload(registry=registry, debounce_ms=80, enabled=True)
    assert await hot.start()
    try:
        await asyncio.sleep(0.2)
        yaml_path.write_text(yaml_text, encoding="utf-8")
        await asyncio.sleep(0.5)
        assert "ok-pipe" in registry.pipelines
    finally:
        await hot.stop()


@pytest.mark.asyncio
async def test_hot_reload_applies_prompt_edit(tmp_path: Path) -> None:
    root = tmp_path / "pipelines"
    _write_tree(root)
    registry = PipelineRegistry(
        search_paths=[str(root)],
        config_base_dir=root.parent,
    )
    registry.load()
    assert registry.prompts["ok_domain"]["dummy"]["template"] == "hello"

    hot = PipelineHotReload(registry=registry, debounce_ms=80, enabled=True)
    assert await hot.start()
    try:
        await asyncio.sleep(0.2)
        (root / "ok_domain" / "prompts.yaml").write_text(
            'prompts:\n  dummy:\n    description: fixture\n    template: "next-run"\n',
            encoding="utf-8",
        )
        await asyncio.sleep(0.5)
        assert registry.prompts["ok_domain"]["dummy"]["template"] == "next-run"
    finally:
        await hot.stop()
