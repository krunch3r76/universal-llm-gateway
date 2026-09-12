"""Tests for digest publish projection and bus post."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import httpx

from bus_watch.digest_publish import (
    project_digest,
    publish_digest,
    publish_if_enabled,
    render_body,
)
from bus_watch.liaison_digest import effective_policy


def _full_digest(**overrides: object) -> dict:
    base = {
        "ts": "2026-09-11T12:00:00Z",
        "root": {
            "id": "10479",
            "slug": "liaison-root",
            "turns": 10,
            "unread": 1,
            "last_subject": "CHECKPOINT",
            "error": None,
        },
        "register": "autonomous",
        "lanes": [
            {
                "id": "1",
                "unread": 0,
                "terminal": True,
                "last_subject": "CLOSEOUT",
            },
            {
                "id": "2",
                "unread": 2,
                "terminal": False,
                "last_subject": "open",
            },
            {
                "id": "3",
                "unread": 0,
                "terminal": False,
                "last_subject": "working",
            },
        ],
        "attention": [{"id": "2"}],
        "checkpoint_due": True,
        "unread_toc": [{"thread": "2"}],
        "watchers_complete_unrelayed": [{"file": "w1.state.json", "thread": "2"}],
        "fleet": {"stargate": "ok"},
        "fingerprint": "abc",
        "policy": {
            "gear": "3-wake-on-attention",
            "successor_model": "cursor/claude-opus-5",
            "post_digest": True,
            "ready": False,
        },
        "budget": {"stop_class": None, "used_tokens": 1000},
    }
    base.update(overrides)
    return base


def test_projection_drops_unlisted_keys_and_filters_terminal() -> None:
    proj = project_digest(_full_digest())
    assert "register" not in proj
    assert "fingerprint" not in proj
    assert "fleet" not in proj
    assert "unread_toc" not in proj
    lane_ids = [lane["id"] for lane in proj["lanes"]]
    assert "1" not in lane_ids
    assert "2" in lane_ids
    assert "3" in lane_ids
    assert proj["watchers"] == [{"file": "w1.state.json", "thread": "2"}]
    assert proj["policy"] == {
        "gear": "3-wake-on-attention",
        "successor_model": "cursor/claude-opus-5",
        "post_digest": True,
    }
    assert proj["budget"] == {"stop_class": None}


def test_render_body_fits_cap_with_many_lanes() -> None:
    lanes = [
        {
            "id": str(i),
            "slug": f"lane-{i}",
            "unread": 1,
            "terminal": False,
            "last_subject": "x" * 80,
        }
        for i in range(40)
    ]
    proj = project_digest(_full_digest(lanes=lanes))
    body = render_body(proj)
    assert body is not None
    assert len(body.encode("utf-8")) <= 4096
    parsed = json.loads(body)
    assert "lanes_omitted" in parsed
    assert parsed["lanes_omitted"] >= 1


def test_publish_posts_with_supersedes_turn() -> None:
    """supersedes_turn from send route (turns_models.TurnSendCreate)."""
    client = MagicMock()
    resp = MagicMock()
    resp.status_code = 201
    resp.json.return_value = {"turn": {"turn_number": 42, "id": 999}}
    client.post.return_value = resp
    state = {"digest_turn_number": 41, "digest_turn_id": 998}
    digest = _full_digest()
    result = publish_digest("10479", digest, state, client=client)
    assert result == {"turn_number": 42, "turn_id": 999}
    args, kwargs = client.post.call_args
    assert args[0] == "/threads/send"
    payload = kwargs["json"]
    assert payload["thread"] == "10479"
    assert payload["to"] == "web-anthropic"
    assert payload["from"] == "cursor"
    assert payload["subject"] == "DIGEST 10479 2026-09-11T12:00:00Z"
    assert payload["supersedes_turn"] == 41
    assert state["digest_turn_number"] == 42
    assert state["digest_turn_id"] == 999


def test_publish_http_error_returns_none_state_unchanged() -> None:
    client = MagicMock()
    client.post.side_effect = httpx.HTTPError("down")
    state = {"digest_turn_number": 5, "digest_turn_id": 50}
    before = dict(state)
    assert publish_digest("10479", _full_digest(), state, client=client) is None
    assert state == before


def test_publish_if_enabled_skips_echo_of_own_digest_turn() -> None:
    """Our DIGEST bumps root.turns; with lanes unchanged the next tick must not republish."""
    client = MagicMock()
    resp = MagicMock()
    resp.status_code = 201
    resp.json.return_value = {"turn": {"turn_number": 11, "id": 1001}}
    client.post.return_value = resp
    state = {"policy": {"gear": "3-wake-on-attention"}}
    first = _full_digest(changed_since_last_tick=True)
    assert publish_if_enabled("10479", first, state, client=client) is True
    assert state["digest_turn_number"] == 11
    # Next tick: root.turns == our turn, everything a reader acts on is unchanged.
    echo = _full_digest(changed_since_last_tick=True)
    echo["root"] = {**echo["root"], "turns": 11, "last_subject": "DIGEST 10479 …"}
    assert publish_if_enabled("10479", echo, state, client=client) is False
    assert client.post.call_count == 1
    # A real lane change on top of the echo publishes again.
    moved = _full_digest(changed_since_last_tick=True)
    moved["root"] = {**moved["root"], "turns": 11}
    moved["lanes"][1] = {**moved["lanes"][1], "turns": 7}
    resp.json.return_value = {"turn": {"turn_number": 12, "id": 1002}}
    assert publish_if_enabled("10479", moved, state, client=client) is True
    assert client.post.call_count == 2


def test_post_digest_policy_gear_three_and_default() -> None:
    assert (
        effective_policy({"policy": {"gear": "3-wake-on-attention"}})["post_digest"]
        is True
    )
    assert effective_policy({"policy": {}})["post_digest"] is False
