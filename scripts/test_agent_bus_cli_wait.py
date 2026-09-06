"""Regression: agent-bus CLI wait (UDS long-poll wrapper)."""

from __future__ import annotations

import argparse
import importlib.util
import json
from importlib.machinery import SourceFileLoader
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest

_CLI_PATH = Path(__file__).resolve().parent / "agent-bus"


def _load_cli() -> Any:
    loader = SourceFileLoader("agent_bus_cli", str(_CLI_PATH))
    spec = importlib.util.spec_from_loader("agent_bus_cli", loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def _wait_args(**overrides: Any) -> argparse.Namespace:
    base = {
        "thread": "9977",
        "after_turn": 1,
        "wait_seconds": 60.0,
        "completion": "first_reply_from",
        "from_agent": "cursor-sdk",
        "until_complete": False,
        "compact": False,
    }
    base.update(overrides)
    return argparse.Namespace(**base)


def test_wait_once_builds_query_and_returns_snapshot(
    capsys: pytest.CaptureFixture[str],
) -> None:
    cli = _load_cli()
    seen: list[tuple[str, float]] = []

    def fake_request(method: str, path: str, token: str, body=None, *, timeout=10.0):
        assert method == "GET"
        assert token == "tok"
        seen.append((path, timeout))
        qs = parse_qs(urlparse(path).query)
        assert qs["after_turn"] == ["3"]
        assert qs["wait"] == ["30.0"]
        assert qs["completion"] == ["first_reply_from"]
        assert qs["from_agent"] == ["cursor-sdk"]
        return {"complete": False, "turns": [], "next_poll_after_s": 5}

    cli._request = fake_request
    snap = cli._wait_once(
        thread="9977",
        after_turn=3,
        wait_seconds=30.0,
        completion="first_reply_from",
        from_agent="cursor-sdk",
        token="tok",
    )
    assert snap["complete"] is False
    assert seen[0][0].startswith("/threads/9977/wait?")
    assert seen[0][1] == pytest.approx(45.0)


def test_cmd_wait_single_call_when_not_until_complete(
    capsys: pytest.CaptureFixture[str],
) -> None:
    cli = _load_cli()
    calls = 0

    def fake_wait_once(**kwargs):
        nonlocal calls
        calls += 1
        return {"complete": False, "turns": [{"id": 9}]}

    cli._wait_once = fake_wait_once
    cli._cmd_wait(_wait_args(until_complete=False), token="tok")
    out = json.loads(capsys.readouterr().out)
    assert calls == 1
    assert out["complete"] is False


def test_cmd_wait_loops_until_complete(capsys: pytest.CaptureFixture[str]) -> None:
    cli = _load_cli()
    responses = [
        {"complete": False, "turns": []},
        {"complete": True, "turns": [{"id": 10, "from_agent": "cursor-sdk"}]},
    ]

    def fake_wait_once(**kwargs):
        return responses.pop(0)

    cli._wait_once = fake_wait_once
    cli._cmd_wait(_wait_args(until_complete=True), token="tok")
    out = json.loads(capsys.readouterr().out)
    assert out["complete"] is True
    assert len(responses) == 0


def test_wait_once_requires_from_agent_for_first_reply_from(
    capsys: pytest.CaptureFixture[str],
) -> None:
    cli = _load_cli()
    with pytest.raises(SystemExit) as exc:
        cli._wait_once(
            thread="1",
            after_turn=1,
            wait_seconds=0,
            completion="first_reply_from",
            from_agent=None,
            token="tok",
        )
    assert exc.value.code == 1
    assert "from-agent" in capsys.readouterr().err.lower()
