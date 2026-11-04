"""Agent-facing liaison digest projection (friction 42277 shape)."""

from __future__ import annotations

import json

import pytest

from bus_watch.digest_agent_projection import project_liaison_digest_agent

pytestmark = pytest.mark.offline

_BIG_BODY = "x" * 8000


def _duplicate_lane_digest() -> dict:
    lane_full = {
        "id": "17620",
        "slug": "done-lane",
        "status": "closed",
        "lifecycle": "completed",
        "lane_role": "sub_mission",
        "turns": 40,
        "unread": 0,
        "last_subject": "cursor-sdk CLOSEOUT",
        "terminal": True,
        "updated_at": "2026-11-01T00:00:00Z",
        "unread_turns": [
            {
                "turn_number": 39,
                "from": "cursor-sdk",
                "subject": "CLOSEOUT",
                "body": _BIG_BODY,
            }
        ],
    }
    attention = [{**lane_full, "unread_turns": lane_full["unread_turns"]}]
    return {
        "ts": "2026-11-04T00:00:00Z",
        "root": {
            "id": "9732",
            "turns": 100,
            "unread": 0,
            "recent_turns": [
                {
                    "turn_number": 39,
                    "from": "cursor-sdk",
                    "subject": "CLOSEOUT",
                    "body": _BIG_BODY,
                }
            ],
            "unread_turns": [],
        },
        "lanes": [lane_full, dict(lane_full, id="8955")],
        "attention": attention,
        "checkpoint_due": False,
        "policy": {"gear": "3-wake-on-attention", "post_digest": True},
        "budget": {"stop_class": None, "used_tokens": 1},
        "induction": {"objective": "test"},
    }


def test_closed_lane_not_duplicated_under_attention() -> None:
    raw = _duplicate_lane_digest()
    before = len(json.dumps(raw, default=str).encode("utf-8"))
    proj = project_liaison_digest_agent(raw, cap_bytes=65536)
    after = len(json.dumps(proj, default=str).encode("utf-8"))
    assert after < before * 0.6
    attn = proj["attention"][0]
    assert attn.get("lane_ref") == "lanes[0]"
    assert "unread_turns" not in attn
    assert proj["lanes"][0]["disposition"] == "closed_summary"
    assert _BIG_BODY not in json.dumps(proj)


def test_active_lane_keeps_actionable_unread() -> None:
    lane = {
        "id": "20001",
        "status": "active",
        "lifecycle": "admitted",
        "unread": 2,
        "terminal": False,
        "last_subject": "open",
        "unread_turns": [
            {
                "turn_number": 3,
                "from": "cursor",
                "subject": "WORK",
                "body": "do thing",
            }
        ],
    }
    digest = {
        "ts": "2026-11-04T00:00:00Z",
        "root": {"id": "9732", "turns": 5, "unread": 0},
        "lanes": [lane],
        "attention": [lane],
        "policy": {},
        "budget": {},
        "induction": {},
    }
    proj = project_liaison_digest_agent(digest)
    assert proj["lanes"][0].get("disposition") != "closed_summary"
    assert proj["lanes"][0]["unread_turns"][0]["body"] == "do thing"


def test_projection_carries_full_digest_sha() -> None:
    raw = _duplicate_lane_digest()
    proj = project_liaison_digest_agent(raw)
    assert proj["projection"]["full_digest_sha256"]
    assert proj["projection"]["source"] == "digest_agent_projection"
