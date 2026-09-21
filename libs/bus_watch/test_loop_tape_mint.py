"""Tests for ``ensure_loop_tape`` birth mint and lineage reuse."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

from bus_watch.loop_tape_mint import ensure_loop_tape


def _state(**policy: object) -> dict:
    return {"born_at": "2026-09-21T08:00:00Z", "policy": dict(policy)}


def _bus_ctx(client: MagicMock) -> patch:
    return patch(
        "bus_watch.loop_tape_mint._bus",
        return_value=MagicMock(__enter__=MagicMock(return_value=client), __exit__=MagicMock()),
    )


@pytest.mark.offline
def test_mint_creates_hop_child_binds_policy(tmp_path: Path) -> None:
    state = _state()
    path = tmp_path / "liaison-11960.tick.json"
    client = MagicMock()

    def fake_get(_client: MagicMock, path_str: str, **_kw: object) -> dict:
        if path_str == "/threads/11960/lineage":
            return {"children": []}
        return {"_error": "unexpected"}

    resp = MagicMock()
    resp.status_code = 201
    resp.json.return_value = {"thread": {"id": "12022"}}
    client.post.return_value = resp

    with _bus_ctx(client), patch("bus_watch.loop_tape_mint._get", side_effect=fake_get):
        child = ensure_loop_tape("11960", state, path)

    assert child == "12022"
    saved = json.loads(path.read_text())
    assert saved["policy"]["loop_thread"] == "12022"
    payload = client.post.call_args.kwargs["json"]
    assert payload["new_slug"] == "11960-loop-tape"


@pytest.mark.offline
def test_idempotent_when_set_zero_bus_calls(tmp_path: Path) -> None:
    state = _state(loop_thread="12022")
    path = tmp_path / "s.json"
    with patch("bus_watch.loop_tape_mint._bus") as mock_bus:
        child = ensure_loop_tape("11960", state, path)
        mock_bus.assert_not_called()
    assert child == "12022"
    assert not path.is_file()


@pytest.mark.offline
def test_lineage_reuse_on_slug_match(tmp_path: Path) -> None:
    state = _state()
    path = tmp_path / "s.json"
    client = MagicMock()

    def fake_get(_client: MagicMock, path_str: str, **_kw: object) -> dict:
        if path_str == "/threads/11960/lineage":
            return {"children": [{"thread_id": "12022", "lane_role": "hop"}]}
        if path_str == "/threads/12022":
            return {"id": "12022", "slug": "11960-loop-tape"}
        return {"_error": "unexpected"}

    with _bus_ctx(client), patch("bus_watch.loop_tape_mint._get", side_effect=fake_get):
        child = ensure_loop_tape("11960", state, path)

    assert child == "12022"
    client.post.assert_not_called()
    assert json.loads(path.read_text())["policy"]["loop_thread"] == "12022"


@pytest.mark.offline
def test_hop_child_wrong_slug_not_reused(tmp_path: Path) -> None:
    state = _state()
    path = tmp_path / "s.json"
    client = MagicMock()

    def fake_get(_client: MagicMock, path_str: str, **_kw: object) -> dict:
        if path_str == "/threads/11960/lineage":
            return {"children": [{"thread_id": "99999", "lane_role": "hop"}]}
        if path_str == "/threads/99999":
            return {"id": "99999", "slug": "11960-conductor-mailbox"}
        return {"_error": "unexpected"}

    resp = MagicMock()
    resp.status_code = 201
    resp.json.return_value = {"thread": {"id": "12022"}}
    client.post.return_value = resp

    with _bus_ctx(client), patch("bus_watch.loop_tape_mint._get", side_effect=fake_get):
        child = ensure_loop_tape("11960", state, path)

    assert child == "12022"
    assert client.post.call_args.kwargs["json"]["new_slug"] == "11960-loop-tape"


@pytest.mark.offline
def test_mint_wire_payload(tmp_path: Path) -> None:
    state = _state()
    path = tmp_path / "s.json"
    client = MagicMock()

    def fake_get(_client: MagicMock, path_str: str, **_kw: object) -> dict:
        if path_str.endswith("/lineage"):
            return {"children": []}
        return {"_error": "unexpected"}

    resp = MagicMock()
    resp.status_code = 201
    resp.json.return_value = {"thread": {"id": "12022"}}
    client.post.return_value = resp

    with _bus_ctx(client), patch("bus_watch.loop_tape_mint._get", side_effect=fake_get):
        ensure_loop_tape("11960", state, path)

    payload = client.post.call_args.kwargs["json"]
    assert payload["new_slug"] == "11960-loop-tape"
    assert payload["parent_thread"] == "11960"
    assert payload["lane_role"] == "hop"
    assert "LOOP-TAPE 11960" in payload["body"]


@pytest.mark.offline
def test_mint_failure_returns_none_logs_no_raise(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    state = _state()
    path = tmp_path / "s.json"
    client = MagicMock()

    def fake_get(_client: MagicMock, path_str: str, **_kw: object) -> dict:
        if path_str.endswith("/lineage"):
            return {"children": []}
        return {"_error": "unexpected"}

    client.post.side_effect = httpx.HTTPError("down")

    with _bus_ctx(client), patch("bus_watch.loop_tape_mint._get", side_effect=fake_get):
        child = ensure_loop_tape("11960", state, path)

    assert child is None
    assert not path.is_file()
    captured = capsys.readouterr()
    assert '"loop": "loop_tape_mint_failed"' in captured.out
