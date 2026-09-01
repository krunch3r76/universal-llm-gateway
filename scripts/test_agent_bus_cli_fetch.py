"""Regression: agent-bus CLI fetch windowing + read-state (F1/F2/F3).

The CLI is a standalone script (no .py extension, not on PYTHONPATH), so it is
loaded by path via importlib. Tests mock the UDS transport (`_request`) and
assert the windowing/mark-read contract without a live agent-bus service.
"""

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


def _fetch_args(**overrides: Any) -> argparse.Namespace:
    base = {
        "to": None,
        "thread": None,
        "last": 5,
        "unread": False,
        "mark_read": False,
        "compact": False,
        "context": 0,
    }
    base.update(overrides)
    return argparse.Namespace(**base)


def test_phase2_marks_only_displayed_turns_and_keeps_newest(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """F1+F3: probe is the OLDEST turn (dropped), newest is displayed, and
    mark-read touches only the surviving displayed turns — never the probe."""
    cli = _load_cli()

    # Newest-first (turn_number DESC), as the server orders them.
    # 10/9/8 unread, 7/6 read context, 5 the oldest probe (unread).
    phase2_turns = [
        {"id": tn, "turn_number": tn, "read_at": None if tn in (10, 9, 8, 5) else "t"}
        for tn in (10, 9, 8, 7, 6, 5)
    ]
    patched: list[int] = []
    get_paths: list[str] = []

    def fake_request(method: str, path: str, token: str, body=None):
        if method == "PATCH":
            patched.append(int(path.split("/")[2]))
            return {"status": "ok", "read_at": "marked"}
        if path.startswith("/threads/") and "?" not in path:
            return {"id": "1138", "slug": "example-arc"}
        get_paths.append(path)
        qs = parse_qs(urlparse(path).query)
        if qs.get("unread") == ["true"]:  # phase-1 peek
            return {"turns": [{"id": i, "turn_number": i} for i in (10, 9, 8)]}
        return {"turns": phase2_turns}  # phase-2 fetch

    cli._request = fake_request
    cli._cmd_fetch(_fetch_args(thread="1138", context=2, mark_read=True), token="tok")

    out = json.loads(capsys.readouterr().out)
    shown = {t["turn_number"] for t in out["turns"]}

    # fetch_last = context(2) + max(unread=3,1) + 1 = 6; probe (oldest=5) trimmed.
    assert 10 in shown, "newest turn must be displayed"
    assert 5 not in shown, "oldest probe must be trimmed from display"
    assert shown == {10, 9, 8, 7, 6}

    # mark-read scoped to displayed unread turns (10/9/8), never the probe (5).
    assert sorted(patched) == [8, 9, 10]
    assert 5 not in patched

    # Phase-2 GET must NOT carry mark_read — marking is done per displayed turn.
    phase2 = [p for p in get_paths if "unread=true" not in p]
    assert phase2 and all("mark_read" not in p for p in phase2)
    assert out["_thread_info"]["id"] == "1138"
    assert out["_thread_info"]["slug"] == "example-arc"


def test_unread_forwarded_without_to(capsys: pytest.CaptureFixture[str]) -> None:
    """F2: `fetch --thread X --unread` (no --to) must forward unread=true."""
    cli = _load_cli()
    get_paths: list[str] = []

    def fake_request(method: str, path: str, token: str, body=None):
        get_paths.append(path)
        if path.startswith("/threads/") and "?" not in path:
            return {"id": "049", "slug": "example-arc"}
        return {"turns": []}

    cli._request = fake_request
    cli._cmd_fetch(_fetch_args(thread="049", unread=True), token="tok")
    capsys.readouterr()

    # First GET is the main fetch; a second GET is the F8 unread_count peek.
    qs = parse_qs(urlparse(get_paths[0]).query)
    assert qs.get("unread") == ["true"]
    assert "to" not in qs


def test_has_earlier_false_at_exact_n_boundary() -> None:
    """F6: a thread with exactly `window` turns, oldest == turn 1, has no earlier."""
    cli = _load_cli()
    full_window = [{"id": tn, "turn_number": tn, "read_at": "t"} for tn in (3, 2, 1)]
    assert cli._has_earlier_turns(full_window, 3) is False  # oldest is turn 1
    # Same length but oldest is turn 2 → genuinely more behind it.
    shifted = [{"id": tn, "turn_number": tn, "read_at": "t"} for tn in (4, 3, 2)]
    assert cli._has_earlier_turns(shifted, 3) is True
    assert cli._has_earlier_turns([], 3) is False
