"""Projected messages JSON discovery, skips, and the hard chunk cap."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from services.rag.chunkers import chunk_file
from services.rag.chunkers._sizing import CHUNK_HARD_CAP_BYTES
from services.rag.chunkers.messages_chunker import indexing_source_hash
from services.rag.chunkers.paragraph_utils import _split_paragraphs, _word_split
from services.rag.chunkers.projected_messages import log_skip, reset_skip_log

pytestmark = pytest.mark.offline


@pytest.fixture(autouse=True)
def _clear_skip_log() -> None:
    reset_skip_log()


def _cap(text: str) -> int:
    return len(text.encode("utf-8"))


def _assert_capped(chunks: list) -> None:
    assert chunks
    for chunk in chunks:
        assert _cap(chunk.text) <= CHUNK_HARD_CAP_BYTES


def test_per_source_fixtures(tmp_path: Path) -> None:
    cursor = tmp_path / "cursor.json"
    cursor.write_text(
        json.dumps(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": "<user_query>hello cursor</user_query>",
                    },
                    {"role": "assistant", "content": "cursor reply"},
                ]
            }
        ),
        encoding="utf-8",
    )
    claude = tmp_path / "claude.json"
    claude.write_text(
        json.dumps(
            [
                {"role": "user", "content": "hello claude"},
                {"role": "assistant", "content": "claude reply"},
            ]
        ),
        encoding="utf-8",
    )
    grok = tmp_path / "grok.json"
    grok.write_text(
        json.dumps(
            {
                "meta": {"observed_at": "2026-10-18"},
                "messages": [
                    {"role": "user", "content": "hello grok"},
                    {"role": "assistant", "content": "grok reply"},
                ],
            }
        ),
        encoding="utf-8",
    )
    for path, needle in (
        (cursor, "hello cursor"),
        (claude, "hello claude"),
        (grok, "hello grok"),
    ):
        chunks = chunk_file(path)
        _assert_capped(chunks)
        assert needle in chunks[0].text


def test_tape_raw_and_render_skip(tmp_path: Path) -> None:
    tape = tmp_path / "tape.json"
    tape.write_text(
        json.dumps({"cells": [{"cp_ordinal": 1}], "index": [], "open_line": {}}),
        encoding="utf-8",
    )
    raw = tmp_path / "snap.raw.json"
    raw.write_text(
        json.dumps(
            {
                "messages": [
                    {"role": "user", "content": "u"},
                    {"role": "assistant", "content": "a"},
                ]
            }
        ),
        encoding="utf-8",
    )
    unsealed = tmp_path / "unsealed.json"
    unsealed.write_text(
        json.dumps(
            {
                "meta": {"provenance": "raw"},
                "messages": [
                    {"role": "user", "content": "u"},
                    {"role": "assistant", "content": "a"},
                ],
            }
        ),
        encoding="utf-8",
    )
    render = tmp_path / "notes" / "system" / "transcripts" / "session-close.md"
    render.parent.mkdir(parents=True)
    render.write_text("Session closed.\n\nNo turn headings here.\n", encoding="utf-8")
    fine = tmp_path / "fine.json"
    fine.write_text(
        json.dumps([{"messages": [{"role": "user", "content": "q"}]}]),
        encoding="utf-8",
    )
    assert chunk_file(tape) == []
    assert chunk_file(raw) == []
    assert chunk_file(unsealed) == []
    assert chunk_file(render) == []
    assert chunk_file(fine) == []


def test_fake_heading_stays_in_body(tmp_path: Path) -> None:
    path = tmp_path / "quoted.json"
    path.write_text(
        json.dumps(
            {
                "messages": [
                    {"role": "user", "content": "first", "turn_index": 1},
                    {
                        "role": "assistant",
                        "content": "body\n\n## Turn 9 - fake - user\n\nstill body",
                        "turn_index": 2,
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    chunks = chunk_file(path)
    _assert_capped(chunks)
    assert not any(chunk.text.startswith("## Turn 9") for chunk in chunks)
    assert any("Turn 9 - fake" in chunk.text for chunk in chunks)


def test_two_megabyte_turn_and_tool_result_stay_under_cap(tmp_path: Path) -> None:
    prose = tmp_path / "prose.json"
    prose.write_text(
        json.dumps(
            {
                "messages": [
                    {"role": "user", "content": "go"},
                    {"role": "assistant", "content": ("word " * 400_000)},
                ]
            }
        ),
        encoding="utf-8",
    )
    tool = tmp_path / "tool.json"
    tool.write_text(
        json.dumps(
            {
                "messages": [
                    {"role": "user", "content": "run"},
                    {
                        "role": "assistant",
                        "content": "```tool\n" + ("x" * 2_000_000) + "\n```",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    prose_chunks = chunk_file(prose)
    tool_chunks = chunk_file(tool)
    _assert_capped(prose_chunks)
    _assert_capped(tool_chunks)
    assert len(prose_chunks) > 1
    assert len(tool_chunks) > 1
    assert any("(part " in chunk.text for chunk in tool_chunks)
    for chunk in tool_chunks:
        assert chunk.text.count("```") % 2 == 0


def test_giant_token_is_cut() -> None:
    pieces = _word_split("a" * 20_000, 100)
    assert len(pieces) > 1
    assert all(len(piece.encode("utf-8")) <= 100 for piece in pieces)
    carry = "w " * 3000
    paras = _split_paragraphs(f"{carry}\n\n{carry}\n\n{'z' * 8000}", 1000, 100)
    assert paras
    assert all(len(text.encode("utf-8")) <= CHUNK_HARD_CAP_BYTES for text, _ in paras)


def test_policy_version_changes_hash(tmp_path: Path) -> None:
    transcript = tmp_path / "notes" / "system" / "transcripts" / "a.json"
    transcript.parent.mkdir(parents=True)
    transcript.write_bytes(b"{}")
    raw = transcript.read_bytes()
    hashed = indexing_source_hash(raw, transcript)
    plain = hashlib.sha256(raw).hexdigest()
    assert hashed != plain
    other = tmp_path / "paper.pdf"
    other.write_bytes(raw)
    assert indexing_source_hash(raw, other) == plain


def test_skip_log_dedupes(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    path = tmp_path / "skip.json"
    path.write_text("{}", encoding="utf-8")
    mtime = path.stat().st_mtime_ns
    with caplog.at_level("INFO", logger="services.rag.chunkers.projected_messages"):
        log_skip(path, mtime, "unsupported_shape")
        log_skip(path, mtime, "unsupported_shape")
    lines = [
        rec.message
        for rec in caplog.records
        if rec.message.startswith("rag_chunk_skip ")
    ]
    assert lines == [f"rag_chunk_skip path={path} reason=unsupported_shape"]
