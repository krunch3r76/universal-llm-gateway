"""Unit tests for ContinuityCheckpointPreConsolidateHandler."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from ._card_patch import (
    RESIDUE_CAP_CHARS,
    _format_opportunity_row,
    clamp_residue,
    parse_worker_json,
    validate_worker_payload,
)
from ._packet import render_tape_lines
from .pre_consolidate import _is_sdk_closeout_turn

pytestmark = pytest.mark.offline


def test_is_sdk_closeout_turn_matches_subject() -> None:
    turn = {
        "from_agent": "cursor-sdk",
        "subject": "cursor-sdk CLOSEOUT abc contract=none",
        "body": "{}",
    }
    assert _is_sdk_closeout_turn(turn, "cursor-sdk")
    assert not _is_sdk_closeout_turn(turn, "cursor")


def test_validate_worker_payload_requires_mission() -> None:
    ok, reason = validate_worker_payload(
        {"card_patch": {"resume_open": "line"}, "residue": "no mission here"}
    )
    assert not ok
    assert reason == "missing_mission"


def test_parse_worker_json_follows_sdk_envelope_to_sidecar(tmp_path, monkeypatch) -> None:
    sidecar = tmp_path / "closeout.md"
    sidecar.write_text(
        '```json\n{"card_patch": {"resume_open": "line"}, '
        '"residue": "Mission: ok", "mission": "one"}\n```\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "handlers._card_patch._resolve_workspaces_uri",
        lambda uri: sidecar if "closeout.md" in uri else None,
    )
    parsed = parse_worker_json(
        json.dumps(
            {
                "schema_version": 1,
                "source_ref": "workspaces://universal-llm-gateway/tmp/reviews/closeouts/closeout.md",
            }
        )
    )
    assert parsed is not None
    assert parsed["card_patch"]["resume_open"] == "line"


def test_format_opportunity_row_accepts_dict_rows() -> None:
    text = _format_opportunity_row(
        {"id": "4b.2", "status": "in_flight", "note": "pre_consolidate drill"}
    )
    assert "4b.2" in text
    assert "in_flight" in text


def test_validate_worker_payload_accepts_mission_in_residue() -> None:
    ok, _ = validate_worker_payload(
        {
            "card_patch": {"resume_open": "line"},
            "residue": "Mission: resume house",
        }
    )
    assert ok


@pytest.mark.asyncio
async def test_pre_consolidate_fetches_closeout_from_qualifying_reply_turn() -> None:
    from .pre_consolidate import ContinuityCheckpointPreConsolidateHandler

    closeout_body = json.dumps(
        {
            "schema_version": 1,
            "source_ref": "workspaces://universal-llm-gateway/tmp/reviews/closeouts/x.md",
        }
    )

    class _Ctx:
        execution_id = "exec-pre"
        dispatch_thread_id = "10223"
        options = {
            "thread": "10223",
            "surface": "cursor",
            "from_agent": "continuity",
        }
        outputs = {
            "seal": {
                "json": {
                    "session_id": "cursor-2026-09-09-193600-ead",
                    "turn_count": 24,
                }
            },
            "tape": {"json": {}},
        }

    handler = ContinuityCheckpointPreConsolidateHandler()
    dispatch_resp = {
        "thread_id": "10435",
        "poll_hint": {
            "arguments": {"thread": "10435", "after_turn": 1, "from_agent": "cursor-sdk"}
        },
        "reply_from_agent": "cursor-sdk",
        "dispatch_id": "d1",
    }
    with (
        patch(
            "handlers.pre_consolidate.stargate_post",
            new=AsyncMock(return_value=(dispatch_resp, 202)),
        ),
        patch(
            "handlers.pre_consolidate.bus_wait",
            new=AsyncMock(
                return_value=(
                    {"complete": True, "qualifying_reply_turn": 2},
                    200,
                )
            ),
        ),
        patch(
            "handlers.pre_consolidate.bus_fetch_turn",
            new=AsyncMock(
                return_value={
                    "turn_number": 2,
                    "from": "cursor-sdk",
                    "subject": "cursor-sdk CLOSEOUT x",
                    "body": closeout_body,
                }
            ),
        ),
        patch(
            "handlers.pre_consolidate.parse_worker_json",
            return_value=None,
        ),
    ):
        out = await handler.execute(object(), _Ctx())
    assert out.json["residue_source"] == "mechanical"
    assert out.json["worker_thread"] == "10435"


def test_render_tape_lines_contains_newest_turn_verbatim() -> None:
    tape = {
        "envelope": {
            "messages": [
                {"role": "user", "turn_index": 1, "content": "old turn"},
                {"role": "assistant", "turn_index": 2, "content": "newest turn text"},
            ]
        }
    }
    rendered, stats = render_tape_lines(tape, max_chars=10000)
    assert "newest turn text" in rendered
    assert stats["kept_turns"] == 2


def test_render_tape_lines_drops_oldest_whole_turns_with_marker() -> None:
    messages = [
        {"role": "user", "turn_index": i, "content": f"content-{i}" * 20}
        for i in range(1, 21)
    ]
    tape = {"envelope": {"messages": messages}}
    rendered, stats = render_tape_lines(tape, max_chars=500)
    assert "(tape truncated:" in rendered
    assert stats["truncated"] is True
    assert "content-1" not in rendered or stats["dropped_turns"] > 0


def test_render_tape_lines_emits_no_markdown_headings() -> None:
    tape = {
        "envelope": {
            "messages": [{"role": "user", "turn_index": 1, "content": "hello"}]
        }
    }
    rendered, _ = render_tape_lines(tape)
    for line in rendered.splitlines():
        assert not line.startswith("#")


def test_validate_worker_payload_clamps_soft_overflow() -> None:
    residue = "Mission: ok\n" + ("x" * 930)
    ok, reason = validate_worker_payload(
        {"card_patch": {"resume_open": "line"}, "residue": residue}
    )
    assert ok
    assert reason == "ok"
    clamped, truncated = clamp_residue(residue)
    assert truncated
    assert len(clamped) <= RESIDUE_CAP_CHARS + 40


def test_validate_worker_payload_rejects_double_cap() -> None:
    residue = "Mission: ok\n" + ("x" * 1600)
    ok, reason = validate_worker_payload(
        {"card_patch": {"resume_open": "line"}, "residue": residue}
    )
    assert not ok
    assert reason == "residue_too_long"


@pytest.mark.asyncio
async def test_seed_is_folded_and_worker_awaited() -> None:
    from .pre_consolidate import ContinuityCheckpointPreConsolidateHandler

    worker_json = (
        '```json\n{"card_patch": {"resume_open": "In one line: go"}, '
        '"residue": "Mission: worker authored", "mission": "one"}\n```\n'
    )

    class _Ctx:
        execution_id = "exec-pre"
        dispatch_thread_id = "10223"
        options = {
            "thread": "10223",
            "surface": "cursor",
            "from_agent": "continuity",
            "residue": "Ruling: seed line",
        }
        outputs = {"seal": {"json": {"turn_count": 1}}, "tape": {"json": {}}}

    handler = ContinuityCheckpointPreConsolidateHandler()
    dispatch_resp = {
        "thread_id": "10435",
        "poll_hint": {
            "arguments": {"thread": "10435", "after_turn": 1, "from_agent": "cursor-sdk"}
        },
        "reply_from_agent": "cursor-sdk",
        "dispatch_id": "d1",
    }
    with (
        patch(
            "handlers.pre_consolidate.stargate_post",
            new=AsyncMock(return_value=(dispatch_resp, 202)),
        ) as mock_post,
        patch(
            "handlers.pre_consolidate.bus_wait",
            new=AsyncMock(
                return_value=(
                    {"complete": True, "qualifying_reply_turn": 2},
                    200,
                )
            ),
        ) as mock_wait,
        patch(
            "handlers.pre_consolidate.bus_fetch_turn",
            new=AsyncMock(
                return_value={
                    "turn_number": 2,
                    "from": "cursor-sdk",
                    "subject": "cursor-sdk CLOSEOUT x",
                    "body": worker_json,
                }
            ),
        ),
        patch(
            "handlers.pre_consolidate.apply_card_patch",
            return_value=(True, "uri", "ok"),
        ),
    ):
        out = await handler.execute(object(), _Ctx())
    mock_post.assert_awaited_once()
    mock_wait.assert_awaited()
    assert out.json["residue_source"] == "model"
    assert out.json["residue"] == "Mission: worker authored"
