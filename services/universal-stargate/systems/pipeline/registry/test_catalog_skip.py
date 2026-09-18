"""Offline tests for domain-root models.yaml catalog-skip records."""

from __future__ import annotations

from pathlib import Path

import pytest

from systems.pipeline.core.handlers.registry import HandlerRegistry
from systems.pipeline.registry.core import PipelineRegistry

pytestmark = pytest.mark.offline

_OK_MODEL = "phi-4-q4-k-m-16384"

_GHOST_YAML = """
schema_version: 6
id: ghost-pipe
version: "1.0"
type: ghost_domain
output: author
steps:
  - name: author
    type: generate
    model_ref: expand
    prompt_ref: ghost_domain.dummy
"""

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

_ALIAS_MISS_MODELS = """
models:
  other:
    model: phi-4-q4-k-m-16384
"""


@pytest.fixture(autouse=True)
def _handlers() -> None:
    HandlerRegistry._ensure_initialized()


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.strip() + "\n", encoding="utf-8")


def test_missing_domain_models_yaml_records_structured_skip(tmp_path: Path) -> None:
    ghost = tmp_path / "ghost_domain"
    _write(ghost / "ghost-v1.yaml", _GHOST_YAML)

    ok = tmp_path / "ok_domain"
    _write(ok / "ok-v1.yaml", _OK_YAML)
    _write(ok / "models.yaml", _OK_MODELS)
    _write(ok / "prompts.yaml", _OK_PROMPTS)

    registry = PipelineRegistry(search_paths=[str(tmp_path)], config_base_dir=tmp_path)
    registry.load()

    expected = str((tmp_path / "ghost_domain" / "models.yaml").resolve())
    skip = next(row for row in registry.catalog_skips if row["pipeline_id"] == "ghost-pipe")
    assert skip["alias"] == "expand"
    assert skip["reason"] == "missing_domain_models_yaml"
    assert skip["expected_models_yaml"] == expected
    assert "ghost-pipe" not in registry.pipelines
    assert "ok-pipe" in registry.pipelines

    with pytest.raises(KeyError, match="ghost-pipe") as exc:
        registry.get_pipeline("ghost-pipe")
    message = str(exc.value)
    assert "expand" in message
    assert "missing_domain_models_yaml" in message
    assert expected in message


def test_present_models_yaml_unknown_alias_is_not_missing_file(tmp_path: Path) -> None:
    domain = tmp_path / "ghost_domain"
    _write(domain / "ghost-v1.yaml", _GHOST_YAML)
    _write(domain / "models.yaml", _ALIAS_MISS_MODELS)
    _write(domain / "prompts.yaml", _OK_PROMPTS)

    registry = PipelineRegistry(search_paths=[str(tmp_path)], config_base_dir=tmp_path)
    registry.load()

    skip = next(row for row in registry.catalog_skips if row["pipeline_id"] == "ghost-pipe")
    assert skip["reason"] == "unknown_model_ref"
    assert Path(skip["expected_models_yaml"]).is_file()
    assert skip["alias"] == "expand"
