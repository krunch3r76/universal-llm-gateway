"""Offline tests for transcript_projection_facts (P1, P2, AC-8)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cortex_store.transcript_lane_touch import lane_touches_from_records
from cortex_store.transcript_projection_facts import parse_records, turn_index_at


def _record(role: str, content: list[dict]) -> dict:
    return {"role": role, "message": {"content": content}}


def _tool_use(name: str, inp: dict) -> dict:
    return {"type": "tool_use", "name": name, "input": inp}


@pytest.mark.offline
def test_p1_synthetic_jsonl_facts(tmp_path: Path) -> None:
    """P1: boundaries, tab titles, artifacts, bus_touch parity."""
    records = [
        _record("user", [{"type": "text", "text": "<user_query>first ask</user_query>"}]),
        _record("user", [{"type": "tool_result", "content": []}]),
        _record(
            "assistant",
            [
                _tool_use("cursor-app-control_rename_chat", {"title": "Tab One"}),
                _tool_use(
                    "agent_bus",
                    {
                        "op": "send",
                        "thread": "10223",
                        "subject": "INFO — hello",
                    },
                ),
            ],
        ),
        _record("user", [{"type": "text", "text": "<user_query>second ask</user_query>"}]),
        _record(
            "assistant",
            [
                _tool_use("cursor-app-control_rename_chat", {"title": "Tab Two"}),
                _tool_use(
                    "agent_bus",
                    {
                        "op": "send",
                        "thread": "10223",
                        "subject": "CHECKPOINT — house",
                        "supersedes_turn": 1,
                    },
                ),
                _tool_use(
                    "fs",
                    {"op": "write", "path": "cortex://notes/system/threads/x.md"},
                ),
                _tool_use(
                    "team_dispatch",
                    {
                        "op": "generate",
                        "seat": "cursor-sdk",
                        "contract": "implement",
                        "execution_id": "exec-1",
                    },
                ),
            ],
        ),
        _record("user", [{"type": "text", "text": "<user_query>third</user_query>"}]),
        _record(
            "assistant",
            [
                _tool_use(
                    "agent_bus",
                    {
                        "op": "send",
                        "thread": "10303",
                        "subject": "CLOSEOUT — WORK child",
                    },
                ),
            ],
        ),
    ]
    facts = parse_records(records, "uuid-test-1")
    assert facts.turn_count == 3
    assert facts.tab_titles == ["Tab One", "Tab Two"]
    assert facts.tab_titles[-1] == "Tab Two"
    checkpoints = [s for s in facts.bus_sends if s.kind == "CHECKPOINT"]
    assert len(checkpoints) == 1
    assert checkpoints[0].turn_index == 2
    assert facts.bus_touch == lane_touches_from_records(records)
    assert any(a.endswith("x.md") for a in facts.artifacts)
    assert facts.dispatches and facts.dispatches[0].op == "generate"


@pytest.mark.offline
def test_turn_index_at_matches_walk_turns() -> None:
    records = [
        _record("user", [{"type": "text", "text": "one"}]),
        _record("assistant", [{"type": "text", "text": "ok"}]),
        _record("user", [{"type": "tool_result", "content": []}]),
        _record("user", [{"type": "text", "text": "two"}]),
    ]
    assert turn_index_at(records, 3) == 2


@pytest.mark.offline
@pytest.mark.skipif(
    not Path(
        "/home/io/.cursor/projects/mnt-torus-projects-universal-llm-gateway/agent-transcripts"
    ).is_dir(),
    reason="real JSONL root absent",
)
def test_p2_real_jsonl_turn_counts_if_present() -> None:
    root = Path(
        "/home/io/.cursor/projects/mnt-torus-projects-universal-llm-gateway/agent-transcripts"
    )
    for tid, expected in (
        ("9c37637d-379f-467e-89fd-849f6950ee19", 6),
        ("c6f9360d-6bc7-4245-8304-478455356dad", 17),
    ):
        path = root / tid / f"{tid}.jsonl"
        if not path.is_file():
            continue
        lines = path.read_text(encoding="utf-8").strip().splitlines()
        records = [json.loads(line) for line in lines if line.strip()]
        facts = parse_records(records, tid)
        assert facts.turn_count == expected
