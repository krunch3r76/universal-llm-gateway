"""Acceptance tests for query-events CLI timeout classes (spec S4b item 8)."""

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
    loader = SourceFileLoader("query_events_cli", str(_CLI_PATH))
    spec = importlib.util.spec_from_loader("query_events_cli", loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def test_ac8_sqlite_busy_prints_json_and_exits_nonzero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cli = _load_cli()
    busy = {"error": "database is locked", "error_code": "sqlite_busy"}
    monkeypatch.setattr(cli, "_query", lambda *_a, **_k: busy)
    monkeypatch.setattr(sys, "argv", ["query-events", "--op", "operations"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 1
    err = capsys.readouterr().err
    parsed = json.loads(err.strip())
    assert parsed["error_code"] == "sqlite_busy"


def test_ac8_client_deadline_prints_json_and_exits_nonzero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cli = _load_cli()
    deadline = {
        "error": "Event query timed out before the server responded.",
        "error_code": "client_deadline",
    }
    monkeypatch.setattr(cli, "_query", lambda *_a, **_k: deadline)
    monkeypatch.setattr(sys, "argv", ["query-events", "--op", "stack-last-started"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 1
    err = capsys.readouterr().err
    parsed = json.loads(err.strip())
    assert parsed["error_code"] == "client_deadline"


def test_ac8_other_http_error_prints_server_json_body(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cli = _load_cli()
    request = httpx.Request("POST", "http://localhost/v1/query")
    response = httpx.Response(
        400,
        json={"error": "SQL error: no such column: ts"},
        request=request,
    )
    exc = httpx.HTTPStatusError("bad", request=request, response=response)

    def _raise(*_a, **_k):  # type: ignore[no-untyped-def]
        raise exc

    monkeypatch.setattr(cli, "_query", _raise)
    monkeypatch.setattr(sys, "argv", ["query-events", "--sql", "SELECT ts FROM events"])
    with pytest.raises(SystemExit) as exc_info:
        cli.main()
    assert exc_info.value.code == 1
    err = capsys.readouterr().err
    parsed = json.loads(err.strip())
    assert "no such column" in parsed["error"].lower()


def test_ac8_successful_query_prints_stdout(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cli = _load_cli()
    monkeypatch.setattr(
        cli,
        "_query",
        lambda *_a, **_k: {"type": "operations", "operations": []},
    )
    monkeypatch.setattr(sys, "argv", ["query-events", "--op", "operations"])
    cli.main()
    out = capsys.readouterr().out
    assert "operations" in out
