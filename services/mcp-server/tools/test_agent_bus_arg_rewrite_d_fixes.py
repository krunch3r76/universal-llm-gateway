"""Review fixes for agent_bus arg-rewrite slice D (agent-bus 15844#23)."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from fastmcp import FastMCP

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent_bus_store import create_app
from agent_bus_store.auth import require_token

from tools._agent_bus_read import register_agent_bus_read_tool  # noqa: E402
from tools.agent_bus import (  # noqa: E402
    _fetch_dispatch,
    _fetch_unread_dispatch,
    _get_dispatch,
    _get_impl,
)
from tools.agent_bus._arg_rewrite import reconcile_dispatch_arguments


@pytest.fixture
def agent_bus_read_fn(monkeypatch: pytest.MonkeyPatch):
    import tools.agent_bus as agent_bus_module

    monkeypatch.setattr(agent_bus_module, "record", lambda *a, **k: None)
    mcp = FastMCP("test-agent-bus-read-fixes")
    register_agent_bus_read_tool(mcp)
    tools = asyncio.run(mcp.list_tools())
    return next(t for t in tools if t.name == "agent_bus_read").fn


def _store_client(tmp_path: Path) -> TestClient:
    app = create_app(db_path=str(tmp_path / "bus.db"))
    app.dependency_overrides[require_token] = lambda: None
    return TestClient(app)


def test_send_idempotency_key_replay_one_turn(tmp_path) -> None:
    """Timeout retry with same key must not insert a second turn."""
    with _store_client(tmp_path) as client:
        payload = {
            "new_slug": "idem-send-slug",
            "from": "cursor",
            "to": "web-anthropic",
            "subject": "s",
            "body": "b",
            "idempotency_key": "idem-key-send-12345678",
        }
        first = client.post("/threads/send", json=payload)
        second = client.post("/threads/send", json=payload)
        assert first.status_code == 201, first.text
        assert second.status_code == 200, second.text
        assert second.json().get("idempotent_replay") is True
        thread_id = first.json()["thread"]["id"]
        turns = client.get("/turns", params={"thread": thread_id}).json()["turns"]
        assert len(turns) == 1


def test_get_mark_read_skips_wrong_recipient() -> None:
    calls: list[tuple[str, str]] = []

    def relay(service: str, method: str, path: str, **kwargs) -> dict:
        del kwargs
        calls.append((method, path))
        if method == "GET":
            return {
                "id": 99,
                "thread": "1",
                "turn_number": 2,
                "to_agent": "web-anthropic",
                "read_at": None,
            }
        return {"read_at": "2026-01-01T00:00:00+00:00"}

    with patch("tools.agent_bus.fetch.relay", side_effect=relay):
        result = _get_impl(
            thread="1",
            turn_number=2,
            mark_read=True,
            reader_agent="cursor",
        )

    assert "PATCH" not in [m for m, _ in calls]
    assert "argument_rewrite_advisory" in result
    assert result["turn"]["read_at"] is None


def test_get_mark_read_skips_broadcast() -> None:
    patch_calls: list[str] = []

    def relay(service: str, method: str, path: str, **kwargs) -> dict:
        del kwargs, service, path
        patch_calls.append(method)
        return {
            "id": 1,
            "to_agent": "all",
            "read_at": None,
        }

    with patch("tools.agent_bus.fetch.relay", side_effect=relay):
        result = _get_impl(
            thread="1",
            turn_number=1,
            mark_read=True,
            reader_agent="cursor",
        )

    assert "PATCH" not in patch_calls
    assert "argument_rewrite_advisory" in result


def test_get_mark_read_marks_when_addressed_to_caller() -> None:
    def relay(service: str, method: str, path: str, **kwargs) -> dict:
        del kwargs, service, path
        if method == "GET":
            return {"id": 5, "to_agent": "cursor", "read_at": None}
        return {"read_at": "2026-01-01T00:00:00+00:00"}

    with patch("tools.agent_bus.fetch.relay", side_effect=relay):
        result = _get_impl(
            thread="1",
            turn_number=1,
            mark_read=True,
            reader_agent="cursor",
        )

    assert result["turn"]["read_at"] is not None


def test_fetch_after_turn_keeps_last_bound() -> None:
    captured: dict[str, str] = {}

    def relay(service: str, method: str, path: str, **kwargs) -> dict:
        del service, kwargs
        qs = urlparse(path).query
        captured.update({k: v[0] for k, v in parse_qs(qs).items()})
        return {"turns": [{"turn_number": i} for i in range(1, 51)]}

    with patch("tools.agent_bus.fetch.relay", side_effect=relay):
        with patch(
            "tools.agent_bus.fetch.resolve_dispatch_from_agent",
            return_value=("", None),
        ):
            _fetch_dispatch(thread="9", after_turn=0, last=10)

    assert captured.get("last") == "10"
    assert captured.get("after_turn") == "0"


def test_fetch_after_turn_relay_bounded_page() -> None:
    """Store returns bounded list when last is forwarded."""
    captured: dict[str, str] = {}

    def relay(service: str, method: str, path: str, **kwargs) -> dict:
        del service, kwargs
        qs = urlparse(path).query
        captured.update({k: v[0] for k, v in parse_qs(qs).items()})
        last = int(captured.get("last", "50"))
        return {"turns": [{"turn_number": i} for i in range(1, last + 1)]}

    with patch("tools.agent_bus.fetch.relay", side_effect=relay):
        with patch(
            "tools.agent_bus.fetch.resolve_dispatch_from_agent",
            return_value=("", None),
        ):
            out = _fetch_dispatch(thread="9", after_turn=3, last=5)

    assert captured["last"] == "5"
    turns = out if isinstance(out, list) else out.get("turns", [])
    assert len(turns) == 5


def test_fetch_mark_read_passes_to_caller() -> None:
    captured: dict[str, str] = {}

    def relay(service: str, method: str, path: str, **kwargs) -> dict:
        del service, kwargs
        qs = urlparse(path).query
        captured.update({k: v[0] for k, v in parse_qs(qs).items()})
        return {"turns": []}

    with patch("tools.agent_bus.fetch.relay", side_effect=relay):
        with patch(
            "tools.agent_bus.fetch.resolve_dispatch_from_agent",
            return_value=("cursor", None),
        ):
            _fetch_dispatch(thread="1", mark_read=True, after_turn=0)

    assert "to" not in captured
    assert captured.get("mark_read_seat") == "cursor"
    assert captured.get("mark_read") == "true"


def test_fetch_after_turn_no_advisory() -> None:
    args, err, advisories = reconcile_dispatch_arguments(
        "fetch", {"thread": "1", "after_turn": 2}
    )
    assert err is None
    assert not any("after_turn is wired" in a for a in advisories)


def test_get_dispatch_marks_with_from_autofill() -> None:
    with patch(
        "tools.agent_bus.fetch.resolve_dispatch_from_agent",
        return_value=("cursor", None),
    ):
        with patch("tools.agent_bus.fetch._get_impl") as impl:
            impl.return_value = {"turn": {}}
            _get_dispatch(thread="1", turn_number=1, mark_read=True)
            impl.assert_called_once()
            assert impl.call_args.kwargs["reader_agent"] == "cursor"


def test_agent_bus_read_get_without_mark_read_no_author_required(
    agent_bus_read_fn,
) -> None:
    from request_profile import bind_request

    with bind_request("default", surface=None):
        with patch("tools.agent_bus.fetch._get_impl", return_value={"turn": {"id": 1}}):
            result = agent_bus_read_fn(
                tool="get",
                arguments='{"thread": "1", "turn_number": 1, "mark_read": false}',
            )
    assert "error" not in result
    assert result.get("reason") != "from_agent_required"


def test_fetch_mark_read_same_turn_list_as_without_mark_read(tmp_path) -> None:
    """List identity is checked against the store, not a relay that echoes one sample."""
    with _store_client(tmp_path) as client:
        send = client.post(
            "/threads/send",
            json={
                "new_slug": "mark-read-list-eq",
                "from": "web-anthropic",
                "to": "cursor",
                "subject": "s",
                "body": "b",
            },
        )
        assert send.status_code == 201, send.text
        thread_id = send.json()["thread"]["id"]
        second = client.post(
            "/threads/send",
            json={
                "thread": thread_id,
                "from": "web-anthropic",
                "to": "cursor",
                "subject": "s2",
                "body": "b2",
            },
        )
        assert second.status_code == 201, second.text
        plain = client.get("/turns", params={"thread": thread_id, "to": "cursor"})
        marked = client.get(
            "/turns",
            params={
                "thread": thread_id,
                "to": "cursor",
                "mark_read": "true",
                "mark_read_seat": "cursor",
            },
        )
        assert plain.status_code == 200
        assert marked.status_code == 200
        plain_turns = plain.json()["turns"]
        marked_turns = marked.json()["turns"]
        assert [t["id"] for t in plain_turns] == [t["id"] for t in marked_turns]
        assert len(plain_turns) == 2
        assert all(t["read_at"] is None for t in plain_turns)
        assert all(t["read_at"] is not None for t in marked_turns)
        persisted = client.get("/turns", params={"thread": thread_id, "to": "cursor"})
        assert [t["id"] for t in persisted.json()["turns"]] == [
            t["id"] for t in plain_turns
        ]
        assert all(t["read_at"] is not None for t in persisted.json()["turns"])


def test_after_turn_truncation_probe_not_marked_read(tmp_path) -> None:
    """The extra after_turn probe row is not returned and must stay unread."""
    with _store_client(tmp_path) as client:
        send = client.post(
            "/threads/send",
            json={
                "new_slug": "trunc-mark-probe",
                "from": "cursor",
                "to": "web-anthropic",
                "subject": "s1",
                "body": "b1",
            },
        )
        thread_id = send.json()["thread"]["id"]
        for i in range(2, 13):
            posted = client.post(
                "/threads/send",
                json={
                    "thread": thread_id,
                    "from": "cursor",
                    "to": "web-anthropic",
                    "subject": f"s{i}",
                    "body": f"b{i}",
                },
            )
            assert posted.status_code == 201, posted.text
        page = client.get(
            "/turns",
            params={
                "thread": thread_id,
                "after_turn": 0,
                "last": 10,
                "mark_read": "true",
                "to": "web-anthropic",
            },
        )
        data = page.json()
        assert data["truncated"] is True
        assert len(data["turns"]) == 10
        returned = {t["turn_number"] for t in data["turns"]}
        assert 11 not in returned
        assert all(t["read_at"] is not None for t in data["turns"])
        probe = client.get(
            "/turns/by-number",
            params={"thread": thread_id, "turn_number": 11},
        )
        assert probe.status_code == 200, probe.text
        assert probe.json()["read_at"] is None


def test_fetch_mark_read_omitted_from_agent_names_resolved_identity() -> None:
    def relay(service: str, method: str, path: str, **kwargs) -> dict:
        del service, method, path, kwargs
        return {"turns": []}

    with patch("tools.agent_bus.fetch.relay", side_effect=relay):
        with patch(
            "tools.agent_bus.fetch.resolve_dispatch_from_agent",
            return_value=("cursor", None),
        ):
            out = _fetch_dispatch(thread="1", mark_read=True)
    advisory = out["argument_rewrite_advisory"]
    assert "cursor" in advisory
    assert "resolve_dispatch_from_agent()" in advisory
    assert "from_agent omitted" in advisory


def test_get_mark_read_omitted_from_agent_names_resolved_identity() -> None:
    with patch(
        "tools.agent_bus.fetch.resolve_dispatch_from_agent",
        return_value=("cursor", None),
    ):
        with patch("tools.agent_bus.fetch._get_impl", return_value={"turn": {"id": 1}}):
            out = _get_dispatch(thread="1", turn_number=1, mark_read=True)
    advisory = out["argument_rewrite_advisory"]
    assert "cursor" in advisory
    assert "resolve_dispatch_from_agent()" in advisory
    assert "from_agent omitted" in advisory


def test_fetch_to_mismatch_mark_read_advisory_not_silent() -> None:
    captured: dict[str, str] = {}

    def relay(service: str, method: str, path: str, **kwargs) -> dict:
        del service, kwargs
        captured.update({k: v[0] for k, v in parse_qs(urlparse(path).query).items()})
        return {"turns": [{"id": 1, "turn_number": 1}]}

    with patch("tools.agent_bus.fetch.relay", side_effect=relay):
        with patch(
            "tools.agent_bus.fetch.resolve_dispatch_from_agent",
            return_value=("cursor", None),
        ):
            out = _fetch_dispatch(
                thread="1", to="web-anthropic", mark_read=True
            )
    assert captured.get("mark_read") != "true"
    assert "mark_read_seat" not in captured
    advisory = out["argument_rewrite_advisory"]
    assert "web-anthropic" in advisory
    assert "cursor" in advisory
    assert "refused" in advisory


def test_fetch_unread_thread_mark_read_passes_resolved_reader() -> None:
    captured: dict[str, str] = {}

    def relay(service: str, method: str, path: str, **kwargs) -> dict:
        del service, kwargs
        captured.update({k: v[0] for k, v in parse_qs(urlparse(path).query).items()})
        return {"turns": [{"id": 7, "to_agent": "cursor", "read_at": None}]}

    with patch("tools.agent_bus.fetch.relay", side_effect=relay):
        with patch(
            "tools.agent_bus.fetch.resolve_dispatch_from_agent",
            return_value=("cursor", None),
        ):
            out = _fetch_unread_dispatch(thread="9", mark_read=True)
    assert captured.get("thread") == "9"
    assert captured.get("unread") == "true"
    assert captured.get("mark_read") == "true"
    assert captured.get("mark_read_seat") == "cursor"
    assert "resolve_dispatch_from_agent()" in out["argument_rewrite_advisory"]


def test_fetch_thread_mark_read_marks_for_reader(tmp_path) -> None:
    with _store_client(tmp_path) as client:
        send = client.post(
            "/threads/send",
            json={
                "new_slug": "mark-read-thread",
                "from": "web-anthropic",
                "to": "cursor",
                "subject": "s",
                "body": "b",
            },
        )
        assert send.status_code == 201
        thread_id = send.json()["thread"]["id"]
        listed = client.get(
            "/turns",
            params={
                "thread": thread_id,
                "mark_read": "true",
                "mark_read_seat": "cursor",
            },
        )
        assert listed.status_code == 200
        turns = listed.json()["turns"]
        assert len(turns) == 1
        assert turns[0]["read_at"] is not None


def test_fetch_after_turn_truncation_flag_when_capped(tmp_path) -> None:
    with _store_client(tmp_path) as client:
        send = client.post(
            "/threads/send",
            json={
                "new_slug": "trunc-thread",
                "from": "cursor",
                "to": "web-anthropic",
                "subject": "s",
                "body": "b",
            },
        )
        thread_id = send.json()["thread"]["id"]
        for i in range(2, 13):
            client.post(
                "/threads/send",
                json={
                    "thread": thread_id,
                    "from": "cursor",
                    "to": "web-anthropic",
                    "subject": f"s{i}",
                    "body": f"b{i}",
                },
            )
        page = client.get(
            "/turns",
            params={"thread": thread_id, "after_turn": 0, "last": 10},
        )
        data = page.json()
        assert data["truncated"] is True
        assert data["next_after_turn"] == 10
        assert len(data["turns"]) == 10
