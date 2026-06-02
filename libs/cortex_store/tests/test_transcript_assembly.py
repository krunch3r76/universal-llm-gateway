"""Unit tests for `cortex_store.transcript_assembly`."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from cortex_store import transcript_assembly as ta
from cortex_store.transcript_assembly import (
    TranscriptPathError,
    assemble_verbatim_md,
    compose_full_transcript,
    compute_text_content_hash,
    derive_session_id_from_jsonl_start,
    resolve_jsonl_path,
    session_id_timing_hint,
)


def _write_jsonl(path: Path, records: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec) + "\n")


@pytest.fixture()
def transcripts_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "agent-transcripts"
    root.mkdir()
    monkeypatch.setenv("CURSOR_AGENT_TRANSCRIPTS_ROOT", str(root))
    return root


def test_resolve_jsonl_path_accepts_file_under_root(transcripts_root: Path) -> None:
    uuid_dir = transcripts_root / "abc-uuid"
    uuid_dir.mkdir()
    jsonl = uuid_dir / "abc-uuid.jsonl"
    jsonl.write_text("{}\n")
    resolved = resolve_jsonl_path(str(jsonl))
    assert resolved == jsonl.resolve()


def test_resolve_jsonl_path_relative_to_root(transcripts_root: Path) -> None:
    uuid_dir = transcripts_root / "xyz"
    uuid_dir.mkdir()
    jsonl = uuid_dir / "xyz.jsonl"
    jsonl.write_text("{}\n")
    resolved = resolve_jsonl_path("xyz/xyz.jsonl")
    assert resolved == jsonl.resolve()


def test_resolve_jsonl_path_rejects_outside_root(
    transcripts_root: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "elsewhere.jsonl"
    outside.write_text("{}\n")
    with pytest.raises(TranscriptPathError, match="outside"):
        resolve_jsonl_path(str(outside))


def test_resolve_jsonl_path_rejects_traversal(transcripts_root: Path) -> None:
    with pytest.raises(TranscriptPathError, match="outside"):
        resolve_jsonl_path("../etc/passwd")


def test_resolve_jsonl_path_rejects_missing(transcripts_root: Path) -> None:
    with pytest.raises(TranscriptPathError, match="not found"):
        resolve_jsonl_path("missing/missing.jsonl")


def test_resolve_jsonl_path_rejects_empty() -> None:
    with pytest.raises(TranscriptPathError, match="required"):
        resolve_jsonl_path("")


def test_assemble_verbatim_md_basic(transcripts_root: Path) -> None:
    uuid_dir = transcripts_root / "uuid-1"
    uuid_dir.mkdir()
    jsonl = uuid_dir / "uuid-1.jsonl"
    _write_jsonl(
        jsonl,
        [
            {
                "role": "user",
                "message": {"content": [{"type": "text", "text": "Hello there"}]},
            },
            {
                "role": "assistant",
                "message": {
                    "content": [
                        {"type": "text", "text": "Hi back"},
                        {"type": "tool_use", "name": "fs"},
                    ]
                },
            },
            {
                "role": "user",
                "message": {"content": [{"type": "text", "text": "Second turn"}]},
            },
            {
                "role": "assistant",
                "message": {"content": [{"type": "text", "text": "Done"}]},
            },
        ],
    )
    verbatim_md, turn_count = assemble_verbatim_md(
        jsonl_path=jsonl, session_id="cursor-2026-05-16-1820"
    )
    assert turn_count == 2
    assert verbatim_md.startswith("# Transcript: cursor-2026-05-16-1820\n")
    assert "## Turn 1 — Hello there" in verbatim_md
    assert "### User\n\nHello there" in verbatim_md
    assert "### Assistant\n\nHi back\n\n[tool call: fs]" in verbatim_md
    assert "## Turn 2 — Second turn" in verbatim_md


def test_assemble_verbatim_md_merges_tool_result_user_records(
    transcripts_root: Path,
) -> None:
    """User records with only `tool_result` blocks must not open a new turn."""
    uuid_dir = transcripts_root / "uuid-2"
    uuid_dir.mkdir()
    jsonl = uuid_dir / "uuid-2.jsonl"
    _write_jsonl(
        jsonl,
        [
            {
                "role": "user",
                "message": {"content": [{"type": "text", "text": "Run it"}]},
            },
            {
                "role": "assistant",
                "message": {"content": [{"type": "tool_use", "name": "shell"}]},
            },
            {
                "role": "user",
                "message": {
                    "content": [
                        {"type": "tool_result", "tool_use_id": "x", "content": "ok"}
                    ]
                },
            },
            {
                "role": "assistant",
                "message": {"content": [{"type": "text", "text": "Done"}]},
            },
        ],
    )
    verbatim_md, turn_count = assemble_verbatim_md(
        jsonl_path=jsonl, session_id="cursor-2026-05-16-1820"
    )
    assert turn_count == 1


def test_compose_full_transcript_concatenates_layers() -> None:
    verbatim = "# Transcript: cursor-2026-05-16-1820\n\n## Turn 1\n### User\nhi\n"
    summary = "## Session Summary\n\n**Decisions:** none\n**Journal:** cursor-2026-05-16-1820\n"
    full = compose_full_transcript(verbatim, summary)
    assert full.startswith("# Transcript:")
    assert "## Session Summary" in full
    assert full.endswith("\n")


def test_compute_text_content_hash_format() -> None:
    h = compute_text_content_hash("hello")
    assert h.startswith("sha256:")
    assert len(h) == len("sha256:") + 64


def test_compute_text_content_hash_deterministic() -> None:
    assert compute_text_content_hash("x") == compute_text_content_hash("x")
    assert compute_text_content_hash("x") != compute_text_content_hash("y")


def test_transcripts_root_default_when_env_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CURSOR_AGENT_TRANSCRIPTS_ROOT", raising=False)
    assert ta._transcripts_root().name == "agent-transcripts"


def test_derive_session_id_from_jsonl_start_uses_mtime(
    transcripts_root: Path,
) -> None:
    jsonl = transcripts_root / "uuid" / "uuid.jsonl"
    jsonl.parent.mkdir(parents=True)
    jsonl.write_text("{}\n")
    derived = derive_session_id_from_jsonl_start(jsonl_path=jsonl, agent="cursor")
    assert re.fullmatch(r"cursor-\d{4}-\d{2}-\d{2}-\d{4}", derived)


def test_session_id_timing_hint_when_ids_differ(transcripts_root: Path) -> None:
    jsonl = transcripts_root / "u" / "u.jsonl"
    jsonl.parent.mkdir(parents=True)
    jsonl.write_text("{}\n")
    from_jsonl = derive_session_id_from_jsonl_start(jsonl_path=jsonl, agent="cursor")
    assert session_id_timing_hint(
        session_id="cursor-2099-12-31-2359",
        jsonl_path=jsonl,
        agent="cursor",
    )
    assert (
        session_id_timing_hint(
            session_id=from_jsonl,
            jsonl_path=jsonl,
            agent="cursor",
        )
        is None
    )
