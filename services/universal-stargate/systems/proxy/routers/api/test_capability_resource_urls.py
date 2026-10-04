"""Offline coverage for capability resource URLs (review withhold rows)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from systems.pipeline.core.handlers.registry import HandlerRegistry
from systems.pipeline.registry.core import PipelineRegistry
from systems.proxy.dependencies import get_auth_dependency, get_proxy
from systems.proxy.routers.api import capabilities, executions, providers_cdp

pytestmark = pytest.mark.offline

_MODEL = "phi-4-q4-k-m-16384"


@pytest.fixture(autouse=True)
def _handlers() -> None:
    HandlerRegistry._ensure_initialized()


def _spec(pipeline_id: str, category: str | None = "demo") -> str:
    category_line = (
        f"category: {category}\n" if category is not None else ""
    )
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


def _write_root(root: Path, pipeline_id: str = "demo-pipe") -> None:
    domain = root / "demo"
    domain.mkdir(parents=True)
    (root / "categories.yaml").write_text(
        "categories:\n  demo:\n    description: d\n",
        encoding="utf-8",
    )
    (domain / "pipe.yaml").write_text(_spec(pipeline_id, "demo"), encoding="utf-8")
    (domain / "models.yaml").write_text(
        f"models:\n  ok:\n    model: {_MODEL}\n",
        encoding="utf-8",
    )
    (domain / "prompts.yaml").write_text(
        "prompts:\n  dummy:\n    description: fixture\n    template: hello\n",
        encoding="utf-8",
    )


def _registry(root: Path) -> PipelineRegistry:
    registry = PipelineRegistry(
        search_paths=[str(root)],
        config_base_dir=root.parent,
    )
    registry.load()
    return registry


def _client(proxy: Any, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    async def _missing(*_args: object, **_kwargs: object) -> None:
        return None

    tracker = SimpleNamespace(
        records={},
        max_records=8,
        retention_seconds=60,
        wait_for_terminal=_missing,
        _agent_bus_url="unix:///tmp/agent-bus.sock",
        _agent_bus_token="test-token",
    )
    monkeypatch.setattr(executions, "_get_tracker", lambda _proxy: tracker)
    monkeypatch.setattr(executions, "fetch_terminal", _missing)
    monkeypatch.setattr(executions, "recover_execution_from_bus_thread", _missing)
    app = FastAPI()
    parent = APIRouter(prefix="/api/v1")
    parent.include_router(capabilities.router)
    parent.include_router(executions.router)
    parent.include_router(providers_cdp.router)
    app.include_router(parent)
    app.dependency_overrides[get_proxy] = lambda: proxy
    app.dependency_overrides[get_auth_dependency] = lambda: {}
    return TestClient(app)


def _ready_proxy(registry: PipelineRegistry) -> SimpleNamespace:
    return SimpleNamespace(
        pipeline_registry=registry,
        is_pipeline_system_ready=True,
    )


def test_stats_path_is_not_an_execution_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Wrong route order would treat 'stats' as an execution id."""
    root = tmp_path / "pipelines"
    _write_root(root)
    client = _client(_ready_proxy(_registry(root)), monkeypatch)
    response = client.get("/api/v1/executions/stats")
    assert response.status_code == 200, response.text
    assert "running" in response.json()
    assert "error" not in response.json()


def test_body_path_id_mismatch_is_422(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "pipelines"
    _write_root(root)
    client = _client(_ready_proxy(_registry(root)), monkeypatch)
    response = client.post(
        "/api/v1/capabilities/demo/demo-pipe",
        json={"model": "other-pipe", "messages": []},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "capability_id_mismatch"


def test_noncanonical_segment_stays_200_with_canonical_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "pipelines"
    _write_root(root)
    client = _client(_ready_proxy(_registry(root)), monkeypatch)
    response = client.get(
        "/api/v1/capabilities/wrong/demo-pipe",
        follow_redirects=False,
    )
    assert response.history == []
    assert response.status_code == 200, response.text
    link = response.headers["link"]
    assert 'rel="canonical"' in link
    assert "/api/v1/capabilities/demo/demo-pipe" in link


def test_noncanonical_post_stays_202_with_canonical_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "pipelines"
    _write_root(root)

    async def _admit(_request: Any, _proxy: Any, _dispatch: Any) -> JSONResponse:
        return JSONResponse(
            status_code=202,
            content={"execution_id": "exec-wrong-cat"},
            headers={"Location": "/api/v1/executions/exec-wrong-cat"},
        )

    monkeypatch.setattr(capabilities, "admit_dispatch", _admit)
    client = _client(_ready_proxy(_registry(root)), monkeypatch)
    response = client.post(
        "/api/v1/capabilities/wrong/demo-pipe",
        json={"model": "demo-pipe", "messages": []},
        follow_redirects=False,
    )
    assert response.history == []
    assert response.status_code == 202, response.text
    assert response.json()["execution_id"] == "exec-wrong-cat"
    link = response.headers["link"]
    assert 'rel="canonical"' in link
    assert "/api/v1/capabilities/demo/demo-pipe" in link


def test_legacy_pipeline_routes_are_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "pipelines"
    _write_root(root)
    client = _client(_ready_proxy(_registry(root)), monkeypatch)
    missing = [
        client.post("/api/v1/pipelines/dispatch", json={"model": "demo-pipe"}),
        client.get("/api/v1/pipelines"),
        client.get("/api/v1/pipelines/executions/exec-1"),
        client.delete("/api/v1/pipelines/executions/exec-1"),
    ]
    assert [item.status_code for item in missing] == [404, 404, 404, 404]


def test_unknown_id_near_matches_cap_and_no_full_dump(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "pipelines"
    root.mkdir()
    domain = root / "demo"
    domain.mkdir()
    (root / "categories.yaml").write_text(
        "categories:\n  demo:\n    description: d\n",
        encoding="utf-8",
    )
    (domain / "models.yaml").write_text(
        f"models:\n  ok:\n    model: {_MODEL}\n",
        encoding="utf-8",
    )
    (domain / "prompts.yaml").write_text(
        "prompts:\n  dummy:\n    description: fixture\n    template: hello\n",
        encoding="utf-8",
    )
    ids = [f"alpha-{index}" for index in range(8)]
    for pipeline_id in ids:
        (domain / f"{pipeline_id}.yaml").write_text(
            _spec(pipeline_id, "demo"),
            encoding="utf-8",
        )
    client = _client(_ready_proxy(_registry(root)), monkeypatch)
    response = client.get("/api/v1/capabilities", params={"id": "alpha-0x"})
    assert response.status_code == 404
    matches = response.json()["error"]["data"]["near_matches"]
    assert len(matches) == 5
    assert len(ids) > 5
    assert "members" not in response.json()


def test_derived_category_pipeline_invocable_by_id_and_category_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "pipelines"
    domain = root / "demo"
    domain.mkdir(parents=True)
    (domain / "bare.yaml").write_text(_spec("bare-pipe", None), encoding="utf-8")
    (domain / "models.yaml").write_text(
        f"models:\n  ok:\n    model: {_MODEL}\n",
        encoding="utf-8",
    )
    (domain / "prompts.yaml").write_text(
        "prompts:\n  dummy:\n    description: fixture\n    template: hello\n",
        encoding="utf-8",
    )
    registry = _registry(root)
    assert registry.pipelines["bare-pipe"].category == "demo"

    async def _admit(_request: Any, _proxy: Any, _dispatch: Any) -> JSONResponse:
        return JSONResponse(
            status_code=202,
            content={"execution_id": "exec-bare"},
            headers={"Location": "/api/v1/executions/exec-bare"},
        )

    monkeypatch.setattr(capabilities, "admit_dispatch", _admit)
    client = _client(_ready_proxy(registry), monkeypatch)
    by_id = client.get("/api/v1/capabilities", params={"id": "bare-pipe"})
    assert by_id.status_code == 200, by_id.text
    assert 'rel="canonical"' in by_id.headers["link"]
    assert "/api/v1/capabilities/demo/bare-pipe" in by_id.headers["link"]
    canonical = "/api/v1/capabilities/demo/bare-pipe"
    by_category = client.get(canonical)
    assert by_category.status_code == 200, by_category.text
    assert by_category.json()["url"] == canonical
    response = client.post(
        canonical, json={"model": "bare-pipe", "messages": []}
    )
    assert response.status_code == 202, response.text
    assert response.json()["execution_id"] == "exec-bare"


def test_domain_category_filter_and_bare_id_post(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Version-directory yaml with a domain field is filterable and invocable."""
    root = tmp_path / "pipelines"
    dispatch = root / "demo"
    dispatch.mkdir(parents=True)
    nested = dispatch / "v1"
    nested.mkdir()
    (nested / "prompts.yaml").write_text(
        "prompts:\n  dummy:\n    description: fixture\n    template: hello\n",
        encoding="utf-8",
    )
    chat = _spec("chat-dispatch", None).replace(
        "prompt_ref: demo.dummy", "prompt_ref: demo.v1.dummy"
    )
    (nested / "chat.yaml").write_text(
        chat + "domain: frontier_dispatch\n",
        encoding="utf-8",
    )
    (nested / "team.yaml").write_text(
        chat.replace("id: chat-dispatch", "id: team-dispatch")
        + "domain: frontier_dispatch\n",
        encoding="utf-8",
    )
    overlay_root = root / "transformation"
    overlay_root.mkdir()
    romantic = overlay_root / "romantic"
    romantic.mkdir()
    (romantic / "prompts.yaml").write_text(
        "prompts:\n  dummy:\n    description: fixture\n    template: hello\n",
        encoding="utf-8",
    )
    overlay = (
        _spec("tone-overlay", None)
        .replace("type: demo", "type: transformation")
        .replace(
            "prompt_ref: demo.dummy",
            "prompt_ref: transformation.romantic.dummy",
        )
    )
    (romantic / "overlay.yaml").write_text(overlay, encoding="utf-8")
    for folder in (dispatch, overlay_root):
        (folder / "models.yaml").write_text(
            f"models:\n  ok:\n    model: {_MODEL}\n",
            encoding="utf-8",
        )
    registry = _registry(root)
    assert registry.pipelines["chat-dispatch"].category == "frontier_dispatch"
    assert registry.pipelines["tone-overlay"].category == "transformation"

    async def _admit(_request: Any, _proxy: Any, dispatch: Any) -> JSONResponse:
        return JSONResponse(
            status_code=202,
            content={"execution_id": dispatch.model},
        )

    monkeypatch.setattr(capabilities, "admit_dispatch", _admit)
    client = _client(_ready_proxy(registry), monkeypatch)
    listed = client.get(
        "/api/v1/capabilities", params={"category": "frontier_dispatch"}
    )
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert "chat-dispatch" in body["members"]
    assert "team-dispatch" in body["members"]
    assert body["members"]["chat-dispatch"]["version"] == "1.0"
    assert "description" in body
    index = client.get("/api/v1/capabilities")
    assert index.status_code == 200, index.text
    names = {row["name"]: row for row in index.json()["categories"]}
    assert names["frontier_dispatch"]["href"] == "/api/v1/capabilities/frontier_dispatch"
    assert "description" in names["frontier_dispatch"]
    assert names["transformation"]["href"] == "/api/v1/capabilities/transformation"
    canonical = "/api/v1/capabilities/frontier_dispatch/chat-dispatch"
    posted = client.post(canonical, json={"model": "chat-dispatch", "messages": []})
    assert posted.status_code == 202, posted.text
    bare = client.post(
        "/api/v1/capabilities/chat-dispatch",
        json={"model": "chat-dispatch", "messages": []},
        follow_redirects=False,
    )
    assert bare.status_code == 308, bare.text
    assert bare.headers["location"].endswith(canonical)
    followed = client.post(
        "/api/v1/capabilities/team-dispatch",
        json={"model": "team-dispatch", "messages": []},
    )
    assert followed.status_code == 202, followed.text
    assert followed.json()["execution_id"] == "team-dispatch"


def test_hot_reload_new_yaml_is_invocable_at_canonical_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "pipelines"
    _write_root(root)
    registry = _registry(root)
    (root / "demo" / "fresh.yaml").write_text(
        _spec("fresh-pipe", "demo"),
        encoding="utf-8",
    )
    registry.reload_pipelines()

    async def _admit(_request: Any, _proxy: Any, _dispatch: Any) -> JSONResponse:
        return JSONResponse(
            status_code=202,
            content={"execution_id": "exec-fresh"},
            headers={"Location": "/api/v1/executions/exec-fresh"},
        )

    monkeypatch.setattr(capabilities, "admit_dispatch", _admit)
    client = _client(_ready_proxy(registry), monkeypatch)
    canonical = "/api/v1/capabilities/demo/fresh-pipe"
    response = client.post(canonical, json={"model": "fresh-pipe", "messages": []})
    assert response.status_code == 202, response.text
    assert response.json()["execution_id"] == "exec-fresh"
    assert 'rel="canonical"' in response.headers["link"]
    assert canonical in response.headers["link"]


def test_cdp_executions_path_is_not_pipeline_executions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "pipelines"
    _write_root(root)

    async def _relay(method: str, path: str, **_kwargs: Any) -> tuple[int, dict, str]:
        assert method == "GET"
        assert path == "/v1/project-ask/executions/cdp-1"
        return 200, {"execution_id": "cdp-1", "source": "cdp"}, "application/json"

    monkeypatch.setattr(providers_cdp, "relay_async", _relay)
    client = _client(_ready_proxy(_registry(root)), monkeypatch)
    cdp = client.get("/api/v1/providers/cdp/executions/cdp-1")
    assert cdp.status_code == 200, cdp.text
    assert cdp.json()["source"] == "cdp"
    pipeline = client.get("/api/v1/executions/cdp-1")
    assert pipeline.status_code == 404
    assert pipeline.json()["error"]["code"] == "execution_id_expired_or_unknown"


def test_category_filter_unchanged_until_reload_after_name_delete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Deleting a vocabulary name on disk does not drop members before reload."""
    root = tmp_path / "pipelines"
    _write_root(root)
    registry = _registry(root)
    client = _client(_ready_proxy(registry), monkeypatch)
    before = client.get("/api/v1/capabilities", params={"category": "demo"})
    assert before.status_code == 200
    assert "demo-pipe" in before.json()["members"]
    (root / "categories.yaml").write_text(
        "categories:\n  other:\n    description: x\n",
        encoding="utf-8",
    )
    after = client.get("/api/v1/capabilities", params={"category": "demo"})
    assert after.status_code == 200
    assert after.json()["members"] == before.json()["members"]


def test_restored_registry_category_matches_rebuild(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(
        "STARGATE_PIPELINE_REGISTRY_SNAPSHOT_DIR",
        str(tmp_path / "snap"),
    )
    root = tmp_path / "pipelines"
    _write_root(root)
    first = _registry(root)
    first.reload_pipelines()
    client = _client(_ready_proxy(first), monkeypatch)
    listed = client.get("/api/v1/capabilities", params={"category": "demo"})
    assert listed.status_code == 200
    restored = PipelineRegistry(
        search_paths=[str(root)],
        config_base_dir=root.parent,
    )
    restored.load()
    restored.reload_pipelines()
    again = _client(_ready_proxy(restored), monkeypatch)
    rebuilt = again.get("/api/v1/capabilities", params={"category": "demo"})
    assert rebuilt.status_code == 200
    assert rebuilt.json()["members"] == listed.json()["members"]
