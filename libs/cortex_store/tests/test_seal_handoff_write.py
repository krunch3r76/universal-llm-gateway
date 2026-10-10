"""Session-close writes the handoff letter onto the seal file."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from continuity_tape.handoff import handoff_binding_stamped
from continuity_tape.messages import seal_messages_sha256

from cortex_store.dispatch_ops.ops_session_close import _op_session_close
from cortex_store.dispatch_ops.ops_transcript_seal import _op_transcript_seal

pytestmark = pytest.mark.offline

_UUID = "c3d4e5f6-a7b8-9012-cdef-123456789abc"
_LETTER = {
    "author": "cursor",
    "mission": "prove the letter",
    "where_we_left_off": "the seal file",
    "state": "open",
    "next_acts": ["stamp the file"],
    "load_bearing": ["the seal file"],
}


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
    path.write_text(
        "".join(json.dumps(rec) + "\n" for rec in records), encoding="utf-8"
    )


def _seal(files_root: Path, session_id: str) -> dict[str, Any]:
    path = files_root / "notes" / "system" / "seals" / f"{session_id}.messages.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _assert_stamped(stored: dict[str, Any], *, session_id: str, house: str, surface: str) -> None:
    letter = stored["handoff"]
    messages = stored["messages"]
    meta_sha = stored["meta"]["messages_sha256"]
    assert letter["author"] == _LETTER["author"]
    assert letter["mission"] == _LETTER["mission"]
    assert letter["where_we_left_off"] == _LETTER["where_we_left_off"]
    assert letter["state"] == _LETTER["state"]
    assert letter["next_acts"] == _LETTER["next_acts"]
    assert letter["load_bearing"] == _LETTER["load_bearing"]
    assert letter["session_id"] == session_id
    assert letter["messages_sha256"] == meta_sha
    assert len(letter["messages_sha256"]) == 64
    assert letter["as_of_message_index"] == len(messages)
    assert letter["house"] == house
    assert letter["surface"] == surface
    assert isinstance(letter["turns_at_seal"], int)
    assert letter["turns_at_seal"] >= 1
    if stored["meta"].get("turn_count") is not None:
        assert letter["turns_at_seal"] == stored["meta"]["turn_count"]
    assert handoff_binding_stamped(
        letter,
        messages=messages,
        messages_sha256=meta_sha,
        session_id=session_id,
    ) == ("bound", [])


def _messages(n: int) -> list[dict[str, Any]]:
    return [
        {
            "role": "user" if i % 2 == 0 else "assistant",
            "content": f"m{i} " + ("word " * 40),
            "turn_index": i + 1,
        }
        for i in range(n)
    ]


def _envelope(messages: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema": "ulg.continuity.messages/1",
        "messages": messages,
        "meta": {
            "provenance": "transcribed",
            "coverage": "full",
            "surface": "claude_ai",
            "transcript_id": "cse_0181xcjbYP83D8VdBopSyiLs",
            "messages_sha256": seal_messages_sha256(messages),
            "sources": [
                {
                    "kind": "dom",
                    "producer": {"id": "web-anthropic", "kind": "seat", "version": "1"},
                }
            ],
        },
    }


def test_cursor_seal_stamps_letter(session_env: dict[str, Path]) -> None:
    root = session_env["transcripts_root"]
    files = session_env["files_root"]
    jsonl = root / _UUID / f"{_UUID}.jsonl"
    stamps = ["2026-10-09T18:00:00+00:00", "2026-10-09T18:01:00+00:00"]
    _write_jsonl(jsonl, stamps)
    rel = f"{_UUID}/{_UUID}.jsonl"
    result = _op_transcript_seal(
        thread="10223", jsonl_path=rel, handoff=dict(_LETTER), surface="cursor"
    )
    assert result.get("error") is None, result
    sid = str(result["session_id"])
    stored = _seal(files, sid)
    _assert_stamped(stored, session_id=sid, house="agent-bus:10223", surface="cursor")


def test_messages_envelope_stamps_letter(session_env: dict[str, Path]) -> None:
    files = session_env["files_root"]
    sid = "web-anthropic-2026-10-09-180000-a1b"
    messages = _messages(2)
    result = _op_session_close(
        session_id=sid,
        agent="web-anthropic",
        session_summary_md="## Session Summary\n\nLetter on the messages envelope. " * 3,
        summary="letter on the messages envelope",
        transcript_messages=_envelope(messages),
        transcript_depth="verbatim",
        entity_ids=["agent-bus:10479"],
        closed_by="succession",
        succession_seal_authority=True,
        handoff=dict(_LETTER),
        stamp_thread="10479",
        stamp_surface="claude_ai",
        stamp_transcript_id="cse_0181xcjbYP83D8VdBopSyiLs",
    )
    assert result.get("error") is None, result
    stored = _seal(files, sid)
    _assert_stamped(
        stored, session_id=sid, house="agent-bus:10479", surface="claude_ai"
    )


def test_extend_carries_letter_stale(session_env: dict[str, Path]) -> None:
    root = session_env["transcripts_root"]
    files = session_env["files_root"]
    jsonl = root / _UUID / f"{_UUID}.jsonl"
    stamps = ["2026-10-09T18:00:00+00:00", "2026-10-09T18:01:00+00:00"]
    _write_jsonl(jsonl, stamps)
    rel = f"{_UUID}/{_UUID}.jsonl"
    first = _op_transcript_seal(
        thread="10223", jsonl_path=rel, handoff=dict(_LETTER), surface="cursor"
    )
    assert first.get("error") is None, first
    sid = str(first["session_id"])
    prior = _seal(files, sid)["handoff"]
    _write_jsonl(
        jsonl,
        stamps + ["2026-10-09T18:02:00+00:00", "2026-10-09T18:03:00+00:00"],
    )
    extended = _op_transcript_seal(thread="10223", jsonl_path=rel, surface="cursor")
    assert extended.get("error") is None, extended
    stored = _seal(files, sid)
    assert stored["handoff"]["mission"] == prior["mission"]
    assert stored["handoff"]["author"] == prior["author"]
    status, reasons = handoff_binding_stamped(
        stored["handoff"],
        messages=stored["messages"],
        messages_sha256=stored["meta"]["messages_sha256"],
        session_id=sid,
    )
    assert status == "stale"
    assert "messages_sha256" in reasons or "as_of_message_index" in reasons


def test_already_closed_letter_does_not_rewrite(session_env: dict[str, Path]) -> None:
    root = session_env["transcripts_root"]
    files = session_env["files_root"]
    jsonl = root / _UUID / f"{_UUID}.jsonl"
    stamps = ["2026-10-09T18:00:00+00:00"]
    _write_jsonl(jsonl, stamps)
    rel = f"{_UUID}/{_UUID}.jsonl"
    first = _op_transcript_seal(thread="10223", jsonl_path=rel, surface="cursor")
    assert first.get("error") is None, first
    sid = str(first["session_id"])
    path = files / "notes" / "system" / "seals" / f"{sid}.messages.json"
    before = path.read_bytes()
    again = _op_transcript_seal(
        thread="10223", jsonl_path=rel, handoff=dict(_LETTER), surface="cursor"
    )
    assert again.get("letter_status") == "dropped_already_closed"
    assert path.read_bytes() == before


def test_no_letter_omits_handoff_key(session_env: dict[str, Path]) -> None:
    root = session_env["transcripts_root"]
    files = session_env["files_root"]
    jsonl = root / _UUID / f"{_UUID}.jsonl"
    _write_jsonl(jsonl, ["2026-10-09T18:00:00+00:00"])
    result = _op_transcript_seal(
        thread="10223", jsonl_path=f"{_UUID}/{_UUID}.jsonl", surface="cursor"
    )
    assert result.get("error") is None, result
    stored = _seal(files, str(result["session_id"]))
    assert "handoff" not in stored
