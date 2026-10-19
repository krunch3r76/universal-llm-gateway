"""L6 — compact executive envelope on agent_bus get for large closeouts."""

from __future__ import annotations

import hashlib
import json
import time
from unittest.mock import patch

from tools.agent_bus._closeout_projection import project_turn_body
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


def _verification_blob() -> list[dict]:
    return [
        {
            "command": f"pytest services/mcp-server/tools/test_agent_bus_fetch_l6.py -q chunk-{i}",
            "exit_code": 0,
            "stdout": "1 passed" * 55,
            "stderr": "",
        }
        for i in range(4)
    ]


def test_project_turn_body_short_summary_verification_list_no_infinite_loop() -> None:
    body = {
        "schema_version": 1,
        "status": "complete",
        "work_outcome": "shipped",
        "summary": "short summary within seventy characters for envelope fit",
        "source_ref": "todo:18426",
        "verification": _verification_blob(),
    }
    raw = json.dumps(body)
    assert len(raw.encode("utf-8")) > 2000
    turn = {
        "subject": "cursor-sdk CLOSEOUT dispatch-18426",
        "body": raw,
        "thread": "18426",
        "turn_number": 2,
    }
    started = time.monotonic()
    projected = project_turn_body(turn, projection="compact")
    elapsed = time.monotonic() - started
    assert elapsed < 1.0
    body_text = projected["body"]
    assert len(body_text.encode("utf-8")) < 1500
    json.loads(body_text)


def test_project_turn_body_huge_work_outcome_hard_byte_cap() -> None:
    body = {
        "schema_version": 1,
        "status": "complete",
        "work_outcome": "W" * 2500,
        "summary": "tiny",
        "source_ref": "todo:hard-cap",
    }
    raw = json.dumps(body)
    turn = {
        "subject": "cursor-sdk CLOSEOUT dispatch-cap",
        "body": raw,
        "thread": "1",
        "turn_number": 1,
    }
    projected = project_turn_body(turn, projection="compact")
    body_text = projected["body"]
    assert len(body_text.encode("utf-8")) < 1500
    parsed = json.loads(body_text)
    assert parsed.get("status") == "complete"
