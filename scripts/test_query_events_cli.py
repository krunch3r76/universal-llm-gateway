"""query-events forwards unknown flags to the capability URL."""

from __future__ import annotations

import importlib.util
import json
import sys
from importlib.machinery import SourceFileLoader
from pathlib import Path
from typing import Any

import httpx
import pytest

_CLI_PATH = Path(__file__).resolve().parent / "query-events"


def _load_cli() -> Any:
    loader = SourceFileLoader("query_events_cli_ac5", str(_CLI_PATH))
    spec = importlib.util.spec_from_loader("query_events_cli_ac5", loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def _install(monkeypatch: pytest.MonkeyPatch, handler) -> list[httpx.Request]:  # type: ignore[no-untyped-def]
    seen: list[httpx.Request] = []

    def transport_handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    transport = httpx.MockTransport(transport_handler)

    def factory(*_a: object, **_k: object) -> httpx.Client:
        return httpx.Client(transport=transport, base_url="http://localhost")

    monkeypatch.setattr("transport_utils.make_sync_client", factory)
    return seen


def test_forwards_unknown_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    cli = _load_cli()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"operation": request.url.path.rsplit("/", 1)[-1]})

    seen = _install(monkeypatch, handler)
    monkeypatch.setattr(
        sys,
        "argv",
        ["query-events", "--op", "pipeline-trace", "--execution-id", "c4c7448d"],
    )
    cli.main()
    assert seen[0].url.path == "/api/v1/capabilities/observability/pipeline-trace"
    assert seen[0].url.params["execution_id"] == "c4c7448d"

    monkeypatch.setattr(
        sys, "argv", ["query-events", "--op", "noise-profile", "--minutes", "5"]
    )
    cli.main()
    assert seen[-1].url.params["minutes"] == "5"

    monkeypatch.setattr(
        sys, "argv", ["query-events", "--op", "recent-failures", "--limit", "10"]
    )
    cli.main()
    assert seen[-1].url.params["limit"] == "10"

    monkeypatch.setattr(sys, "argv", ["query-events", "--op", "recent-failures"])
    cli.main()
    assert "limit" not in seen[-1].url.params


def test_sql_socket_and_failures(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    cli = _load_cli()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/sql"):
            body = json.loads(request.content)
            assert body["limit"] == 100
            assert request.url.path == "/api/v1/observability/sql"
            return httpx.Response(200, json={"rows": [], "count": 0})
        if "lock" in request.url.path:
            return httpx.Response(
                503,
                json={
                    "code": "LOCK_WAIT",
                    "message": "Event store waited on a database lock.",
                    "data": {"error_class": "lock_wait"},
                    "source": "rpc",
                    "retryable": True,
                },
            )
        raise httpx.ReadTimeout("read timed out", request=request)

    _install(monkeypatch, handler)
    monkeypatch.setattr(
        sys,
        "argv",
        ["query-events", "--sql", "SELECT 1", "--socket", "/tmp/x.sock"],
    )
    cli.main()
    out = capsys.readouterr().out
    assert "rows" in out

    monkeypatch.setattr(sys, "argv", ["query-events", "--op", "lock"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 1
    err = json.loads(capsys.readouterr().err)
    assert err["error_class"] == "lock_wait"

    monkeypatch.setattr(sys, "argv", ["query-events", "--op", "slow"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 1
    assert json.loads(capsys.readouterr().err)["error_class"] == "client_deadline"

    monkeypatch.setattr(sys, "argv", ["query-events", "--op", "x", "stray"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
