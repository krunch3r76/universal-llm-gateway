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


def _registry(root: Path, tmp_path: Path) -> PipelineRegistry:
    # Snapshot enabled: a stale restore would hide delete/prompt edits (review nit).
    return PipelineRegistry(
        search_paths=[str(root)],
        config_base_dir=root.parent,
        snapshot_dir=tmp_path / "snap",
    )


@pytest.mark.asyncio
async def test_hot_reload_wires_on_delete_callback(tmp_path: Path) -> None:
    """Without on_delete, Change.deleted is dropped (base 6ecdf2e7c)."""
    root = tmp_path / "pipelines"
    _write_tree(root)
    registry = _registry(root, tmp_path)
    registry.load()
    hot = PipelineHotReload(registry=registry, debounce_ms=80, enabled=True)
    assert await hot.start()
    try:
        assert hot._watchers
        for watcher in hot._watchers:
            assert watcher.on_delete is not None
    finally:
        await hot.stop()


@pytest.mark.asyncio
async def test_hot_reload_unregisters_deleted_pipeline_yaml(tmp_path: Path) -> None:
    root = tmp_path / "pipelines"
    _write_tree(root)
    yaml_path = root / "ok_domain" / "ok-v1.yaml"
    registry = _registry(root, tmp_path)
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
    registry = _registry(root, tmp_path)
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
    registry = _registry(root, tmp_path)
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


@pytest.mark.asyncio
async def test_hot_reload_unregisters_yaml_moved_out_of_tree(tmp_path: Path) -> None:
    root = tmp_path / "pipelines"
    _write_tree(root)
    yaml_path = root / "ok_domain" / "ok-v1.yaml"
    registry = _registry(root, tmp_path)
    registry.load()
    assert "ok-pipe" in registry.pipelines

    hot = PipelineHotReload(registry=registry, debounce_ms=80, enabled=True)
    assert await hot.start()
    try:
        await asyncio.sleep(0.2)
        yaml_path.rename(tmp_path / "ok-v1-moved.yaml")
        await asyncio.sleep(0.5)
        assert "ok-pipe" not in registry.pipelines
    finally:
        await hot.stop()


_BOGUS_STEP_TYPE = "bogus_unregistered_step_type_xyz"


def test_reload_keeps_last_good_when_step_type_unregistered(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    root = tmp_path / "pipelines"
    _write_tree(root)
    registry = _registry(root, tmp_path)
    registry.load()
    assert "ok-pipe" in registry.pipelines
    previous_step_type = registry.pipelines["ok-pipe"].steps[0].type

    bad_yaml = _OK_YAML.replace("type: generate", f"type: {_BOGUS_STEP_TYPE}")
    _write(root / "ok_domain" / "ok-v1.yaml", bad_yaml)
    with caplog.at_level("WARNING"):
        old_count, new_count = registry.reload_pipelines()

    assert "ok-pipe" in registry.pipelines
    assert registry.pipelines["ok-pipe"].steps[0].type == previous_step_type
    assert old_count == new_count == 1
    assert any(
        "Keeping last good pipeline 'ok-pipe'" in rec.message
        and _BOGUS_STEP_TYPE in rec.message
        for rec in caplog.records
    )


def test_reload_unregistered_step_type_never_loaded_stays_absent(
    tmp_path: Path,
) -> None:
    root = tmp_path / "pipelines"
    _write_tree(root)
    bad_yaml = _OK_YAML.replace("type: generate", f"type: {_BOGUS_STEP_TYPE}")
    _write(root / "ok_domain" / "ok-v1.yaml", bad_yaml)
    registry = _registry(root, tmp_path)
    registry.load()
    assert "ok-pipe" not in registry.pipelines

    registry.reload_pipelines()
    assert "ok-pipe" not in registry.pipelines


def test_reload_drops_pipeline_on_catalog_skip_not_unregistered_type(
    tmp_path: Path,
) -> None:
    root = tmp_path / "pipelines"
    _write_tree(root)
    registry = _registry(root, tmp_path)
    registry.load()
    assert "ok-pipe" in registry.pipelines

    bad_yaml = _OK_YAML.replace("model_ref: ok", "model_ref: expand")
    _write(root / "ok_domain" / "ok-v1.yaml", bad_yaml)
    registry.reload_pipelines()
    assert "ok-pipe" not in registry.pipelines
    assert any(row["pipeline_id"] == "ok-pipe" for row in registry.catalog_skips)


def test_reload_drops_when_handler_and_catalog_errors_mixed(tmp_path: Path) -> None:
    root = tmp_path / "pipelines"
    _write_tree(root)
    registry = _registry(root, tmp_path)
    registry.load()
    assert "ok-pipe" in registry.pipelines

    bad_yaml = _OK_YAML.replace("type: generate", f"type: {_BOGUS_STEP_TYPE}").replace(
        "model_ref: ok", "model_ref: expand"
    )
    _write(root / "ok_domain" / "ok-v1.yaml", bad_yaml)
    registry.reload_pipelines()
    assert "ok-pipe" not in registry.pipelines


def test_reload_does_not_restore_when_prior_models_unavailable(
    tmp_path: Path,
) -> None:
    root = tmp_path / "pipelines"
    _write_tree(root)
    registry = PipelineRegistry(
        search_paths=[str(root)],
        config_base_dir=root.parent,
        snapshot_dir=tmp_path / "snap",
        is_model_available=lambda _model_id: True,
    )
    registry.load()
    assert "ok-pipe" in registry.pipelines

    bad_yaml = _OK_YAML.replace("type: generate", f"type: {_BOGUS_STEP_TYPE}")
    _write(root / "ok_domain" / "ok-v1.yaml", bad_yaml)
    registry._is_model_available = lambda _model_id: False
    registry.reload_pipelines()
    assert "ok-pipe" not in registry.pipelines
