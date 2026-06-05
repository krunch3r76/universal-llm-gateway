from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from cortex_store import db
from cortex_store.dispatch_ops import ops_journals
from cortex_store.routes import (
    session_close,
    session_close_helpers,
    session_close_persist,
    session_journals,
)


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
                confidence TEXT,
                confidence_score REAL,
                evidence TEXT,
                evidence_uris TEXT,
                derivation_type TEXT,
                chunk_id TEXT,
                chunk_id_schema TEXT,
                reasoning_summary TEXT,
                is_atomic INTEGER DEFAULT 1,
                is_decontextualized INTEGER DEFAULT 1,
                observed_at TEXT,
                valid_from TEXT,
                valid_until TEXT,
                superseded_by INTEGER,
                review_status TEXT,
                reviewer TEXT,
                reviewed_at TEXT,
                review_notes TEXT,
                resolution_status TEXT,
                fulfillment_assertion_id INTEGER,
                quality_score REAL,
                prospective_summary TEXT,
                events_json TEXT,
                artifact_uri TEXT,
                artifact_storage TEXT,
                entrenchment_score REAL,
                predicate_form TEXT,
                created_at TEXT,
                raw_predicate_form TEXT,
                normalization_decision TEXT,
                candidate_set_fingerprint TEXT,
                normalizer_version TEXT,
                seeded_by TEXT
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
                prior_session_id TEXT,
                handoff_prompt TEXT
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
    monkeypatch.setattr(session_close, "_FILES_ROOT", files_root)
    monkeypatch.setattr(session_close_persist, "_FILES_ROOT", files_root)
    monkeypatch.setattr(session_close_helpers, "_FILES_ROOT", files_root)
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
        "decisions": ["Persist handoff prompts on the session_journals row."],
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

    # The retired RJ-row id is absent from the response; journal_row_id is the durable handle.
    assert ("handoff_" + "entry_id") not in result
    assert result["transcript_entity_id"] == "transcript:orion-2026-05-04-0844"
    assert (files_root / result["transcript_path"]).is_file()

    journal = _query_one(
        db_path,
        "SELECT * FROM session_journals WHERE session_id = ?",
        ("orion-2026-05-04-0844",),
    )
    assert journal is not None
    assert journal["handoff_prompt"] == handoff

    entity = _query_one(
        db_path,
        "SELECT attributes FROM entities WHERE id = ?",
        ("transcript:orion-2026-05-04-0844",),
    )
    assert entity is not None
    assert json.loads(entity["attributes"])["handoff_prompt"] == handoff

    edge = _query_one(
        db_path,
        "SELECT * FROM session_edges WHERE from_node = ? AND to_node = ? AND edge_type = 'continues'",
        (
            "transcript:orion-2026-05-04-0844",
            "transcript:orion-2026-05-04-0700",
        ),
    )
    assert edge is not None

    # No reflective_journal row and no journal_links row are written for a handoff.
    assert _query_count(db_path, "SELECT COUNT(*) FROM reflective_journal") == 0
    assert _query_count(db_path, "SELECT COUNT(*) FROM journal_links") == 0


def test_close_sets_attribute_on_preexisting_bare_transcript_entity(
    session_env: dict[str, Path],
) -> None:
    """Out-of-order close must not drift attribute from column (agent-bus 1188).

    When session B closes with ``prior_session_id=A`` before A closes,
    ``_ensure_transcript_entity`` back-creates a BARE ``transcript:A`` (no
    attributes). A's later close hits ``INSERT OR IGNORE`` as a no-op, so the
    handoff (and provenance) would land only in the journal column unless the
    explicit attribute UPDATE runs. Assert attribute == column.
    """
    db_path = session_env["db_path"]
    files_root = session_env["files_root"]
    session_a = "orion-2026-05-04-0700"
    session_b = "orion-2026-05-04-0847"

    handoff = "Resume from the G2 confirmed+inference policy."
    handoff_file = files_root / "notes/system/sessions/a-handoff.md"
    handoff_file.parent.mkdir(parents=True, exist_ok=True)
    handoff_file.write_text(
        f"<!-- handoff:start -->\n{handoff}\n<!-- handoff:end -->\n",
        encoding="utf-8",
    )

    # B closes first → back-creates a bare transcript:A via prior_session_id.
    b_result = ops_journals._op_session_close(
        **_payload(
            session_id=session_b,
            prior_session_id=session_a,
            transcripts_root=session_env["transcripts_root"],
        )
    )
    assert "error" not in b_result, b_result
    bare = _query_one(
        db_path,
        "SELECT attributes FROM entities WHERE id = ?",
        (f"transcript:{session_a}",),
    )
    assert bare is not None  # pre-existing bare entity
    bare_attrs = json.loads(bare["attributes"]) if bare["attributes"] else {}
    assert "handoff_prompt" not in bare_attrs

    # A closes with a handoff → INSERT OR IGNORE no-op on the pre-existing
    # entity; the explicit UPDATE must carry the attribute state.
    a_payload = _payload(
        session_id=session_a,
        handoff_prompt=handoff,
        transcripts_root=session_env["transcripts_root"],
    )
    a_payload["handoff_source_path"] = "notes/system/sessions/a-handoff.md"
    a_payload["expected_handoff_prompt"] = handoff
    a_result = ops_journals._op_session_close(**a_payload)
    assert "error" not in a_result, a_result

    column = _query_one(
        db_path,
        "SELECT handoff_prompt FROM session_journals WHERE session_id = ?",
        (session_a,),
    )
    assert column is not None and column["handoff_prompt"] == handoff

    entity = _query_one(
        db_path,
        "SELECT attributes FROM entities WHERE id = ?",
        (f"transcript:{session_a}",),
    )
    assert entity is not None
    attrs = json.loads(entity["attributes"])
    # The drift bug would leave the attribute empty; it must match the column.
    assert attrs["handoff_prompt"] == column["handoff_prompt"]
    assert "status" not in attrs
    assert attrs["closed_at"]
    prov = attrs["handoff_provenance"]
    assert prov["write_path"] == "session_close"
    assert prov["source_file"] == "notes/system/sessions/a-handoff.md"
    assert prov["source_file_sha256"].startswith("sha256:")
    assert prov["derivation"] == "section"
    assert prov["derived_handoff_prompt_sha256"].startswith("sha256:")


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

    assert ("handoff_" + "entry_id") not in result
    warning = result.get("_warning", {})
    findings = warning.get("post_close_findings", [])
    assert not any(f["kind"] == "missing_handoff" for f in findings)
    # No handoff supplied ⟹ journal row's handoff_prompt is NULL.
    journal = _query_one(
        db_path,
        "SELECT handoff_prompt FROM session_journals WHERE session_id = ?",
        ("cursor-2026-05-04-0844",),
    )
    assert journal is not None
    assert journal["handoff_prompt"] is None
    assert _query_count(db_path, "SELECT COUNT(*) FROM reflective_journal") == 0
    assert _query_count(db_path, "SELECT COUNT(*) FROM journal_links") == 0


def test_session_close_rolls_back_and_unlinks_transcript_on_journal_insert_failure(
    session_env: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DB tx failure after the file write ⟹ file unlinked, no rows committed.

    The handoff write path no longer touches reflective_journal, so the
    rollback invariant is exercised by forcing the journal-row transaction
    to fail. ``json_encode`` is called while building the INSERT value
    tuples inside the try block — after the transcript file is written —
    so patching it raises mid-transaction and triggers the
    rollback + unlink path.
    """
    db_path = session_env["db_path"]
    files_root = session_env["files_root"]

    def _boom(*_args: Any, **_kwargs: Any) -> str:
        raise RuntimeError("journal insert failed")

    monkeypatch.setattr(session_close_persist, "json_encode", _boom)

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


def test_session_close_warns_handoff_missing_transcript_anchor(
    session_env: dict[str, Path],
) -> None:
    result = ops_journals._op_session_close(
        **_payload(
            session_id="orion-2026-05-04-0848",
            handoff_prompt="Poll agent-bus thread 99; integrate findings.",
            transcripts_root=session_env["transcripts_root"],
        )
    )
    assert "error" not in result, result
    findings = result.get("_warning", {}).get("post_close_findings", [])
    assert any(f["kind"] == "handoff_missing_transcript_anchor" for f in findings)


def test_session_close_handoff_with_transcript_anchor_no_anchor_warning(
    session_env: dict[str, Path],
) -> None:
    session_id = "orion-2026-05-04-0849"
    handoff = (
        f"**Closing session:** transcript:{session_id}\n"
        "**Load context:** fs(cortex, read, notes/system/transcripts/"
        f"{session_id}.md)\n"
        "Poll agent-bus thread 99."
    )
    result = ops_journals._op_session_close(
        **_payload(
            session_id=session_id,
            handoff_prompt=handoff,
            transcripts_root=session_env["transcripts_root"],
        )
    )
    assert "error" not in result, result
    findings = result.get("_warning", {}).get("post_close_findings", [])
    assert not any(f["kind"] == "handoff_missing_transcript_anchor" for f in findings)


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
    assert "error" not in first

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


def test_close_light_web_without_transcript_source_succeeds(
    session_env: dict[str, Path],
) -> None:
    """light web close: session_summary_md is the file; no transcript_md required."""
    summary = "Light-depth web close — structural layer only, no verbatim source."
    summary_md = _session_summary(summary)
    result = ops_journals._op_session_close(
        session_id="web-2026-05-27-1003",
        agent="web",
        session_summary_md=summary_md,
        summary=summary,
        transcript_depth="light",
    )
    assert "error" not in result, result
    assert result["transcript_depth"] == "light"
    assert result["transcript_entity_id"] is not None
    on_disk = (session_env["files_root"] / result["transcript_path"]).read_text(
        encoding="utf-8"
    )
    assert on_disk == summary_md


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
    assert not (files_root / "notes/system/transcripts/web-2026-05-27-1004.md").exists()
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


def test_close_none_with_handoff_rejected(
    session_env: dict[str, Path],
) -> None:
    """none + handoff ⟹ 422 handoff.requires_transcript_entity (no journal write)."""
    db_path = session_env["db_path"]
    handoff = "Resume by running the depth-dial verification suite."
    summary = "None-depth with handoff — rejected before persist."
    result = ops_journals._op_session_close(
        session_id="web-2026-05-27-1005",
        agent="web",
        session_summary_md=_session_summary(summary),
        summary=summary,
        transcript_depth="none",
        handoff_prompt=handoff,
    )
    assert "error" in result
    assert result.get("reason") == "handoff.requires_transcript_entity"
    journal = _query_one(
        db_path,
        "SELECT id FROM session_journals WHERE session_id = ?",
        ("web-2026-05-27-1005",),
    )
    assert journal is None


def test_close_none_with_handoff_source_path_rejected(
    session_env: dict[str, Path],
) -> None:
    """none + handoff_source_path ⟹ same 422 (derivation still needs an entity)."""
    summary = "None-depth with handoff_source_path — rejected."
    result = ops_journals._op_session_close(
        session_id="web-2026-05-27-1015",
        agent="web",
        session_summary_md=_session_summary(summary),
        summary=summary,
        transcript_depth="none",
        handoff_source_path="notes/system/sessions/missing-handoff.md",
    )
    assert "error" in result
    assert result.get("reason") == "handoff.requires_transcript_entity"


def test_close_light_with_handoff_mirrors_to_transcript_entity(
    session_env: dict[str, Path],
) -> None:
    """light + handoff ⟹ entity attributes carry handoff (canonical pickup surface)."""
    handoff = "Pick up phase 3 bus handoff — verify thread state first."
    summary = "Light-depth close with handoff mirrored to transcript entity."
    result = ops_journals._op_session_close(
        session_id="web-2026-05-27-1016",
        agent="web",
        session_summary_md=_session_summary(summary),
        summary=summary,
        transcript_depth="light",
        handoff_prompt=handoff,
    )
    assert "error" not in result, result
    assert result["transcript_entity_id"] == "transcript:web-2026-05-27-1016"
    entity = _query_one(
        session_env["db_path"],
        "SELECT attributes FROM entities WHERE id = ?",
        ("transcript:web-2026-05-27-1016",),
    )
    assert entity is not None
    assert json.loads(entity["attributes"])["handoff_prompt"] == handoff


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


def test_close_already_closed_idempotent_when_handoff_unchanged(
    session_env: dict[str, Path],
) -> None:
    """Second close with same handoff ⟹ idempotent snapshot (2-A v2 binding #5)."""
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

    summary_b = "Second close attempt — idempotent when handoff unchanged."
    second = ops_journals._op_session_close(
        session_id="web-2026-05-27-1009",
        agent="web",
        transcript_md=_web_transcript_md("web-2026-05-27-1009"),
        session_summary_md=_session_summary(summary_b),
        summary=summary_b,
        transcript_depth="verbatim",
    )
    assert "error" not in second
    assert second["journal_row_id"] == first["journal_row_id"]
    assert second["transcript_depth"] == "none"
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
    assert not (files_root / "notes/system/transcripts/web-2026-05-27-1011.md").exists()
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
