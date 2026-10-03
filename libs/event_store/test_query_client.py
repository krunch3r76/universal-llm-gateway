"""Path building and error mapping for the observability client."""

from __future__ import annotations

import httpx

from event_store.query_client import list_members, query_member, query_sql


def _factory(transport: httpx.MockTransport):
    def make(url: str = "", timeout: float = 10.0, follow_redirects: bool = False):
        del url, timeout, follow_redirects
        return httpx.Client(transport=transport, base_url="http://localhost")

    return make


def test_paths(monkeypatch) -> None:
    seen: list[tuple[str, str, dict[str, str]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, dict(request.url.params)))
        if request.method == "POST":
            return httpx.Response(200, json={"rows": [], "count": 0})
        if request.url.path.endswith("observability"):
            return httpx.Response(200, json={"members": []})
        return httpx.Response(200, json={"operation": "pipeline-trace"})

    monkeypatch.setattr(
        "event_store.query_client.make_sync_client",
        _factory(httpx.MockTransport(handler)),
    )
    assert query_member("pipeline-trace", {"execution_id": "x"})["operation"] == (
        "pipeline-trace"
    )
    query_sql("SELECT 1")
    list_members()
    assert seen[0][0:2] == ("GET", "/api/v1/observability/pipeline-trace")
    assert seen[0][2]["execution_id"] == "x"
    assert seen[1][0:2] == ("POST", "/api/v1/observability/sql")
    assert seen[2] == ("GET", "/api/v1/observability", {})


def test_lock_wait_and_deadline_and_non_scalar(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("busy"):
            return httpx.Response(
                503,
                json={
                    "code": "LOCK_WAIT",
                    "message": "Event store waited on a database lock.",
                    "source": "rpc",
                    "retryable": True,
                    "data": {"error_class": "lock_wait"},
                },
            )
        raise httpx.ReadTimeout("read timed out", request=request)

    monkeypatch.setattr(
        "event_store.query_client.make_sync_client",
        _factory(httpx.MockTransport(handler)),
    )
    busy = query_member("busy")
    assert busy["error_class"] == "lock_wait"
    deadline = query_member("slow")
    assert deadline["error_class"] == "client_deadline"
    calls = {"n": 0}

    def counting(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={})

    monkeypatch.setattr(
        "event_store.query_client.make_sync_client",
        _factory(httpx.MockTransport(counting)),
    )
    rejected = query_member("pipeline-trace", {"execution_id": {"nested": 1}})
    assert rejected["code"] == "INVALID_PARAMS"
    assert calls["n"] == 0
