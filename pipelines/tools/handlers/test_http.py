"""http_v1 allowlist, first_present leaves, and the ingest composite await."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import yaml

from pipelines.tools.handlers.http import HttpHandler


class _Step:
    def __init__(self, data: dict) -> None:
        self.id = data.get("name", "step")
        self._data = data

    def get_domain_field(self, field: str, default=None):  # type: ignore[no-untyped-def]
        return self._data.get(field, default)


class _Client:
    def __init__(self, responses: list[httpx.Response]) -> None:
        self.responses = responses

    async def __aenter__(self) -> _Client:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    async def request(self, *_args: object, **_kwargs: object) -> httpx.Response:
        return self.responses.pop(0)

    async def get(self, *_args: object, **_kwargs: object) -> httpx.Response:
        return self.responses.pop(0)


def _load(name: str) -> dict:
    path = Path(f"pipelines/{name}/v1/{name.replace('_', '-')}-v1.yaml")
    if name == "ingest_article":
        path = Path("pipelines/ingest_article/v1/ingest-article-v1.yaml")
    if name == "gate1_audit":
        path = Path("pipelines/gate1_audit/v1/gate1-audit-v1.yaml")
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@pytest.mark.offline
def test_composites_expressible(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOBS_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("JOBS_TOKEN", "token")
    handler = HttpHandler()
    for name in ("ingest_article", "gate1_audit"):
        doc = _load(name)
        assert doc["schema_version"] == 6
        for step in doc["steps"]:
            errors = handler.validate(_Step(step))
            assert errors == [], (name, step["name"], errors)


@pytest.mark.offline
def test_not_allowlisted(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOBS_STATE_DIR", str(tmp_path))
    errors = HttpHandler().validate(
        _Step({"service": "rag", "operation_id": "orphaned_articles"})
    )
    assert any(item.startswith("http_v1_operation_not_allowlisted") for item in errors)


@pytest.mark.offline
def test_ingest_composite_binds_result(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JOBS_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("JOBS_TOKEN", "token")
    location = "/api/v1/jobs/article-fetch/runs/run-1"
    responses = [
        httpx.Response(202, headers={"location": location}, json={"run_id": "run-1"}),
        httpx.Response(
            200,
            json={
                "status": "completed",
                "result": {"source_path": "/papers/a.pdf", "filename": "a.pdf", "sha256": "abc"},
            },
        ),
    ]

    def factory(*_args: object, **_kwargs: object) -> _Client:
        return _Client(responses)

    monkeypatch.setattr("pipelines.tools.handlers.http.make_async_client", factory)
    doc = _load("ingest_article")
    context = SimpleNamespace(
        options={"arxiv": "2401.00001"},
        http_request=SimpleNamespace(headers={"x-ulg-surface": "code"}),
        get_output=lambda _step: None,
    )
    output = asyncio.run(HttpHandler().execute(_Step(doc["steps"][0]), context))
    assert output.json["result"]["source_path"] == "/papers/a.pdf"
