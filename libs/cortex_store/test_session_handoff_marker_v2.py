"""2-A v2 marker extraction and close-contract tests (agent-bus 1188)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi import HTTPException

from cortex_store import db
from cortex_store.dispatch_ops import ops_journals, ops_session_close
from cortex_store.routes import session_journals
from cortex_store.test_session_close_handoff import _install_schema
from cortex_store.session_handoff import (
    DERIVATION_SECTION,
    DERIVATION_SECTION_AMBIGUOUS,
    DERIVATION_SECTION_UNRESOLVED,
    DERIVATION_DETACHED_STRING,
    extract_handoff_marker_region,
    read_handoff_source_file,
    resolve_handoff_for_write,
    handoff_dry_run_preview,
)


def test_unlabeled_marker_extracts_literal_body() -> None:
    text = (
        "# doc\n"
        "<!-- handoff:start -->\n"
        "line one\n"
        "line two\n"
        "<!-- handoff:end -->\n"
        "tail\n"
    )
    result = extract_handoff_marker_region(text, None)
    assert result.status == "ok"
    assert result.pair_count == 1
    assert result.body == "line one\nline two"


def test_labeled_marker_pair() -> None:
    text = (
        "<!-- handoff:agent-b:start -->\nlabeled body\n<!-- handoff:agent-b:end -->\n"
    )
    result = extract_handoff_marker_region(text, "agent-b")
    assert result.status == "ok"
    assert result.body == "labeled body"


def test_zero_pairs_unresolved() -> None:
    assert extract_handoff_marker_region("# no markers\n", None).status == "unresolved"


def test_multiple_pairs_ambiguous() -> None:
    text = (
        "<!-- handoff:start -->\n"
        "a\n"
        "<!-- handoff:end -->\n"
        "<!-- handoff:start -->\n"
        "b\n"
        "<!-- handoff:end -->\n"
    )
    result = extract_handoff_marker_region(text, None)
    assert result.status == "ambiguous"
    assert result.pair_count == 2


def test_unbalanced_start_unresolved() -> None:
    text = "<!-- handoff:start -->\nbody only\n"
    assert extract_handoff_marker_region(text, None).status == "unresolved"


def test_whitespace_only_body_unresolved() -> None:
    text = "<!-- handoff:start -->\n   \n<!-- handoff:end -->\n"
    assert extract_handoff_marker_region(text, None).status == "unresolved"


def test_fenced_line_with_marker_text_not_counted() -> None:
    text = (
        "```md\n"
        "<!-- handoff:start -->\n"
        "```\n"
        "<!-- handoff:start -->\n"
        "real\n"
        "<!-- handoff:end -->\n"
    )
    result = extract_handoff_marker_region(text, None)
    assert result.status == "ok"
    assert result.body == "real"


def test_expected_handoff_prompt_mismatch_409(tmp_path: Path) -> None:
    src = tmp_path / "h.md"
    src.write_text(
        "<!-- handoff:start -->\nderived\n<!-- handoff:end -->\n",
        encoding="utf-8",
    )
    with pytest.raises(HTTPException) as exc:
        resolve_handoff_for_write(
            files_root=tmp_path,
            write_path="session_close",
            written_at="2026-06-03T00:00:00Z",
            handoff_source_path="h.md",
            handoff_source_section=None,
            handoff_prompt=None,
            expected_handoff_prompt="wrong",
        )
    assert exc.value.status_code == 409


def test_sandbox_escape_hard_fail(tmp_path: Path) -> None:
    outside = tmp_path.parent / "escape.md"
    outside.write_text("x", encoding="utf-8")
    with pytest.raises(HTTPException) as exc:
        read_handoff_source_file(tmp_path, "../escape.md")
    assert exc.value.status_code == 422
    detail = exc.value.detail
    assert isinstance(detail, dict)
    assert detail["reason"] == "handoff_source_path.sandbox_escape"


@pytest.fixture()
def handoff_close_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> dict[str, Path]:
    db_path = tmp_path / "cortex.db"
    files_root = tmp_path / "files"
    files_root.mkdir(parents=True)
    transcripts_root = tmp_path / "agent-transcripts"
    transcripts_root.mkdir()
    _install_schema(db_path)
    monkeypatch.setattr(db, "_CORTEX_DB", db_path)
    monkeypatch.setattr(ops_journals, "_FILES_ROOT", files_root)
    monkeypatch.setattr(ops_session_close, "_FILES_ROOT", files_root)
    monkeypatch.setattr(session_journals, "_FILES_ROOT", files_root)
    monkeypatch.setenv("CURSOR_AGENT_TRANSCRIPTS_ROOT", str(transcripts_root))
    return {
        "db_path": db_path,
        "files_root": files_root,
        "transcripts_root": transcripts_root,
    }


def _session_summary(summary: str) -> str:
    return f"## Session Summary\n\n{summary}\n\n## Decisions\n\n- (none)\n"


def test_dry_run_section_handoff_valid(handoff_close_env: dict[str, Path]) -> None:
    files_root = handoff_close_env["files_root"]
    rel = "notes/system/sessions/dry-handoff.md"
    path = files_root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "Continue thread 1188 from dry_run."
    path.write_text(
        f"<!-- handoff:start -->\n{body}\n<!-- handoff:end -->\n",
        encoding="utf-8",
    )
    summary = "Dry-run validates marker-backed handoff without writes."
    result = ops_journals._op_session_close(
        session_id="web-2026-06-03-1200",
        agent="web",
        session_summary_md=_session_summary(summary),
        summary=summary,
        transcript_depth="none",
        dry_run=True,
        handoff_source_path=rel,
        expected_handoff_prompt=body,
    )
    assert result["dry_run"] is True
    assert result["would_succeed"] is True
    assert result["handoff_valid"] is True
    assert result["derived_handoff_prompt"] == body
    preview = result["handoff_provenance_preview"]
    assert preview["derivation"] == DERIVATION_SECTION
    assert preview["derived_handoff_prompt_sha256"].startswith("sha256:")


def test_unresolved_does_not_keep_stale_prompt(
    handoff_close_env: dict[str, Path],
) -> None:
    files_root = handoff_close_env["files_root"]
    rel = "notes/system/sessions/no-markers.md"
    path = files_root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("no marker region here\n", encoding="utf-8")
    summary = "Close with bad markers must not store caller prompt."
    result = ops_journals._op_session_close(
        session_id="web-2026-06-03-1201",
        agent="web",
        session_summary_md=_session_summary(summary),
        summary=summary,
        transcript_depth="none",
        handoff_source_path=rel,
        handoff_prompt="stale caller prompt must not persist",
    )
    assert "error" not in result
    db_path = handoff_close_env["db_path"]
    row = (
        sqlite3.connect(db_path)
        .execute(
            "SELECT handoff_prompt FROM session_journals WHERE session_id = ?",
            ("web-2026-06-03-1201",),
        )
        .fetchone()
    )
    assert row is not None
    assert row[0] is None
    entity = (
        sqlite3.connect(db_path)
        .execute(
            "SELECT attributes FROM entities WHERE id = ?",
            ("transcript:web-2026-06-03-1201",),
        )
        .fetchone()
    )
    assert entity is None  # depth=none


def test_detached_string_derivation(tmp_path: Path) -> None:
    resolution = resolve_handoff_for_write(
        files_root=tmp_path,
        write_path="session_close",
        written_at="2026-06-03T00:00:00Z",
        handoff_source_path=None,
        handoff_source_section=None,
        handoff_prompt="detached only",
    )
    assert resolution.provenance is not None
    assert resolution.provenance["derivation"] == DERIVATION_DETACHED_STRING
    assert resolution.handoff_prompt == "detached only"


def test_dry_run_preview_helper(tmp_path: Path) -> None:
    src = tmp_path / "n.md"
    src.write_text(
        "<!-- handoff:start -->\nx\n<!-- handoff:end -->\n",
        encoding="utf-8",
    )
    preview = handoff_dry_run_preview(
        files_root=tmp_path,
        handoff_source_path="n.md",
        handoff_source_section=None,
        handoff_prompt=None,
    )
    assert preview["handoff_valid"] is True
    assert preview["derived_handoff_prompt"] == "x"
    assert preview["handoff_provenance_preview"]["derivation"] == DERIVATION_SECTION


def test_unresolved_derivation_constant() -> None:
    assert DERIVATION_SECTION_UNRESOLVED == "section_unresolved"
    assert DERIVATION_SECTION_AMBIGUOUS == "section_ambiguous"
