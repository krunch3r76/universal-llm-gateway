"""item-1 — fetch header default, projection modes, get turn_numbers."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import pytest
from fastmcp import FastMCP

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import agent_bus as agent_bus_module  # noqa: E402
from tools._agent_bus_read import register_agent_bus_read_tool  # noqa: E402
from tools.agent_bus._closeout_projection import (  # noqa: E402
    _HEADER_FIELDS,
    project_turn_body,
    project_turns_header,
)
from tools.agent_bus.fetch import (  # noqa: E402
    _fetch_dispatch,
    _fetch_unread_dispatch,
    _get_dispatch,
)


def _sample_turn(
    *,
    turn_number: int,
    body: str = "body",
    body_chars: int | None = None,
    supersedes_turn: int | None = None,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": f"id-{turn_number}",
        "thread": "1",
        "turn_number": turn_number,
        "from": "cursor",
        "to": "web-anthropic",
        "subject": f"subject-{turn_number}",
        "status": "open",
        "created_at": "2026-01-01T00:00:00Z",
        "read_at": None,
        "body": body if body_chars is None else None,
    }
    if body_chars is not None:
        row["body_chars"] = body_chars
    if supersedes_turn is not None:
        row["supersedes_turn"] = supersedes_turn
    return row


def test_header_includes_from_agent_id_only_when_set() -> None:
    tab = "21060be7-f0a6-4fa1-9058-b117ce4fc526"
    raw_turns = [
        _sample_turn(turn_number=1, body_chars=3),
        _sample_turn(turn_number=2, body_chars=4),
    ]
    raw_turns[1]["from_agent_id"] = tab
    rows = project_turns_header(raw_turns)
    assert "from_agent_id" not in rows[0]
    assert rows[1]["from_agent_id"] == tab


def test_fetch_default_is_header() -> None:
    captured: dict[str, str] = {}
    raw_turns = [
        _sample_turn(turn_number=3, body_chars=9),
        _sample_turn(turn_number=2, body_chars=5),
    ]

    def relay(service: str, method: str, path: str, **kwargs) -> dict[str, Any]:
        del service, method, kwargs
        captured.update({k: v[0] for k, v in parse_qs(urlparse(path).query).items()})
        return {"turns": raw_turns}

    with patch("tools.agent_bus.fetch.relay", side_effect=relay):
        out = _fetch_dispatch(thread="T")

    assert captured.get("compact") == "true"
    assert out["selection"] == "headers"
    assert out["projection"] == "header"
    for turn in out["turns"]:
        assert "body" not in turn
        allowed = set(_HEADER_FIELDS) | {"supersedes_turn"}
        assert set(turn.keys()) <= allowed
        assert turn.get("from") == "cursor"
        assert turn.get("to") == "web-anthropic"


def test_fetch_projection_full_passthrough() -> None:
    relay_payload = {
        "turns": [_sample_turn(turn_number=1, body="full-body")],
        "extra": "keep",
    }

    with patch("tools.agent_bus.fetch.relay", return_value=relay_payload):
        out = _fetch_dispatch(thread="T", projection="full")

    assert out == relay_payload


def test_fetch_projection_compact_envelope() -> None:
    raw = _sample_turn(turn_number=1, body="x" * 2000)
    relay_payload = {"turns": [raw]}

    with patch("tools.agent_bus.fetch.relay", return_value=relay_payload):
        out = _fetch_dispatch(thread="T", projection="compact")

    expected = project_turn_body(raw, projection="compact")
    assert out["turns"][0] == expected


def test_fetch_compact_alias_and_conflict() -> None:
    raw = [_sample_turn(turn_number=1, body_chars=3)]

    with patch("tools.agent_bus.fetch.relay", return_value={"turns": raw}):
        header = _fetch_dispatch(thread="T", compact=True)
    assert header["projection"] == "header"

    full_turn = _sample_turn(turn_number=1, body="payload")
    with patch("tools.agent_bus.fetch.relay", return_value={"turns": [full_turn]}):
        full = _fetch_dispatch(thread="T", compact=False)
    assert full == {"turns": [full_turn]}

    conflict = _fetch_dispatch(thread="T", compact=True, projection="full")
    assert conflict.get("reason") == "fetch_projection_conflict"
    conflict2 = _fetch_dispatch(thread="T", compact=False, projection="header")
    assert conflict2.get("reason") == "fetch_projection_conflict"


def test_fetch_mark_read_defaults_full() -> None:
    captured: dict[str, str] = {}

    def relay(service: str, method: str, path: str, **kwargs) -> dict[str, Any]:
        del service, method, kwargs
        captured.update({k: v[0] for k, v in parse_qs(urlparse(path).query).items()})
        return {"turns": []}

    with patch("tools.agent_bus.fetch.relay", side_effect=relay):
        _fetch_dispatch(thread="T", mark_read=True)
    assert "compact" not in captured

    with patch("tools.agent_bus.fetch.relay", return_value={"turns": []}):
        header = _fetch_dispatch(thread="T", mark_read=True, projection="header")
    assert header["projection"] == "header"


def test_get_turn_numbers_picks() -> None:
    t3 = _sample_turn(turn_number=3, body="three")
    t5 = _sample_turn(turn_number=5, body="five")

    def relay(service: str, method: str, path: str, **kwargs) -> dict[str, Any]:
        del service, method, kwargs
        qs = parse_qs(urlparse(path).query)
        number = int(qs["turn_number"][0])
        if number == 5:
            return t5
        if number == 3:
            return t3
        return {"error": "missing"}

    with patch("tools.agent_bus.fetch.relay", side_effect=relay):
        out = _get_dispatch(thread="1", turn_numbers=[5, 3, 5])

    assert [t["turn_number"] for t in out["turns"]] == [5, 3]
    assert out["turns"][0] == project_turn_body(t5, projection=None)
    deduped = _get_dispatch(thread="1", turn_numbers=[5] * 11)
    assert len(deduped["turns"]) == 1
    over = _get_dispatch(thread="1", turn_numbers=list(range(1, 12)))
    assert over.get("reason") == "get_turn_numbers_over_cap"
    both = _get_dispatch(thread="1", turn_number=2, turn_numbers=[2])
    assert both.get("reason") == "get_turn_selector_conflict"
    bad_mix = _get_dispatch(thread="1", turn_numbers=[1, "x"])
    assert bad_mix.get("reason") == "get_turn_numbers_invalid"
    bad_bool = _get_dispatch(thread="1", turn_numbers=[True])
    assert bad_bool.get("reason") == "get_turn_numbers_invalid"


def test_get_turn_numbers_partial_failure() -> None:
    good = _sample_turn(turn_number=1, body="ok")

    def relay(service: str, method: str, path: str, **kwargs) -> dict[str, Any]:
        del service, method, kwargs
        qs = parse_qs(urlparse(path).query)
        number = int(qs["turn_number"][0])
        if number == 1:
            return good
        return {
            "error": "HTTP 404",
            "detail": "missing",
            "reason": "turn_not_found",
        }

    with patch("tools.agent_bus.fetch.relay", side_effect=relay):
        out = _get_dispatch(thread="1", turn_numbers=[1, 2])

    assert out["partial"] is True
    assert out["turns"][0]["turn_number"] == 1
    assert out["turns"][1]["turn_number"] == 2
    assert "error" in out["turns"][1]
    assert out["turns"][1].get("reason") is not None


def test_get_turn_numbers_pick_exception() -> None:
    call_count = 0

    def boom(*args: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise RuntimeError("pick blew up")
        return {"turn": _sample_turn(turn_number=1, body="ok")}

    with patch("tools.agent_bus.fetch._get_impl", side_effect=boom):
        out = _get_dispatch(thread="1", turn_numbers=[1, 2])

    assert out["partial"] is True
    assert len(out["turns"]) == 2


def test_header_tolerates_missing_body_chars() -> None:
    rows = project_turns_header([{"id": "x", "thread": "1", "turn_number": 1}])
    assert rows[0]["body_chars"] is None


def test_header_payload_not_sparse_marked() -> None:
    with patch(
        "tools.agent_bus.fetch.relay",
        return_value={"turns": [_sample_turn(turn_number=1, body_chars=1)]},
    ):
        out = _fetch_dispatch(thread="T")
    assert "sparse_policy" not in out


def test_fetch_header_relay_error_passthrough() -> None:
    err = {"error": "HTTP 503", "detail": "down"}
    with patch("tools.agent_bus.fetch.relay", return_value=err):
        out = _fetch_dispatch(thread="T")
    assert "error" in out
    assert "selection" not in out


def test_fetch_header_preserves_order() -> None:
    raw = [
        _sample_turn(turn_number=3, body_chars=1),
        _sample_turn(turn_number=2, body_chars=1),
        _sample_turn(turn_number=1, body_chars=1),
    ]
    with patch("tools.agent_bus.fetch.relay", return_value={"turns": raw}):
        out = _fetch_dispatch(thread="T")
    assert [t["turn_number"] for t in out["turns"]] == [3, 2, 1]


def test_fetch_records_projection() -> None:
    events: list[tuple[Any, ...]] = []

    def _record(signal: str, **fields: Any) -> None:
        events.append((signal, fields))

    with patch("tools.agent_bus.fetch.record", side_effect=_record):
        with patch("tools.agent_bus.fetch.relay", return_value={"turns": []}):
            _fetch_dispatch(thread="T", projection="compact")

    fetched = [e for e in events if e[0] == "mcp.agentbus.turns.fetched"]
    assert fetched and fetched[-1][1].get("mode") == "compact"


@pytest.fixture
def agent_bus_read_fn(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(agent_bus_module, "record", lambda *a, **k: None)
    mcp = FastMCP("test-agent-bus-read-header")
    register_agent_bus_read_tool(mcp)
    tools = asyncio.run(mcp.list_tools())
    return next(t for t in tools if t.name == "agent_bus_read").fn


def test_agent_bus_read_accepts_new_args(agent_bus_read_fn) -> None:
    with patch("tools.agent_bus.fetch.relay", return_value={"turns": []}):
        fetch_out = agent_bus_read_fn(
            tool="fetch",
            arguments=json.dumps({"thread": "1", "projection": "header"}),
        )
    assert "unsupported argument" not in str(fetch_out.get("error", ""))
    assert fetch_out.get("projection") == "header"

    def relay(service: str, method: str, path: str, **kwargs) -> dict[str, Any]:
        del service, method, kwargs
        return _sample_turn(turn_number=1, body="one")

    with patch("tools.agent_bus.fetch.relay", side_effect=relay):
        get_out = agent_bus_read_fn(
            tool="get",
            arguments=json.dumps({"thread": "1", "turn_numbers": [1, 2]}),
        )
    assert "unsupported argument" not in str(get_out.get("error", ""))
    assert len(get_out.get("turns", [])) == 2


def test_fetch_impl_label_truthful() -> None:
    import pytest as pt

    with pt.raises(TypeError):
        from tools.agent_bus.fetch import _fetch_impl

        _fetch_impl(
            to=None,
            thread="1",
            last=1,
            unread=False,
            mark_read=False,
            compact=False,
        )


def test_header_live_shape_via_turn_serializer() -> None:
    from agent_bus_store.routes.turns import _rows_to_turns
    from agent_bus_store.turns_models import TurnList

    db_row = {
        "id": 1,
        "thread": "T",
        "turn_number": 1,
        "from_agent": "a",
        "to_agent": "b",
        "subject": "s",
        "status": "open",
        "supersedes_turn": None,
        "created_at": "2026-01-01T00:00:00Z",
        "read_at": None,
        "body": None,
        "body_chars": 42,
        "attested_by": None,
        "user_agent": None,
    }
    wire = TurnList(turns=_rows_to_turns([db_row])).model_dump(mode="json")

    with patch("tools.agent_bus.fetch.relay", return_value=wire):
        out = _fetch_dispatch(thread="T")

    turn = out["turns"][0]
    assert turn["from"] == "a"
    assert turn["to"] == "b"
    assert turn["body_chars"] == 42


def test_header_keeps_envelope_keys() -> None:
    relay_payload = {
        "turns": [_sample_turn(turn_number=1, body_chars=3)],
        "truncated": True,
        "next_after_turn": 21,
    }
    with patch("tools.agent_bus.fetch.relay", return_value=relay_payload):
        out = _fetch_dispatch(thread="T")
    assert out["truncated"] is True
    assert out["next_after_turn"] == 21
    assert "body_hint" in out


def test_fetch_unread_unprojected() -> None:
    from tools.agent_bus.fetch import _fetch_unread_dispatch

    payload = {"turns": [_sample_turn(turn_number=1, body="x")]}
    with patch("tools.agent_bus.fetch.relay", return_value=payload):
        out = _fetch_unread_dispatch(thread="T")
    assert out == payload
    assert "selection" not in out


def test_fetch_unread_records_store_mode() -> None:
    events: list[tuple[Any, ...]] = []

    def _record(signal: str, **fields: Any) -> None:
        events.append((signal, fields))

    with patch("tools.agent_bus.fetch.record", side_effect=_record):
        with patch("tools.agent_bus.fetch.relay", return_value={"turns": []}):
            _fetch_unread_dispatch(thread="T", compact=True)
    fetched = [e for e in events if e[0] == "mcp.agentbus.turns.fetched"]
    assert fetched[-1][1].get("mode") == "store_compact"


def test_cursor_bridge_status_reads_bodies() -> None:
    from urllib.parse import parse_qs, urlparse

    from tools.cursor_bridge import _status

    captured: dict[str, str] = {}
    body = "preview-body-text"

    def relay(service: str, method: str, path: str, **kwargs) -> dict[str, Any]:
        del service, method, kwargs
        captured.update({k: v[0] for k, v in parse_qs(urlparse(path).query).items()})
        return {
            "turns": [
                {
                    "turn_number": 1,
                    "from": "a",
                    "to": "b",
                    "subject": "s",
                    "created_at": "2026-01-01T00:00:00Z",
                    "body": body,
                }
            ]
        }

    with patch("tools.agent_bus.fetch.relay", side_effect=relay):
        out = _status(thread="9", last=5)

    assert "compact" not in captured
    assert out["turns"][0]["body_preview"]


def test_prompt_sites_pick_turns() -> None:
    from tools._oc_knowledge_templates import AGENT_BUS_COMPACT
    from tools._oc_surface_templates import FRONTIER_MODEL_ROUTING
    from tools.frontier import __doc__ as frontier_doc

    combined = f"{FRONTIER_MODEL_ROUTING}\n{AGENT_BUS_COMPACT}\n{frontier_doc}"
    assert "turn_numbers" in combined
    assert "fetch (headers)" in combined or "Fetch inbox headers" in combined
