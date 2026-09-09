"""Unit tests for ContinuityCheckpointPreConsolidateHandler."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

import json

from ._card_patch import _format_opportunity_row, parse_worker_json, validate_worker_payload
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


@pytest.mark.asyncio
async def test_seat_residue_skips_wait() -> None:
    from .pre_consolidate import ContinuityCheckpointPreConsolidateHandler

    class _Ctx:
        execution_id = "exec-pre"
        dispatch_thread_id = "10223"
        options = {
            "thread": "10223",
            "surface": "cursor",
            "from_agent": "continuity",
            "residue": "Mission: seat residue",
        }
        outputs = {"seal": {"json": {"turn_count": 1}}, "tape": {"json": {}}}

    handler = ContinuityCheckpointPreConsolidateHandler()
    with patch(
        "handlers.pre_consolidate.stargate_post",
        new=AsyncMock(return_value=({"dispatch_id": "d1"}, 202)),
    ):
        out = await handler.execute(object(), _Ctx())
    assert out.json["residue_source"] == "seat"
