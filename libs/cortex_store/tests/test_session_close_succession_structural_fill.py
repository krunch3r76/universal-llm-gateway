"""Tests for A-1 succession structural fill routing (I5)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException

from cortex_store.dispatch_ops import ops_journals
from cortex_store.dispatch_ops.ops_transcript_seal import _op_transcript_seal
from cortex_store.session_close_successor_hop import (
    SUCCESSION_FILL_REASON,
    SealedJournal,
)

pytestmark = pytest.mark.offline


@pytest.fixture(autouse=True)
def _patch_explicit_uuids(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "cortex_store.dispatch_ops.ops_transcript_discover.explicit_uuids_for_lane",
        lambda _thread, extra=None: set(extra or ()),
    )


_UUID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
_START = "2026-09-07T10:00:00+00:00"
_LID = "2026-09-07T11:00:00Z"


def _summary(text: str) -> str:
    return f"## Session Summary\n\n**Decisions:** {text}\n**Open items:** None.\n"


def _write_jsonl(path: Path, stamps: list[str]) -> None:
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
                            "text": f"<timestamp>{stamp}</timestamp>\nContinue.",
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
                        {"type": "text", "text": "Ack."},
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
        row = conn.execute("SELECT COUNT(*) FROM session_journals").fetchone()
        return int(row[0])
    finally:
        conn.close()


def test_succession_fill_before_post_lid_hop(tmp_path) -> None:
    from cortex_store.session_close_successor_hop import resolve_successor_hop as rsh

    jsonl = tmp_path / "uuid" / "uuid.jsonl"
    jsonl.parent.mkdir(parents=True)
    jsonl.write_text("", encoding="utf-8")

    with PatchLookup(
        {
            "sealed-id": SealedJournal(
                session_id="sealed-id",
                journal_row_id=2,
                timestamp="2026-09-07T11:00:00Z",
                prior_session_id=None,
                closed_by="succession",
            ),
        }
    ):
        hop = rsh(
            supplied_session_id="boot-id",
            jsonl_start_id="sealed-id",
            jsonl_path=jsonl,
            agent="cursor",
        )
    assert hop is not None
    assert hop.hop_reason == SUCCESSION_FILL_REASON
    assert hop.session_id == "sealed-id"


def test_i5_preflight_fill_then_human_close(session_env: dict[str, Path]) -> None:
    """I5: succession seal → preflight succession_fill → structural human close."""
    db_path = session_env["db_path"]
    transcripts_root = session_env["transcripts_root"]
    jsonl = transcripts_root / _UUID / f"{_UUID}.jsonl"
    _write_jsonl(jsonl, [_START, "2026-09-07T10:30:00+00:00"])

    rel = f"{_UUID}/{_UUID}.jsonl"
    seal = _op_transcript_seal(thread="10223", jsonl_path=rel)
    assert "error" not in seal, seal
    sealed_sid = seal["session_id"]
    assert _journal_count(db_path) == 1

    preflight = ops_journals._op_session_close_preflight(
        session_id=sealed_sid,
        agent="cursor",
        transcript_jsonl_path=rel,
        session_summary_md=_summary("Structural fill after succession harvest."),
        summary="Structural fill after succession harvest.",
        transcript_depth="verbatim",
    )
    assert preflight.get("ok") is True, preflight
    assert preflight.get("hop_reason") == SUCCESSION_FILL_REASON
    assert preflight["session_id"] == sealed_sid

    fill = ops_journals._op_session_close(
        session_id=sealed_sid,
        agent="cursor",
        transcript_jsonl_path=rel,
        session_summary_md=_summary("Real human structural fill decisions."),
        summary="Real human structural fill decisions.",
        transcript_depth="verbatim",
        decisions=["Filled structural layer after succession seal."],
    )
    assert "error" not in fill, fill
    assert fill["journal_row_id"] == seal["journal_row_id"]
    assert _journal_count(db_path) == 1

    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT closed_by FROM session_journals WHERE session_id = ?",
            (sealed_sid,),
        ).fetchone()
        assert row is not None
        assert row[0] == "cursor"
    finally:
        conn.close()


def test_i5_r1b_wrong_close_409(session_env: dict[str, Path]) -> None:
    """I5 R1b: non-fill close on uuid awaiting succession fill ⇒ 409."""
    transcripts_root = session_env["transcripts_root"]
    jsonl = transcripts_root / _UUID / f"{_UUID}.jsonl"
    _write_jsonl(jsonl, [_START])
    rel = f"{_UUID}/{_UUID}.jsonl"

    seal = _op_transcript_seal(thread="10223", jsonl_path=rel)
    assert "error" not in seal, seal
    sealed_sid = seal["session_id"]

    from cortex_store.models import SessionCloseRequest
    from cortex_store.routes.session_close_validate import validate_session_close

    wrong_sid = "cursor-2026-09-07-130000-b01"
    assert wrong_sid != sealed_sid
    body = SessionCloseRequest(
        session_id=wrong_sid,
        agent="cursor",
        session_summary_md=_summary("Wrong close should be refused."),
        summary="Wrong close should be refused.",
        transcript_jsonl_path=rel,
        transcript_depth="verbatim",
    )
    with pytest.raises(HTTPException) as exc:
        validate_session_close(body)
    assert exc.value.status_code == 409
    detail = exc.value.detail
    assert isinstance(detail, dict)
    assert detail.get("reason") == "session.succession_fill_required"
    assert detail.get("expected") == sealed_sid


def test_i5_succession_fill_light_depth_with_jsonl(session_env: dict[str, Path]) -> None:
    """I5/R2: light depth + jsonl on succession fill ⇒ verbatim archival, file_path kept."""
    db_path = session_env["db_path"]
    files_root = session_env["files_root"]
    transcripts_root = session_env["transcripts_root"]
    jsonl = transcripts_root / _UUID / f"{_UUID}.jsonl"
    _write_jsonl(jsonl, [_START, "2026-09-07T10:30:00+00:00"])
    rel = f"{_UUID}/{_UUID}.jsonl"

    seal = _op_transcript_seal(thread="10223", jsonl_path=rel)
    assert "error" not in seal, seal
    sealed_sid = seal["session_id"]
    prior_path = files_root / f"notes/system/transcripts/{sealed_sid}.md"
    assert prior_path.is_file()

    fill = ops_journals._op_session_close(
        session_id=sealed_sid,
        agent="cursor",
        transcript_jsonl_path=rel,
        session_summary_md=_summary("Light-declared fill still archives verbatim."),
        summary="Light-declared fill still archives verbatim.",
        transcript_depth="light",
        decisions=["Structural fill under light depth declaration."],
    )
    assert "error" not in fill, fill
    assert fill["transcript_depth"] == "verbatim"
    assert fill["transcript_path"] is not None
    assert fill["turn_count"] >= 1

    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT file_path, closed_by FROM session_journals WHERE session_id = ?",
            (sealed_sid,),
        ).fetchone()
        assert row is not None
        assert row[0] is not None
        assert row[1] == "cursor"
    finally:
        conn.close()
    assert prior_path.is_file()
    assert _journal_count(db_path) == 1


def test_i5_succession_fill_light_depth_without_jsonl(
    session_env: dict[str, Path],
) -> None:
    """R2 SPLICE: light depth succession fill without JSONL splices structural layer."""
    db_path = session_env["db_path"]
    files_root = session_env["files_root"]
    transcripts_root = session_env["transcripts_root"]
    jsonl = transcripts_root / _UUID / f"{_UUID}.jsonl"
    _write_jsonl(jsonl, [_START, "2026-09-07T10:30:00+00:00"])
    rel = f"{_UUID}/{_UUID}.jsonl"

    seal = _op_transcript_seal(thread="10223", jsonl_path=rel)
    assert "error" not in seal, seal
    sealed_sid = seal["session_id"]
    prior_path = files_root / f"notes/system/transcripts/{sealed_sid}.md"
    assert prior_path.is_file()

    fill = ops_journals._op_session_close(
        session_id=sealed_sid,
        agent="cursor",
        session_summary_md=_summary("SPLICE fill without JSONL path."),
        summary="SPLICE fill without JSONL path on light depth.",
        transcript_depth="light",
        decisions=["Structural fill via SPLICE without JSONL."],
    )
    assert "error" not in fill, fill
    assert fill["transcript_depth"] == "light"
    assert fill["transcript_path"] is not None
    assert prior_path.is_file()
    text = prior_path.read_text(encoding="utf-8")
    assert "## Turn" in text
    assert "SPLICE fill without JSONL path." in text

    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT file_path, closed_by, verbatim_sha256, verbatim_bytes "
            "FROM session_journals WHERE session_id = ?",
            (sealed_sid,),
        ).fetchone()
        assert row is not None
        assert row[0] is not None
        assert row[1] == "cursor"
        assert row[2] is not None
        assert row[3] is not None and row[3] > 0
    finally:
        conn.close()
    assert _journal_count(db_path) == 1


def test_b13_splice_preserves_verbatim_with_session_summary_heading(
    session_env: dict[str, Path],
) -> None:
    """B-13: user turn containing '## Session Summary' survives SPLICE fill."""
    db_path = session_env["db_path"]
    files_root = session_env["files_root"]
    transcripts_root = session_env["transcripts_root"]
    jsonl = transcripts_root / _UUID / f"{_UUID}.jsonl"
    collision_text = (
        "<timestamp>2026-09-07T10:00:00+00:00</timestamp>\n"
        "Please review this heading:\n## Session Summary\nNot the structural layer."
    )
    path = jsonl
    path.parent.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = [
        {
            "role": "user",
            "message": {"content": [{"type": "text", "text": collision_text}]},
        },
        {
            "role": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": "Ack."},
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
        },
    ]
    with path.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec) + "\n")
    rel = f"{_UUID}/{_UUID}.jsonl"

    seal = _op_transcript_seal(thread="10223", jsonl_path=rel)
    assert "error" not in seal, seal
    sealed_sid = seal["session_id"]
    prior_path = files_root / f"notes/system/transcripts/{sealed_sid}.md"
    assert prior_path.is_file()
    sealed_full = prior_path.read_text(encoding="utf-8")
    assert "## Session Summary" in sealed_full
    assert "Not the structural layer." in sealed_full

    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT verbatim_bytes FROM session_journals WHERE session_id = ?",
            (sealed_sid,),
        ).fetchone()
        assert row is not None
        sealed_vbytes = int(row[0])
        assert sealed_vbytes > 0
    finally:
        conn.close()
    sealed_verbatim_prefix = sealed_full.encode("utf-8")[:sealed_vbytes]

    fill = ops_journals._op_session_close(
        session_id=sealed_sid,
        agent="cursor",
        session_summary_md=_summary("SPLICE after heading collision."),
        summary="SPLICE fill preserves verbatim with embedded heading.",
        transcript_depth="light",
        decisions=["Structural fill via SPLICE with heading collision."],
    )
    assert "error" not in fill, fill

    after_full = prior_path.read_text(encoding="utf-8")
    after_verbatim_prefix = after_full.encode("utf-8")[:sealed_vbytes]
    assert after_verbatim_prefix == sealed_verbatim_prefix, (
        "SPLICE fill must preserve sealed verbatim byte-identity; "
        "marker split would truncate at embedded heading."
    )
    assert "SPLICE after heading collision." in after_full
    assert "Not the structural layer." in after_full

    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT verbatim_bytes FROM session_journals WHERE session_id = ?",
            (sealed_sid,),
        ).fetchone()
        assert row is not None
        assert row[0] == sealed_vbytes
    finally:
        conn.close()


def test_i5_succession_fill_none_depth_without_jsonl(
    session_env: dict[str, Path],
) -> None:
    """R2: none depth succession fill keeps file_path when sealed verbatim exists."""
    files_root = session_env["files_root"]
    transcripts_root = session_env["transcripts_root"]
    jsonl = transcripts_root / _UUID / f"{_UUID}.jsonl"
    _write_jsonl(jsonl, [_START])
    rel = f"{_UUID}/{_UUID}.jsonl"

    seal = _op_transcript_seal(thread="10223", jsonl_path=rel)
    assert "error" not in seal, seal
    sealed_sid = seal["session_id"]
    prior_path = files_root / f"notes/system/transcripts/{sealed_sid}.md"
    assert prior_path.is_file()

    fill = ops_journals._op_session_close(
        session_id=sealed_sid,
        agent="cursor",
        session_summary_md=_summary("None-depth SPLICE keeps sealed file."),
        summary="None-depth SPLICE keeps sealed file on disk.",
        transcript_depth="none",
        decisions=["Structural fill at none depth without JSONL."],
    )
    assert "error" not in fill, fill
    assert fill["transcript_depth"] == "none"
    assert fill["transcript_path"] is not None
    assert prior_path.is_file()


class PatchLookup:
    def __init__(self, mapping: dict[str, SealedJournal | None]) -> None:
        self.mapping = mapping

    def __enter__(self):
        from unittest.mock import patch

        self._p = patch(
            "cortex_store.session_close_successor_hop.latest_journal_in_chain",
            side_effect=lambda sid: self.mapping.get(sid),
        )
        return self._p.__enter__()

    def __exit__(self, *args):
        return self._p.__exit__(*args)
