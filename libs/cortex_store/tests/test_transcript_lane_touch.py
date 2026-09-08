"""Tests for transcript_lane_touch (AMEND-R T1–T4)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cortex_store.transcript_lane_touch import (
    binding_for,
    dominant_lane,
    lane_touches,
)

pytestmark = pytest.mark.offline

_LANE_N = "10223"
_LANE_M = "99999"


def _tool_block(tool_name: str, arguments: dict) -> dict:
    return {
        "role": "assistant",
        "message": {
            "content": [
                {
                    "type": "tool_use",
                    "name": "CallDynamicTool",
                    "input": {
                        "toolName": tool_name,
                        "arguments": arguments,
                    },
                }
            ]
        },
    }


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec) + "\n")


def test_t1_dominant_lane_write_counts(tmp_path: Path) -> None:
    path = tmp_path / "w.jsonl"
    _write_jsonl(
        path,
        [
            _tool_block("agent_bus_read", {"thread": _LANE_N}),
            _tool_block("agent_bus_read", {"thread": _LANE_N}),
            _tool_block("agent_bus_read", {"thread": _LANE_N}),
            _tool_block("agent_bus", {"tool": "send", "thread": _LANE_N}),
            _tool_block("agent_bus", {"tool": "post", "thread": _LANE_N}),
            _tool_block("agent_bus", {"tool": "send", "thread": _LANE_M}),
        ],
    )
    touches = lane_touches(path)
    assert touches[_LANE_N].reads == 3
    assert touches[_LANE_N].writes == 2
    assert touches[_LANE_M].writes == 1
    assert dominant_lane(touches) == _LANE_N


def test_t2_reads_only_binding_read_only(tmp_path: Path) -> None:
    path = tmp_path / "r.jsonl"
    _write_jsonl(path, [_tool_block("agent_bus_read", {"thread": _LANE_N})])
    touches = lane_touches(path)
    binding, _ = binding_for(
        _LANE_N,
        touches,
        explicit_uuids=set(),
        conversation_uuid="uuid-read-only",
    )
    assert binding == "read_only"
    assert dominant_lane(touches) is None


def test_t3_write_tie_latest_write_wins(tmp_path: Path) -> None:
    path = tmp_path / "tie.jsonl"
    _write_jsonl(
        path,
        [
            _tool_block("agent_bus", {"tool": "send", "thread": _LANE_N}),
            _tool_block("agent_bus", {"tool": "send", "thread": _LANE_M}),
        ],
    )
    assert dominant_lane(lane_touches(path)) == _LANE_M


def test_t4_inner_arguments_dict_and_json_string(tmp_path: Path) -> None:
    path = tmp_path / "args.jsonl"
    _write_jsonl(
        path,
        [
            {
                "role": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "name": "CallDynamicTool",
                            "input": {
                                "toolName": "agent_bus",
                                "arguments": json.dumps(
                                    {"tool": "reply", "thread_id": _LANE_N}
                                ),
                            },
                        }
                    ]
                },
            }
        ],
    )
    touches = lane_touches(path)
    assert touches[_LANE_N].writes == 1
