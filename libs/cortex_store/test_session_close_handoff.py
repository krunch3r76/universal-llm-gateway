from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from cortex_store import db
from cortex_store.dispatch_ops import ops_journals
from cortex_store.routes import session_journals


def _install_schema(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(
            """
            CREATE TABLE entities (
                id TEXT PRIMARY KEY,
                type TEXT NOT NULL,
                name TEXT NOT NULL,
                description TEXT,
                status TEXT,
                source_uri TEXT,
                attributes TEXT,
                created_at TEXT,
                updated_at TEXT
            );
            CREATE TABLE assertions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_id TEXT NOT NULL,
                claim TEXT,
                superseded_by INTEGER,
                review_status TEXT
            );
            CREATE TABLE relationships (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                from_entity TEXT NOT NULL,
                to_entity TEXT NOT NULL,
                type TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE session_journals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                agent TEXT NOT NULL,
                summary TEXT NOT NULL,
                domains TEXT,
                decisions TEXT,
                open_items TEXT,
                entity_ids TEXT,
                file_path TEXT,
                session_id TEXT NOT NULL,
                prior_session_id TEXT
            );
            CREATE TABLE session_edges (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                agent TEXT NOT NULL,
                from_node TEXT NOT NULL,
                to_node TEXT NOT NULL,
                edge_type TEXT NOT NULL,
                strength REAL,
                edge_source TEXT,
                created_at TEXT,
                valid_until TEXT
            );
            CREATE TABLE reflective_journal (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent TEXT NOT NULL,
                register TEXT NOT NULL,
                entry TEXT NOT NULL,
                kind TEXT NOT NULL,
                session_id TEXT,
                revises INTEGER,
                consolidation_data TEXT,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
            );
            CREATE TABLE journal_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                from_entry INTEGER NOT NULL,
                to_entry INTEGER,
                to_entity TEXT,
                link_type TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
            );
            """
        )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture()
def session_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    db_path = tmp_path / "cortex.db"
    files_root = tmp_path / "files"
    files_root.mkdir(parents=True)
    transcripts_root = tmp_path / "agent-transcripts"
    transcripts_root.mkdir()
    _install_schema(db_path)
    monkeypatch.setattr(db, "_CORTEX_DB", db_path)
    monkeypatch.setattr(ops_journals, "_FILES_ROOT", files_root)
    # Route handler reads _FILES_ROOT through its own import — patch the
    # symbol on routes/session_journals.py as well to keep the test isolated
    # from the cortex-api host's CORTEX_FILES_ROOT.
    monkeypatch.setattr(session_journals, "_FILES_ROOT", files_root)
    monkeypatch.setenv("CURSOR_AGENT_TRANSCRIPTS_ROOT", str(transcripts_root))
    return {
        "db_path": db_path,
        "files_root": files_root,
        "transcripts_root": transcripts_root,
    }


def _write_jsonl(path: Path) -> None:
    """Two-turn fake Cursor JSONL — enough to satisfy the dual-layer doctrine."""
    path.parent.mkdir(parents=True, exist_ok=True)
    records = [
        {
            "role": "user",
            "message": {
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "Please continue the handoff capture arc and preserve "
                            "atomicity."
                        ),
                    }
                ]
            },
        },
        {
            "role": "assistant",
            "message": {
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "I audited the write path, confirmed the transaction "
                            "boundary, and mapped the rollback risks."
                        ),
                    }
                ]
            },
        },
        {
            "role": "user",
            "message": {
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "Make sure the next session can resume without "
                            "reconstructing context from scratch."
                        ),
                    }
                ]
            },
        },
        {
            "role": "assistant",
            "message": {
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "I will persist a continuation-grade handoff prompt, "
                            "verify the link direction, and keep the summary "
                            "grounded in the completed work."
                        ),
                    }
                ]
            },
        },
    ]
    with path.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec) + "\n")


def _session_summary(summary: str) -> str:
    return (
        "## Session Summary\n\n"
        f"**Decisions:** {summary}\n"
        "**Open items:** Finish the docstring and test pass.\n"
    )


def _payload(
    *,
    session_id: str,
    agent: str = "gatherer",
    prior_session_id: str | None = None,
    handoff_prompt: str | None = None,
    transcripts_root: Path,
) -> dict[str, Any]:
    summary = (
        "Validated the session-close handoff capture path and checked "
        "rollback behavior."
    )
    jsonl_path = transcripts_root / session_id / f"{session_id}.jsonl"
    _write_jsonl(jsonl_path)
    return {
        "session_id": session_id,
        "agent": agent,
        "transcript_jsonl_path": str(jsonl_path),
        "session_summary_md": _session_summary(summary),
        "summary": summary,
        "domains": ["cortex"],
        "decisions": ["Persist handoff prompts as reflective journal entries."],
        "open_items": ["Finish the docstring and test pass."],
        "entity_ids": ["service:cortex"],
        "prior_session_id": prior_session_id,
        "handoff_prompt": handoff_prompt,
    }


def _query_one(
    db_path: Path, sql: str, params: tuple[Any, ...] = ()
) -> dict[str, Any] | None:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(sql, params).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _query_count(db_path: Path, sql: str, params: tuple[Any, ...] = ()) -> int:
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(sql, params).fetchone()
        assert row is not None
        return int(row[0])
    finally:
        conn.close()


def test_session_close_happy_path_with_handoff(session_env: dict[str, Path]) -> None:
    db_path = session_env["db_path"]
    files_root = session_env["files_root"]
    handoff = "Start with the openapi and tool-doc pass, then verify rollback tests."

    result = ops_journals._op_session_close(
        **_payload(
            session_id="orion-2026-05-04-0844",
            prior_session_id="orion-2026-05-04-0700",
            handoff_prompt=handoff,
            transcripts_root=session_env["transcripts_root"],
        )
    )

    assert result["handoff_entry_id"] is not None
    assert result["transcript_entity_id"] == "transcript:orion-2026-05-04-0844"
    assert (files_root / result["transcript_path"]).is_file()

    journal = _query_one(
        db_path,
        "SELECT * FROM session_journals WHERE session_id = ?",
        ("orion-2026-05-04-0844",),
    )
    assert journal is not None

    edge = _query_one(
        db_path,
        "SELECT * FROM session_edges WHERE from_node = ? AND to_node = ? AND edge_type = 'continues'",
        (
            "transcript:orion-2026-05-04-0844",
            "transcript:orion-2026-05-04-0700",
        ),
    )
    assert edge is not None

    entry = _query_one(
        db_path,
        "SELECT * FROM reflective_journal WHERE id = ?",
        (result["handoff_entry_id"],),
    )
    assert entry is not None
    assert entry["kind"] == "handoff"
    assert entry["register"] == "self"
    assert entry["entry"] == handoff

    link = _query_one(
        db_path,
        "SELECT * FROM journal_links WHERE from_entry = ? AND link_type = 'handoff_for'",
        (result["handoff_entry_id"],),
    )
    assert link is not None
    assert link["to_entity"] == "transcript:orion-2026-05-04-0844"


def test_session_close_without_handoff_is_clean_no_warnings(
    session_env: dict[str, Path],
) -> None:
    """Per assertion 8384: handoff absence is not a gap — no post-close warning."""
    db_path = session_env["db_path"]

    result = ops_journals._op_session_close(
        **_payload(
            session_id="cursor-2026-05-04-0844",
            agent="cursor",
            transcripts_root=session_env["transcripts_root"],
        )
    )

    assert result["handoff_entry_id"] is None
    warning = result.get("_warning", {})
    findings = warning.get("post_close_findings", [])
    assert not any(f["kind"] == "missing_handoff" for f in findings)
    assert _query_count(db_path, "SELECT COUNT(*) FROM reflective_journal") == 0
    assert _query_count(db_path, "SELECT COUNT(*) FROM journal_links") == 0


def test_session_close_rolls_back_and_unlinks_transcript_on_handoff_insert_failure(
    session_env: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = session_env["db_path"]
    files_root = session_env["files_root"]

    def _boom(*_args: Any, **_kwargs: Any) -> int:
        raise RuntimeError("handoff insert failed")

    monkeypatch.setattr(session_journals, "_insert_reflective_entry_tx", _boom)

    result = ops_journals._op_session_close(
        **_payload(
            session_id="orion-2026-05-04-0845",
            prior_session_id="orion-2026-05-04-0700",
            handoff_prompt="Resume with rollback verification.",
            transcripts_root=session_env["transcripts_root"],
        )
    )

    assert "Session close failed" in result["error"]
    assert not (
        files_root / "notes/system/transcripts/orion-2026-05-04-0845.md"
    ).exists()
    assert _query_count(db_path, "SELECT COUNT(*) FROM entities") == 0
    assert _query_count(db_path, "SELECT COUNT(*) FROM session_journals") == 0
    assert _query_count(db_path, "SELECT COUNT(*) FROM session_edges") == 0
    assert _query_count(db_path, "SELECT COUNT(*) FROM reflective_journal") == 0
    assert _query_count(db_path, "SELECT COUNT(*) FROM journal_links") == 0


def test_session_close_rolls_back_and_unlinks_transcript_on_link_failure(
    session_env: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = session_env["db_path"]
    files_root = session_env["files_root"]

    def _boom(*_args: Any, **_kwargs: Any) -> int:
        raise RuntimeError("link insert failed")

    monkeypatch.setattr(session_journals, "_insert_journal_link_tx", _boom)

    result = ops_journals._op_session_close(
        **_payload(
            session_id="orion-2026-05-04-0846",
            prior_session_id="orion-2026-05-04-0700",
            handoff_prompt="Resume by checking the journal link direction.",
            transcripts_root=session_env["transcripts_root"],
        )
    )

    assert "Session close failed" in result["error"]
    assert not (
        files_root / "notes/system/transcripts/orion-2026-05-04-0846.md"
    ).exists()
    assert _query_count(db_path, "SELECT COUNT(*) FROM entities") == 0
    assert _query_count(db_path, "SELECT COUNT(*) FROM session_journals") == 0
    assert _query_count(db_path, "SELECT COUNT(*) FROM session_edges") == 0
    assert _query_count(db_path, "SELECT COUNT(*) FROM reflective_journal") == 0
    assert _query_count(db_path, "SELECT COUNT(*) FROM journal_links") == 0


def _web_transcript_md(session_id: str) -> str:
    """Plausible web-supplied verbatim markdown (no JSONL exists)."""
    return (
        f"# Transcript: {session_id}\n\n"
        "## Turn 1 — kickoff\n\n"
        "### User\n\nWe need to validate the either-of path end to end.\n\n"
        "### Assistant\n\nI confirmed the route handler accepts transcript_md "
        "directly and writes the composed transcript atomically.\n\n"
        "## Turn 2 — followup\n\n"
        "### User\n\nGood — capture the decision and continuation state.\n\n"
        "### Assistant\n\nSeeded decision and continuation-state assertions; "
        "the close path now supports both cursor and web.\n\n"
    )


def _web_payload(
    *, session_id: str, agent: str = "web", **extra: Any
) -> dict[str, Any]:
    summary = (
        "Validated the web session-close transcript_md path against the "
        "either-of validator."
    )
    base: dict[str, Any] = {
        "session_id": session_id,
        "agent": agent,
        "transcript_md": _web_transcript_md(session_id),
        "session_summary_md": _session_summary(summary),
        "summary": summary,
    }
    base.update(extra)
    return base


def test_session_close_accepts_transcript_md_only(
    session_env: dict[str, Path],
) -> None:
    """Web close path: transcript_md is sufficient — no JSONL needed."""
    db_path = session_env["db_path"]
    files_root = session_env["files_root"]
    result = ops_journals._op_session_close(
        **_web_payload(session_id="web-2026-05-17-0410")
    )
    assert "error" not in result, result
    assert result["transcript_entity_id"] == "transcript:web-2026-05-17-0410"
    assert result["turn_count"] == 2
    assert result["content_hash"].startswith("sha256:")
    on_disk = (files_root / result["transcript_path"]).read_text(encoding="utf-8")
    assert "## Turn 1" in on_disk
    assert "## Session Summary" in on_disk
    journal = _query_one(
        db_path,
        "SELECT * FROM session_journals WHERE session_id = ?",
        ("web-2026-05-17-0410",),
    )
    assert journal is not None


def test_session_close_rejects_when_neither_source_supplied(
    session_env: dict[str, Path],
) -> None:
    """Either-of: neither jsonl_path nor transcript_md ⟹ structured error."""
    summary = (
        "Confirmed the either-of validator rejects when both transcript "
        "sources are missing."
    )
    result = ops_journals._op_session_close(
        session_id="web-2026-05-17-0411",
        agent="claude-web",
        session_summary_md=_session_summary(summary),
        summary=summary,
    )
    assert "error" in result
    assert "transcript_jsonl_path" in result["error"]
    assert "transcript_md" in result["error"]


def test_session_close_prefers_jsonl_path_when_both_supplied(
    session_env: dict[str, Path],
) -> None:
    """Both supplied ⟹ jsonl_path wins; transcript_md is ignored."""
    files_root = session_env["files_root"]
    payload = _payload(
        session_id="cursor-2026-05-17-0412",
        agent="cursor",
        transcripts_root=session_env["transcripts_root"],
    )
    payload["transcript_md"] = (
        "# Transcript: SHOULD-NOT-APPEAR\n\n"
        "## Turn 99 — ignored\n\n### User\n\nThis text MUST NOT land on disk.\n"
    )
    result = ops_journals._op_session_close(**payload)
    assert "error" not in result, result
    on_disk = (files_root / result["transcript_path"]).read_text(encoding="utf-8")
    assert "SHOULD-NOT-APPEAR" not in on_disk
    assert "## Turn 99" not in on_disk
    assert "## Turn 1" in on_disk


def test_session_close_warns_when_prior_session_id_is_omitted(
    session_env: dict[str, Path],
) -> None:
    first = ops_journals._op_session_close(
        **_payload(
            session_id="orion-2026-05-04-0700",
            handoff_prompt="Next session should continue the handoff capture work.",
            transcripts_root=session_env["transcripts_root"],
        )
    )
    assert first["handoff_entry_id"] is not None

    second = ops_journals._op_session_close(
        **_payload(
            session_id="orion-2026-05-04-0847",
            handoff_prompt="Resume with the final documentation pass.",
            transcripts_root=session_env["transcripts_root"],
        )
    )

    findings = second.get("_warning", {}).get("post_close_findings", [])
    assert any(f["kind"] == "prior_session_id_omitted" for f in findings)



# ---- transcript_depth dial tests (Phase 1 of session-close-transcript-depth-dial) ----


def test_close_verbatim_default_is_backward_compatible(
    session_env: dict[str, Path],
) -> None:
    """No transcript_depth arg ⟹ defaults to verbatim; response carries field."""
    files_root = session_env["files_root"]
    payload = _payload(
        session_id="cursor-2026-05-27-1000",
        agent="cursor",
        transcripts_root=session_env["transcripts_root"],
    )
    result = ops_journals._op_session_close(**payload)
    assert "error" not in result, result
    assert result["transcript_depth"] == "verbatim"
    assert result["transcript_entity_id"] == "transcript:cursor-2026-05-27-1000"
    assert result["transcript_path"] is not None
    assert (files_root / result["transcript_path"]).is_file()
    assert result["content_hash"].startswith("sha256:")


def test_close_verbatim_explicit(session_env: dict[str, Path]) -> None:
    """transcript_depth='verbatim' explicit ⟹ identical to default."""
    payload = _payload(
        session_id="cursor-2026-05-27-1001",
        agent="cursor",
        transcripts_root=session_env["transcripts_root"],
    )
    payload["transcript_depth"] = "verbatim"
    result = ops_journals._op_session_close(**payload)
    assert "error" not in result, result
    assert result["transcript_depth"] == "verbatim"
    assert result["transcript_entity_id"] is not None


def test_close_light_writes_structural_layer_only(
    session_env: dict[str, Path],
) -> None:
    """light ⟹ file contains session_summary_md only; entity attribute set; turn_count==0."""
    db_path = session_env["db_path"]
    files_root = session_env["files_root"]
    summary = "Light-depth close — structural-layer-only file written."
    summary_md = _session_summary(summary)
    result = ops_journals._op_session_close(
        session_id="web-2026-05-27-1002",
        agent="web",
        transcript_md=_web_transcript_md("web-2026-05-27-1002"),
        session_summary_md=summary_md,
        summary=summary,
        transcript_depth="light",
    )
    assert "error" not in result, result
    assert result["transcript_depth"] == "light"
    assert result["transcript_entity_id"] == "transcript:web-2026-05-27-1002"
    assert result["transcript_path"] is not None
    assert result["turn_count"] == 0
    on_disk = (files_root / result["transcript_path"]).read_text(encoding="utf-8")
    # Light file is the structural layer verbatim — no Turn blocks, no User voice.
    assert on_disk == summary_md
    # Entity attributes carry transcript_depth.
    row = _query_one(
        db_path,
        "SELECT attributes FROM entities WHERE id = ?",
        ("transcript:web-2026-05-27-1002",),
    )
    assert row is not None
    attrs = json.loads(row["attributes"])
    assert attrs["transcript_depth"] == "light"


def test_close_light_without_transcript_source_succeeds(
    session_env: dict[str, Path],
) -> None:
    """light derives content from session_summary_md ⟹ no 422 when source omitted."""
    summary = "Light-depth close without any transcript source supplied."
    result = ops_journals._op_session_close(
        session_id="web-2026-05-27-1003",
        agent="web",
        session_summary_md=_session_summary(summary),
        summary=summary,
        transcript_depth="light",
    )
    assert "error" not in result, result
    assert result["transcript_depth"] == "light"
    assert result["transcript_entity_id"] is not None



def test_close_none_skips_file_and_entity(session_env: dict[str, Path]) -> None:
    """none ⟹ no file, no transcript entity, journal row with file_path=NULL."""
    db_path = session_env["db_path"]
    files_root = session_env["files_root"]
    summary = "None-depth close — only the journal row is written."
    result = ops_journals._op_session_close(
        session_id="web-2026-05-27-1004",
        agent="web",
        session_summary_md=_session_summary(summary),
        summary=summary,
        transcript_depth="none",
    )
    assert "error" not in result, result
    assert result["transcript_depth"] == "none"
    assert result["transcript_entity_id"] is None
    assert result["transcript_path"] is None
    assert result["content_hash"] is None
    assert result["turn_count"] == 0
    assert result["byte_count"] == 0
    # No file written under notes/system/transcripts/.
    assert not (
        files_root / "notes/system/transcripts/web-2026-05-27-1004.md"
    ).exists()
    # No transcript entity exists.
    ent = _query_one(
        db_path,
        "SELECT id FROM entities WHERE id = ?",
        ("transcript:web-2026-05-27-1004",),
    )
    assert ent is None
    # Journal row exists with file_path NULL.
    jr = _query_one(
        db_path,
        "SELECT file_path FROM session_journals WHERE session_id = ?",
        ("web-2026-05-27-1004",),
    )
    assert jr is not None
    assert jr["file_path"] is None


def test_close_none_with_handoff_skips_link(session_env: dict[str, Path]) -> None:
    """none + handoff ⟹ reflective entry created; no handoff_for link row."""
    db_path = session_env["db_path"]
    summary = "None-depth with handoff — reflective entry without link."
    result = ops_journals._op_session_close(
        session_id="web-2026-05-27-1005",
        agent="web",
        session_summary_md=_session_summary(summary),
        summary=summary,
        transcript_depth="none",
        handoff_prompt="Resume by running the depth-dial verification suite.",
    )
    assert "error" not in result, result
    assert result["handoff_entry_id"] is not None
    # Reflective journal entry exists and is scoped to the session.
    entry = _query_one(
        db_path,
        "SELECT * FROM reflective_journal WHERE id = ?",
        (result["handoff_entry_id"],),
    )
    assert entry is not None
    assert entry["kind"] == "handoff"
    assert entry["session_id"] == "web-2026-05-27-1005"
    # No journal_links row for this handoff entry (no transcript entity to link to).
    assert (
        _query_count(
            db_path,
            "SELECT COUNT(*) FROM journal_links WHERE from_entry = ?",
            (result["handoff_entry_id"],),
        )
        == 0
    )


def test_close_none_with_prior_session_writes_edge(
    session_env: dict[str, Path],
) -> None:
    """none + prior_session_id ⟹ continues edge written (FK-less, safe)."""
    db_path = session_env["db_path"]
    summary = "None-depth with prior_session_id — edge still written."
    result = ops_journals._op_session_close(
        session_id="web-2026-05-27-1006",
        agent="web",
        session_summary_md=_session_summary(summary),
        summary=summary,
        transcript_depth="none",
        prior_session_id="web-2026-05-27-0959",
    )
    assert "error" not in result, result
    edge = _query_one(
        db_path,
        "SELECT * FROM session_edges WHERE from_node = ? AND to_node = ? "
        "AND edge_type = 'continues'",
        (
            "transcript:web-2026-05-27-1006",
            "transcript:web-2026-05-27-0959",
        ),
    )
    assert edge is not None


def test_close_verbatim_missing_source_still_422(
    session_env: dict[str, Path],
) -> None:
    """verbatim default + no source ⟹ 422 transcript_source.missing (preserved)."""
    summary = "Verbatim missing source — still rejected after depth dial lands."
    result = ops_journals._op_session_close(
        session_id="web-2026-05-27-1007",
        agent="web",
        session_summary_md=_session_summary(summary),
        summary=summary,
    )
    assert "error" in result
    assert "transcript_jsonl_path" in result["error"]
    assert "transcript_md" in result["error"]



def test_close_depth_invalid_value_rejected(session_env: dict[str, Path]) -> None:
    """transcript_depth='medium' ⟹ Pydantic Literal rejects at request validation."""
    # The ops layer accepts depth as str and forwards it; the rejection
    # surfaces when _close_session_impl calls SessionCloseRequest.model_validate
    # — caught as a generic exception in the ops error-envelope branch.
    payload = _payload(
        session_id="cursor-2026-05-27-1008",
        agent="cursor",
        transcripts_root=session_env["transcripts_root"],
    )
    payload["transcript_depth"] = "medium"  # invalid Literal value
    result = ops_journals._op_session_close(**payload)
    assert "error" in result


def test_close_already_closed_echoes_prior_depth(
    session_env: dict[str, Path],
) -> None:
    """Second close attempt ⟹ 422 session.already_closed with prior depth echoed."""
    summary_a = "First close — depth=none, no transcript artifact written."
    first = ops_journals._op_session_close(
        session_id="web-2026-05-27-1009",
        agent="web",
        session_summary_md=_session_summary(summary_a),
        summary=summary_a,
        transcript_depth="none",
    )
    assert "error" not in first, first
    assert first["transcript_depth"] == "none"

    summary_b = "Second close attempt — should be rejected as already closed."
    second = ops_journals._op_session_close(
        session_id="web-2026-05-27-1009",
        agent="web",
        transcript_md=_web_transcript_md("web-2026-05-27-1009"),
        session_summary_md=_session_summary(summary_b),
        summary=summary_b,
        transcript_depth="verbatim",
    )
    assert "error" in second
    assert second.get("reason") == "session.already_closed"
    assert second.get("transcript_depth") == "none"
    # No transcript entity existed for the prior depth=none close.
    assert second.get("transcript_entity_id") is None


def test_preflight_none_skips_source_check(session_env: dict[str, Path]) -> None:
    """preflight with depth=none and no source ⟹ ok:true; zero turn/byte."""
    summary = "Preflight depth=none — no transcript source required."
    result = ops_journals._op_session_close_preflight(
        session_id="web-2026-05-27-1010",
        agent="web",
        session_summary_md=_session_summary(summary),
        summary=summary,
        transcript_depth="none",
    )
    assert result["ok"] is True
    assert result["turn_count"] == 0
    assert result["byte_count"] == 0
    assert result["transcript_depth"] == "none"


def test_dry_run_none_succeeds_without_artifact(
    session_env: dict[str, Path],
) -> None:
    """dry_run + depth=none ⟹ would_succeed; no file, no entity, no journal row."""
    db_path = session_env["db_path"]
    files_root = session_env["files_root"]
    summary = "Dry run with depth=none — preview only, no writes."
    result = ops_journals._op_session_close(
        session_id="web-2026-05-27-1011",
        agent="web",
        session_summary_md=_session_summary(summary),
        summary=summary,
        transcript_depth="none",
        dry_run=True,
    )
    assert result["dry_run"] is True
    assert result["would_succeed"] is True
    assert result["transcript_depth"] == "none"
    assert result["byte_count"] == 0
    # No side effects.
    assert not (
        files_root / "notes/system/transcripts/web-2026-05-27-1011.md"
    ).exists()
    assert (
        _query_count(
            db_path,
            "SELECT COUNT(*) FROM session_journals WHERE session_id = ?",
            ("web-2026-05-27-1011",),
        )
        == 0
    )
    assert (
        _query_count(
            db_path,
            "SELECT COUNT(*) FROM entities WHERE id = ?",
            ("transcript:web-2026-05-27-1011",),
        )
        == 0
    )
