"""A health-restart flap must not full-build the pipeline registry once per cycle.

Each restart is a new process: ``load`` then ``reload_pipelines`` (which
``load``s a fresh registry). The count under test is search-path walks, not
wall-clock.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from systems.pipeline.core.domain_router import get_domain_router
from systems.pipeline.core.handlers.registry import HandlerRegistry
from systems.pipeline.registry.core import PipelineRegistry
from systems.pipeline.registry.loader import PipelineLoader
from systems.pipeline.registry.snapshot import snapshot_dir_for, source_fingerprint

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

_PROBE_STEP = "lane_probe_step"

_PROBE_YAML = f"""
schema_version: 6
id: pipe-p
version: "1.0"
type: ok_domain
output: author
steps:
  - name: author
    type: {_PROBE_STEP}
    model_ref: ok
    prompt_ref: ok_domain.dummy
"""


@pytest.fixture(autouse=True)
def _handlers() -> None:
    HandlerRegistry._ensure_initialized()


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.strip() + "\n", encoding="utf-8")


def _write_tree(root: Path, *, template: str = "hello") -> None:
    prompts = _OK_PROMPTS.replace("hello", template)
    domain = root / "ok_domain"
    _write(domain / "ok-v1.yaml", _OK_YAML)
    _write(domain / "models.yaml", _OK_MODELS)
    _write(domain / "prompts.yaml", prompts)


def _count_walks(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    builds = {"n": 0}
    real = PipelineLoader._load_from_search_path

    def counted(self: PipelineLoader, search_path: Path, path_name: str) -> None:
        builds["n"] += 1
        return real(self, search_path, path_name)

    monkeypatch.setattr(PipelineLoader, "_load_from_search_path", counted)
    return builds


def _enable_snapshot(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(
        "STARGATE_PIPELINE_REGISTRY_SNAPSHOT_DIR",
        str(tmp_path / "snap"),
    )


def _start(root: Path, *, checker=None) -> PipelineRegistry:
    """One process start: load, then the startup reload_pipelines pass."""
    registry = PipelineRegistry(
        search_paths=[str(root)],
        is_model_available=checker,
        config_base_dir=root.parent,
    )
    registry.load()
    registry.reload_pipelines()
    return registry


def test_health_restart_flap_does_not_full_build_once_per_cycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_snapshot(monkeypatch, tmp_path)
    root = tmp_path / "pipelines"
    _write_tree(root)
    builds = _count_walks(monkeypatch)

    n = 4
    last: PipelineRegistry | None = None
    for _ in range(n):
        last = _start(root)
    assert last is not None
    assert "ok-pipe" in last.pipelines
    assert builds["n"] == 1


def test_source_change_pays_another_full_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_snapshot(monkeypatch, tmp_path)
    root = tmp_path / "pipelines"
    _write_tree(root, template="hello")
    builds = _count_walks(monkeypatch)

    _start(root)
    _write_tree(root, template="hello-edited-source")
    last = _start(root)

    assert builds["n"] == 2
    assert "ok-pipe" in last.pipelines


def test_availability_change_pays_another_full_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_snapshot(monkeypatch, tmp_path)
    root = tmp_path / "pipelines"
    _write_tree(root)
    builds = _count_walks(monkeypatch)
    allowed = {"on": True}

    def checker(_model_id: str) -> bool:
        return allowed["on"]

    first = _start(root, checker=checker)
    assert "ok-pipe" in first.pipelines
    allowed["on"] = False
    second = _start(root, checker=checker)

    assert builds["n"] == 2
    assert "ok-pipe" not in second.pipelines


def test_reload_after_yaml_delete_drops_pipeline_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hot-reload rebuild must drop ids whose source YAML is gone (a:37652)."""
    _enable_snapshot(monkeypatch, tmp_path)
    root = tmp_path / "pipelines"
    _write_tree(root)
    registry = _start(root)
    assert "ok-pipe" in registry.pipelines

    (root / "ok_domain" / "ok-v1.yaml").unlink()
    old_count, new_count = registry.reload_pipelines()

    assert old_count == 1
    assert new_count == 0
    assert "ok-pipe" not in registry.pipelines


def test_reload_after_prompt_edit_updates_template(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Prompt YAML edits must land on the next reload without a process restart."""
    _enable_snapshot(monkeypatch, tmp_path)
    root = tmp_path / "pipelines"
    _write_tree(root, template="hello")
    registry = _start(root)
    assert registry.prompts["ok_domain"]["dummy"]["template"] == "hello"

    _write_tree(root, template="hello-edited")
    registry.reload_pipelines()

    assert registry.prompts["ok_domain"]["dummy"]["template"] == "hello-edited"


class _LaneProbeHandler:
    step_type = _PROBE_STEP


def _register_probe() -> None:
    HandlerRegistry.register_class(_LaneProbeHandler)


def _unregister_probe() -> None:
    HandlerRegistry._generic_handler_classes.pop(_PROBE_STEP, None)
    get_domain_router()._generic_handler_classes.pop(_PROBE_STEP, None)


def _snapshot_file(root: Path) -> Path:
    registry = PipelineRegistry(
        search_paths=[str(root)],
        config_base_dir=root.parent,
    )
    directory = snapshot_dir_for(registry)
    assert directory is not None
    return directory / f"{source_fingerprint(registry)}.json"


def test_catalog_reload_keeps_pipeline_absent_from_matching_availability_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same-availability entry built before P's handler must not replace the walk."""
    _enable_snapshot(monkeypatch, tmp_path)
    root = tmp_path / "pipelines"
    _write_tree(root)
    _write(root / "ok_domain" / "pipe-p.yaml", _PROBE_YAML)
    try:
        first = _start(root)
        assert "ok-pipe" in first.pipelines
        assert "pipe-p" not in first.pipelines

        _register_probe()
        old_count, new_count = first.reload_pipelines()

        assert old_count == 1
        assert new_count == 2
        assert "pipe-p" in first.pipelines
    finally:
        _unregister_probe()


def test_availability_only_snapshot_entry_is_not_reused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Availability-only entries (no definition fingerprint) are not restored."""
    _enable_snapshot(monkeypatch, tmp_path)
    root = tmp_path / "pipelines"
    _write_tree(root)
    _write(root / "ok_domain" / "pipe-p.yaml", _PROBE_YAML)
    try:
        _register_probe()
        registry = _start(root)
        assert "pipe-p" in registry.pipelines

        path = _snapshot_file(root)
        payload = json.loads(path.read_text(encoding="utf-8"))
        current = payload["entries"][0]
        stale_state = json.loads(json.dumps(current["state"]))
        stale_state["pipelines"].pop("pipe-p")
        payload["entries"] = [
            {"availability": current["availability"], "state": stale_state}
        ]
        path.write_text(json.dumps(payload), encoding="utf-8")

        registry.reload_pipelines()
        assert "pipe-p" in registry.pipelines
    finally:
        _unregister_probe()
