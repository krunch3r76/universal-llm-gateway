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
    rebuild = str(proj["projection"]["full_digest_snapshot_read_only"])
    assert "9732" in rebuild
    assert "--emit-only" in rebuild
    assert "lanes_omitted" not in proj
    assert proj["projection"]["full_digest_sha256"]


def test_mixed_failed_and_unstamped_lanes_preserved() -> None:
    digest = {
        "ts": "2026-11-04T00:00:00Z",
        "root": {"id": "9732", "turns": 2, "unread": 0},
        "lanes": [
            {
                "id": "L1",
                "terminal": True,
                "lifecycle": "failed",
                "unread": 0,
                "last_subject": "FAILED",
            },
            {
                "id": "L2",
                "terminal": False,
                "unread": 0,
                "unstamped": 2,
                "quiet_reason": "holder_lost",
                "tags": ["lane:paste"],
                "last_subject": "WORK",
            },
        ],
        "attention": [],
        "policy": {},
        "budget": {},
        "induction": {},
    }
    proj = project_liaison_digest_agent(digest)
    by_id = {lane["id"]: lane for lane in proj["lanes"]}
    assert "L1" in by_id
    assert "L2" in by_id
    assert by_id["L2"]["unstamped"] == 2
    assert by_id["L2"]["quiet_reason"] == "holder_lost"
    assert by_id["L2"]["tags"] == ["lane:paste"]


def test_retrieval_pointers_use_supported_agent_bus_read() -> None:
    digest = {
        "ts": "z",
        "root": {"id": "99", "turns": 1},
        "lanes": [
            {
                "id": "55",
                "unread": 1,
                "unread_turns": [
                    {"turn_number": 3, "from": "a", "subject": "S", "body": "b" * 5000}
                ],
            }
        ],
        "attention": [],
        "policy": {},
        "budget": {},
        "induction": {},
    }
    proj = project_liaison_digest_agent(digest, cap_bytes=800)
    lane = proj["lanes"][0]
    assert "agent_bus_read(fetch, thread=55" in lane["retrieval"]
    row = lane["unread_turns"][0]
    assert row.get("body_truncated") is True
    assert 'projection="full"' in row["body_retrieval"]


def test_unread_turns_kept_when_also_in_recent() -> None:
    row = {"turn_number": 1, "from": "a", "subject": "S", "thread": "9732"}
    digest = {
        "ts": "z",
        "root": {
            "id": "9732",
            "unread_turns": [dict(row)],
            "recent_turns": [dict(row)],
        },
        "lanes": [],
        "attention": [],
        "policy": {},
        "budget": {},
        "induction": {},
    }
    proj = project_liaison_digest_agent(digest)
    assert len(proj["root"]["unread_turns"]) == 1
    assert proj["root"]["recent_turns"] == []
