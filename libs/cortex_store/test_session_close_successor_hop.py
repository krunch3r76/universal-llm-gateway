"""Successor-hop preflight + persist-echo contracts for Nth ``/session-end``."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from agent_seat.session_id import session_id_time_base

from cortex_store.dispatch_ops import ops_journals
from cortex_store.session_close_successor_hop import (
    HOP_REASON,
    parse_utc_timestamp,
    resolve_session_id_for_close,
)
from cortex_store.transcript_session_id import derive_session_id_from_jsonl_start

pytestmark = pytest.mark.offline

_SEALED = "cursor-2026-08-25-172800-d14"
_LID_TS = "2026-08-25T18:00:00Z"
_START_TS = "2026-08-25T17:28:00+00:00"
_AFTER_TS = "2026-08-25T19:00:00+00:00"
_UUID_A = "58e37659-e61d-436f-8a8a-71b4af30d0f5"
_UUID_B = "32def1a3-1111-2222-3333-444444444444"


def _summary(text: str) -> str:
    return f"## Session Summary\n\n**Decisions:** {text}\n**Open items:** None.\n"


def _write_user_turns(path: Path, stamps: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    for stamp in stamps:
        records.append(
            {
                "role": "user",
                "message": {
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                f"<timestamp>{stamp}</timestamp>\n"
                                "Please continue the hop arc."
                            ),
                        }
                    ]
                },
            }
        )
        records.append(
            {
                "role": "assistant",
                "message": {"content": [{"type": "text", "text": "Acknowledged."}]},
            }
        )
    with path.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec) + "\n")


def _seal(
    db_path: Path,
    session_id: str,
    *,
    timestamp: str = _LID_TS,
    prior: str | None = None,
    agent: str = "cursor",
) -> int:
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            "INSERT INTO session_journals "
            "(timestamp, agent, summary, session_id, prior_session_id) "
            "VALUES (?, ?, ?, ?, ?)",
            (timestamp, agent, "Sealed lid for hop tests.", session_id, prior),
        )
        conn.commit()
        return int(cur.lastrowid or 0)
    finally:
        conn.close()


def _preflight(session_id: str, jsonl_path: Path, *, agent: str = "cursor") -> dict:
    return ops_journals._op_session_close_preflight(
        session_id=session_id,
        agent=agent,
        transcript_jsonl_path=str(jsonl_path),
        session_summary_md=_summary("Preflight hop probe for successor mint."),
        summary="Preflight hop probe for successor mint.",
        transcript_depth="light",
    )


def test_preflight_hops_when_sealed_id_has_later_user_turn(
    session_env: dict[str, Path],
) -> None:
    jsonl = session_env["transcripts_root"] / _UUID_A / f"{_UUID_A}.jsonl"
    _write_user_turns(jsonl, [_START_TS, _AFTER_TS])
    _seal(session_env["db_path"], _SEALED)

    result = _preflight(_SEALED, jsonl)

    assert result["ok"] is True
    assert result["hop_reason"] == HOP_REASON
    assert result["prior_session_id"] == _SEALED
    assert result["session_id"] != _SEALED
    assert session_id_time_base(result["session_id"]) == "cursor-2026-08-25-190000"


def test_preflight_no_hop_when_sealed_and_no_later_user_turn(
    session_env: dict[str, Path],
) -> None:
    jsonl = session_env["transcripts_root"] / _UUID_A / f"{_UUID_A}.jsonl"
    # Turns stay before the lid so work_since is false regardless of wall clock.
    _write_user_turns(jsonl, ["2020-01-01T00:00:00+00:00"])
    first = ops_journals._op_session_close(
        session_id=_SEALED,
        agent="cursor",
        session_summary_md=_summary("First close seals the journal row."),
        summary="First close seals the journal row.",
        transcript_depth="light",
    )
    assert "error" not in first, first

    result = _preflight(_SEALED, jsonl)

    assert result["ok"] is True
    assert "hop_reason" not in result
    assert result.get("session_id") in (None, _SEALED)

    second = ops_journals._op_session_close(
        session_id=_SEALED,
        agent="cursor",
        session_summary_md=_summary("Retry close of the sealed hop-1 id."),
        summary="Retry close of the sealed hop-1 id.",
        transcript_depth="light",
    )
    assert second.get("already_closed") is True
    assert second["journal_row_id"] == first["journal_row_id"]


def test_third_session_end_chains_prior_to_hop_two(
    session_env: dict[str, Path],
) -> None:
    jsonl = session_env["transcripts_root"] / _UUID_A / f"{_UUID_A}.jsonl"
    hop2_ts = "2026-08-25T19:00:00+00:00"
    hop3_ts = "2026-08-25T20:15:00+00:00"
    _write_user_turns(jsonl, [_START_TS, hop2_ts, hop3_ts])
    _seal(session_env["db_path"], _SEALED, timestamp="2026-08-25T18:00:00Z")

    first_hop = _preflight(_SEALED, jsonl)
    assert first_hop["hop_reason"] == HOP_REASON
    hop2 = first_hop["session_id"]
    assert first_hop["prior_session_id"] == _SEALED
    _seal(
        session_env["db_path"],
        hop2,
        timestamp="2026-08-25T19:30:00Z",
        prior=_SEALED,
    )

    third = _preflight(_SEALED, jsonl)
    assert third["hop_reason"] == HOP_REASON
    assert third["prior_session_id"] == hop2
    assert third["prior_session_id"] != _SEALED
    assert third["session_id"] != hop2
    assert session_id_time_base(third["session_id"]) == "cursor-2026-08-25-201500"


def test_boot_held_sealed_id_still_hops(
    session_env: dict[str, Path],
) -> None:
    jsonl = session_env["transcripts_root"] / _UUID_A / f"{_UUID_A}.jsonl"
    _write_user_turns(jsonl, [_START_TS, _AFTER_TS])
    _seal(session_env["db_path"], _SEALED)
    from_jsonl = derive_session_id_from_jsonl_start(jsonl_path=jsonl, agent="cursor")
    assert from_jsonl is not None
    assert from_jsonl != _SEALED

    result = _preflight(_SEALED, jsonl)

    assert result["hop_reason"] == HOP_REASON
    assert result["session_id"] != _SEALED
    assert result["session_id"] != from_jsonl
    assert result["prior_session_id"] == _SEALED
    assert result.get("session_id_from_jsonl_start") == from_jsonl


def test_same_utc_second_two_conversation_uuids_differ(
    session_env: dict[str, Path],
) -> None:
    root = session_env["transcripts_root"]
    path_a = root / _UUID_A / f"{_UUID_A}.jsonl"
    path_b = root / _UUID_B / f"{_UUID_B}.jsonl"
    _write_user_turns(path_a, [_START_TS])
    _write_user_turns(path_b, [_START_TS])

    first_a = derive_session_id_from_jsonl_start(jsonl_path=path_a, agent="cursor")
    second_a = derive_session_id_from_jsonl_start(jsonl_path=path_a, agent="cursor")
    id_b = derive_session_id_from_jsonl_start(jsonl_path=path_b, agent="cursor")

    assert first_a is not None and id_b is not None
    assert first_a == second_a
    assert first_a != id_b
    assert session_id_time_base(first_a) == session_id_time_base(id_b)
    assert session_id_time_base(first_a) == "cursor-2026-08-25-172800"


def test_persist_sealed_id_still_already_closed(
    session_env: dict[str, Path],
) -> None:
    session_id = "cursor-2026-08-25-181500-abc"
    first = ops_journals._op_session_close(
        session_id=session_id,
        agent="cursor",
        session_summary_md=_summary("First light close to seal the journal row."),
        summary="First light close to seal the journal row.",
        transcript_depth="light",
    )
    assert "error" not in first, first
    assert first.get("already_closed") is not True

    second = ops_journals._op_session_close(
        session_id=session_id,
        agent="cursor",
        session_summary_md=_summary("Second persist of the same sealed session_id."),
        summary="Second persist of the same sealed session_id.",
        transcript_depth="light",
    )
    assert second.get("already_closed") is True
    assert second["journal_row_id"] == first["journal_row_id"]
    conn = sqlite3.connect(session_env["db_path"])
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM session_journals WHERE session_id = ?",
            (session_id,),
        ).fetchone()
    finally:
        conn.close()
    assert count is not None and int(count[0]) == 1


def test_hop_preflight_stable_across_retries(
    session_env: dict[str, Path],
) -> None:
    jsonl = session_env["transcripts_root"] / _UUID_A / f"{_UUID_A}.jsonl"
    _write_user_turns(jsonl, [_START_TS, _AFTER_TS])
    _seal(session_env["db_path"], _SEALED)
    first = _preflight(_SEALED, jsonl)
    second = _preflight(_SEALED, jsonl)
    assert first["session_id"] == second["session_id"]
    assert first["prior_session_id"] == second["prior_session_id"]


def test_hop_uses_first_post_lid_timestamp_not_wall_clock(
    session_env: dict[str, Path],
) -> None:
    jsonl = session_env["transcripts_root"] / _UUID_A / f"{_UUID_A}.jsonl"
    later = (datetime.now(UTC) + timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    _write_user_turns(jsonl, [_START_TS, _AFTER_TS, later])
    _seal(session_env["db_path"], _SEALED)
    result = _preflight(_SEALED, jsonl)
    assert session_id_time_base(result["session_id"]) == "cursor-2026-08-25-190000"


def test_parse_utc_timestamp_honors_positive_offset() -> None:
    parsed = parse_utc_timestamp("2026-08-25T17:28:00+00:00")
    assert parsed is not None
    assert parsed.tzinfo is UTC
    assert parsed.hour == 17
    assert parsed.minute == 28


def test_resolve_session_id_for_close_without_jsonl_start_returns_none(
    tmp_path: Path,
) -> None:
    jsonl = tmp_path / "uuid" / "uuid.jsonl"
    jsonl.parent.mkdir(parents=True)
    jsonl.write_text("", encoding="utf-8")
    resolution = resolve_session_id_for_close(
        session_id=None,
        agent="cursor",
        transcript_jsonl_path=str(jsonl),
    )
    assert resolution is None
