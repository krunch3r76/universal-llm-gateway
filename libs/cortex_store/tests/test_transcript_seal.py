"""Tests for transcript_seal dispatch op (I6, I7)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from cortex_store.dispatch_ops import ops_journals
from cortex_store.dispatch_ops.ops_transcript_seal import _op_transcript_seal
from cortex_store.transcript_session_id import derive_session_id_from_jsonl_start

pytestmark = pytest.mark.offline

@pytest.fixture(autouse=True)
def _patch_explicit_uuids(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "cortex_store.dispatch_ops.ops_transcript_discover.explicit_uuids_for_lane",
        lambda _thread, extra=None: set(extra or ()),
    )

_UUID = "b2c3d4e5-f6a7-8901-bcde-f12345678901"
_START = "2026-09-07T12:00:00+00:00"


def _summary(text: str) -> str:
    return f"## Session Summary\n\n**Decisions:** {text}\n**Open items:** None.\n"


def _write_jsonl(path: Path, stamps: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    for i, stamp in enumerate(stamps):
        records.append(
            {
                "role": "user",
                "message": {
                    "content": [
                        {
                            "type": "text",
                            "text": f"<timestamp>{stamp}</timestamp>\nTurn {i + 1}.",
                        }
                    ]
                },
            }
        )
        records.append(
            {
                "role": "assistant",
                "message": {
                    "content": [
                        {"type": "text", "text": f"Ack {i + 1}."},
                        {
                            "type": "tool_use",
                            "name": "CallDynamicTool",
                            "input": {
                                "toolName": "agent_bus",
                                "arguments": {"tool": "post", "thread": "10223"},
                            },
                        },
                    ]
                },
            }
        )
    with path.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec) + "\n")


def _journal_count(db_path: Path) -> int:
    conn = sqlite3.connect(db_path)
    try:
        return int(conn.execute("SELECT COUNT(*) FROM session_journals").fetchone()[0])
    finally:
        conn.close()


def test_seal_requires_thread_and_jsonl() -> None:
    assert _op_transcript_seal(thread="1").get("code") == "transcript_seal.missing_jsonl"
    assert (
        _op_transcript_seal(jsonl_path="x/y.jsonl").get("code")
        == "transcript_seal.missing_thread"
    )


@patch("cortex_store.dispatch_ops.ops_transcript_discover.explicit_uuids_for_lane", return_value=set())
@patch("cortex_store.dispatch_ops.ops_transcript_seal.lane_touches")
@patch("cortex_store.session_close_successor_hop.lookup_journaled_by_conversation_uuid")
@patch("cortex_store.session_close_successor_hop.lookup_sealed_journal")
@patch("cortex_store.dispatch_ops.ops_transcript_seal.derive_session_id_from_jsonl_start")
@patch("cortex_store.dispatch_ops.ops_transcript_seal.resolve_jsonl_path")
def test_seal_already_closed(
    mock_resolve, mock_derive, mock_lookup, mock_uuid_lookup, mock_touches, _mock_explicit, tmp_path
) -> None:
    from cortex_store.session_close_successor_hop import SealedJournal
    from cortex_store.transcript_lane_touch import LaneTouch

    mock_touches.return_value = {"100": LaneTouch(writes=1, reads=0, last_write_index=1)}

    p = tmp_path / "u" / "u.jsonl"
    p.parent.mkdir(parents=True)
    p.write_text("{}\n", encoding="utf-8")
    mock_resolve.return_value = p
    mock_derive.return_value = "cursor-2026-09-07-120000-abc"
    mock_uuid_lookup.return_value = None
    mock_lookup.return_value = SealedJournal(
        session_id="cursor-2026-09-07-120000-abc",
        journal_row_id=1,
        timestamp="2026-09-07T12:00:00Z",
        prior_session_id=None,
        closed_by="cursor",
    )
    result = _op_transcript_seal(thread="100", jsonl_path=str(p))
    assert result.get("code") == "transcript_seal.already_closed"


def test_i6_human_close_then_resume_already_closed(session_env: dict[str, Path]) -> None:
    """I6: boot-held human close then resume seal ⇒ already_closed via conversation_uuid."""
    db_path = session_env["db_path"]
    transcripts_root = session_env["transcripts_root"]
    jsonl = transcripts_root / _UUID / f"{_UUID}.jsonl"
    _write_jsonl(jsonl, [_START, "2026-09-07T12:30:00+00:00"])
    rel = f"{_UUID}/{_UUID}.jsonl"
    derived = derive_session_id_from_jsonl_start(jsonl_path=jsonl, agent="cursor")
    assert derived is not None
    boot_held = "cursor-2026-09-07-120000-b00"
    assert boot_held != derived

    human = ops_journals._op_session_close(
        session_id=boot_held,
        agent="cursor",
        transcript_jsonl_path=rel,
        session_summary_md=_summary("Human close before resume."),
        summary="Human close before resume.",
        transcript_depth="verbatim",
    )
    assert "error" not in human, human
    before = _journal_count(db_path)

    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT conversation_uuid FROM session_journals WHERE session_id = ?",
            (boot_held,),
        ).fetchone()
        assert row is not None
        assert row[0] == _UUID
    finally:
        conn.close()

    resume = _op_transcript_seal(thread="10223", jsonl_path=rel)
    assert resume.get("code") == "transcript_seal.already_closed"
    assert resume.get("session_id") == boot_held
    assert _journal_count(db_path) == before


def test_i7_prefix_extend_then_already_closed(session_env: dict[str, Path]) -> None:
    """I7: sealed at 10 turns, grow JSONL ⇒ PREFIX-EXTEND; unchanged ⇒ already_closed."""
    db_path = session_env["db_path"]
    transcripts_root = session_env["transcripts_root"]
    jsonl = transcripts_root / _UUID / f"{_UUID}.jsonl"
    stamps_10 = [f"2026-09-07T12:{i:02d}:00+00:00" for i in range(10)]
    _write_jsonl(jsonl, stamps_10)
    rel = f"{_UUID}/{_UUID}.jsonl"

    first = _op_transcript_seal(thread="10223", jsonl_path=rel)
    assert "error" not in first, first
    assert first.get("turn_count") == 10
    sealed_sid = first["session_id"]

    stamps_20 = stamps_10 + [f"2026-09-07T13:{i:02d}:00+00:00" for i in range(10)]
    _write_jsonl(jsonl, stamps_20)

    extended = _op_transcript_seal(thread="10223", jsonl_path=rel)
    assert "error" not in extended, extended
    assert extended["session_id"] == sealed_sid
    assert extended.get("turn_count") == 20
    assert _journal_count(db_path) == 1

    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT closed_by FROM session_journals WHERE session_id = ?",
            (sealed_sid,),
        ).fetchone()
        assert row is not None
        assert row[0] == "succession"
    finally:
        conn.close()

    unchanged = _op_transcript_seal(thread="10223", jsonl_path=rel)
    assert unchanged.get("code") == "transcript_seal.already_closed"


def test_4s3_extend_post_summary_already_closed(
    session_env: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """4s.3 extend: post-summary JSONL ⇒ already_closed + divergence; prefix extend still works."""
    captured: list[dict[str, Any]] = []

    def _capture(**kwargs: object) -> None:
        captured.append(dict(kwargs))

    monkeypatch.setattr(
        "cortex_store.events_tape.session_close_succession_structural_filled",
        _capture,
    )

    db_path = session_env["db_path"]
    files_root = session_env["files_root"]
    transcripts_root = session_env["transcripts_root"]
    jsonl = transcripts_root / _UUID / f"{_UUID}.jsonl"
    stamps_10 = [f"2026-09-07T12:{i:02d}:00+00:00" for i in range(10)]
    _write_jsonl(jsonl, stamps_10)
    rel = f"{_UUID}/{_UUID}.jsonl"

    first = _op_transcript_seal(thread="10223", jsonl_path=rel)
    assert "error" not in first, first
    sealed_sid = first["session_id"]
    tx_path = files_root / f"notes/system/transcripts/{sealed_sid}.md"
    seal_path = files_root / f"notes/system/seals/{sealed_sid}.messages.json"
    md_before = tx_path.read_bytes()
    seal_before = seal_path.read_bytes()

    from cortex_store.tests.test_session_close_succession_structural_fill import (
        _write_post_summary_jsonl,
    )

    _write_post_summary_jsonl(
        jsonl,
        prior_stamps=stamps_10,
        new_stamps=["2026-09-09T10:00:00+00:00"],
        summary_stamp=stamps_10[0],
    )

    diverged = _op_transcript_seal(thread="10223", jsonl_path=rel)
    assert diverged.get("code") == "transcript_seal.already_closed"
    assert "error" not in diverged
    assert diverged.get("divergence") == "verbatim_diverged"
    assert diverged.get("turn_count") == 10
    assert tx_path.read_bytes() == md_before
    assert seal_path.read_bytes() == seal_before

    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT closed_by FROM session_journals WHERE session_id = ?",
            (sealed_sid,),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row[0] == "succession"

    stamps_20 = stamps_10 + [f"2026-09-07T13:{i:02d}:00+00:00" for i in range(10)]
    _write_jsonl(jsonl, stamps_20)

    extended = _op_transcript_seal(thread="10223", jsonl_path=rel)
    assert "error" not in extended, extended
    assert extended.get("turn_count") == 20

    assert captured, "expected PREFIX-EXTEND succession_structural_filled event"
    ev = captured[-1]
    assert ev.get("reason") == "PREFIX-EXTEND"
    assert ev.get("extended") is True
    assert ev.get("cause") == "prefix_extend"


def test_b14_extend_rollback_restores_sealed_file(
    session_env: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """B-14: DB rollback on succession extend restores prior sealed transcript bytes."""
    from cortex_store.routes import session_close_persist

    db_path = session_env["db_path"]
    files_root = session_env["files_root"]
    transcripts_root = session_env["transcripts_root"]
    jsonl = transcripts_root / _UUID / f"{_UUID}.jsonl"
    stamps_10 = [f"2026-09-07T12:{i:02d}:00+00:00" for i in range(10)]
    _write_jsonl(jsonl, stamps_10)
    rel = f"{_UUID}/{_UUID}.jsonl"

    first = _op_transcript_seal(thread="10223", jsonl_path=rel)
    assert "error" not in first, first
    sealed_sid = first["session_id"]
    tx_path = files_root / f"notes/system/transcripts/{sealed_sid}.md"
    prior_bytes = tx_path.read_bytes()
    assert len(prior_bytes) > 0

    stamps_20 = stamps_10 + [f"2026-09-07T13:{i:02d}:00+00:00" for i in range(10)]
    _write_jsonl(jsonl, stamps_20)

    call_count = 0
    original_json_encode = session_close_persist.json_encode

    def _fail_on_extend(*args: object, **kwargs: object) -> str:
        nonlocal call_count
        call_count += 1
        if call_count >= 2:
            raise RuntimeError("extend journal update failed")
        return original_json_encode(*args, **kwargs)

    monkeypatch.setattr(session_close_persist, "json_encode", _fail_on_extend)

    with pytest.raises(RuntimeError, match="extend journal update failed"):
        _op_transcript_seal(thread="10223", jsonl_path=rel)

    assert tx_path.read_bytes() == prior_bytes

    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT closed_by FROM session_journals WHERE session_id = ?",
            (sealed_sid,),
        ).fetchone()
        assert row is not None
        assert row[0] == "succession"
    finally:
        conn.close()
    assert _journal_count(db_path) == 1
