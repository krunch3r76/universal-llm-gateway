"""L6 — compact executive envelope on agent_bus get for large closeouts."""

from __future__ import annotations

import hashlib
import json
from unittest.mock import patch

from tools.agent_bus.fetch import _get_dispatch


def _large_json_closeout() -> dict:
    summary = "\n".join(f"summary line {i}: detail" for i in range(120))
    body = {
        "schema_version": 1,
        "status": "complete",
        "work_outcome": "shipped batch",
        "summary": summary,
        "source_ref": "todo:test",
        "evidence_uris": {
            "artifact_paths": ["workspaces://repo/a.py"],
            "artifact_digests": ["sha256:abc"],
            "git_refs": ["abc123"],
        },
        "verification": {"pytest": "12 passed"},
    }
    raw = json.dumps(body)
    assert len(raw.encode()) > 1500
    return {
        "subject": "cursor-sdk CLOSEOUT dispatch-1",
        "body": raw,
        "thread": "18361",
        "turn_number": 2,
    }


def _large_cdp_reply() -> dict:
    lines = ["# CDP generate result", "VERDICT: APPROVE", "read-all-cited-files: yes"]
    lines += [f"detail line {i}" for i in range(200)]
    body = "\n".join(lines)
    assert len(body.encode()) > 1500
    return {
        "subject": "cdp reply exec-9",
        "body": body,
        "thread": "1",
        "turn_number": 3,
    }


@patch("tools.agent_bus.fetch.relay")
def test_get_default_compact_json_closeout(relay) -> None:
    turn = _large_json_closeout()
    relay.return_value = turn
    out = _get_dispatch(thread="18361", turn_number=2)
    projected = out["turn"]
    assert len(projected["body"].encode()) < 1500
    assert projected["projection_available"] == "full"
    assert projected["body_sha256"] == hashlib.sha256(turn["body"].encode()).hexdigest()
    assert projected["body_bytes"] == len(turn["body"].encode())
    assert "shipped batch" in projected["body"] or "complete" in projected["body"]


@patch("tools.agent_bus.fetch.relay")
def test_get_projection_full_returns_exact_body(relay) -> None:
    turn = _large_json_closeout()
    relay.return_value = turn
    out = _get_dispatch(thread="18361", turn_number=2, projection="full")
    assert out["turn"]["body"] == turn["body"]
    assert "body_sha256" not in out["turn"]


@patch("tools.agent_bus.fetch.relay")
def test_get_default_compact_cdp_reply(relay) -> None:
    turn = _large_cdp_reply()
    relay.return_value = turn
    out = _get_dispatch(thread="1", turn_number=3)
    projected = out["turn"]
    assert len(projected["body"].encode()) < 1500
    assert "VERDICT: APPROVE" in projected["body"]
    assert projected["projection_available"] == "full"
    assert projected["body_sha256"] == hashlib.sha256(turn["body"].encode()).hexdigest()


@patch("tools.agent_bus.fetch.relay")
def test_get_full_cdp_reply_unchanged(relay) -> None:
    turn = _large_cdp_reply()
    relay.return_value = turn
    out = _get_dispatch(thread="1", turn_number=3, projection="full")
    assert out["turn"]["body"] == turn["body"]
