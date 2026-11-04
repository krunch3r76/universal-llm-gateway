"""Agent-facing liaison digest projection (friction 42277 shape)."""

from __future__ import annotations

import json

import pytest

from bus_watch.digest_agent_projection import (
    project_liaison_digest_agent,
    serialize_agent_digest,
)

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
                    "thread": "9732",
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
    after = len(serialize_agent_digest(proj))
    assert after < before * 0.6
    attn = proj["attention"][0]
    assert attn.get("lane_ref") == {"id": "17620"}
    assert "unread_turns" not in attn
    assert proj["lanes"][0]["disposition"] == "closed_summary"
    assert _BIG_BODY not in json.dumps(proj)


def test_active_failed_lane_survives_cap() -> None:
    failed = {
        "id": "20002",
        "status": "closed",
        "lifecycle": "failed",
        "unread": 1,
        "terminal": True,
        "last_subject": "FAILED",
        "unread_turns": [{"turn_number": 9, "from": "cursor", "subject": "FAILED", "body": "fix"}],
    }
    filler = {
        "status": "closed",
        "lifecycle": "completed",
        "unread": 0,
        "terminal": True,
        "last_subject": "CLOSEOUT",
    }
    digest = {
        "ts": "2026-11-04T00:00:00Z",
        "root": {"id": "9732", "turns": 5, "unread": 0},
        "lanes": [failed] + [dict(filler, id=f"999{i}") for i in range(30)],
        "attention": [failed],
        "policy": {},
        "budget": {},
        "induction": {},
    }
    proj = project_liaison_digest_agent(digest, cap_bytes=2048)
    kept_ids = {lane["id"] for lane in proj.get("lanes") or []}
    assert "20002" in kept_ids


def test_notice_dedupe_is_per_thread() -> None:
    digest = {
        "ts": "2026-11-04T00:00:00Z",
        "root": {
            "id": "9732",
            "recent_turns": [
                {"turn_number": 1, "from": "a", "subject": "S", "thread": "9732"},
                {"turn_number": 1, "from": "a", "subject": "S", "thread": "8888"},
            ],
        },
        "lanes": [],
        "attention": [],
        "policy": {},
        "budget": {},
        "induction": {},
    }
    proj = project_liaison_digest_agent(digest)
    assert len(proj["root"]["recent_turns"]) == 2


def test_cap_uses_same_serialization_as_render() -> None:
    digest = _duplicate_lane_digest()
    proj = project_liaison_digest_agent(digest, cap_bytes=500)
    wire = serialize_agent_digest(proj)
    assert len(wire) <= 500 or proj.get("overflow")


def test_projection_carries_full_digest_rebuild_pointer() -> None:
    raw = _duplicate_lane_digest()
    proj = project_liaison_digest_agent(raw)
    assert "9732" in str(proj["projection"]["full_digest_rebuild_read_only"])
    assert proj["projection"]["full_digest_sha256"]
