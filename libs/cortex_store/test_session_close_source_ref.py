"""Session-close ``source_ref`` stamping tests (unified-admission Step 3)."""

from __future__ import annotations

import importlib.util
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

_MIG_PATH = Path(__file__).parent / "migrations" / "055_session_journals_source_ref.py"
_spec = importlib.util.spec_from_file_location(
    "migration_055_session_journals_source_ref", _MIG_PATH
)
assert _spec is not None and _spec.loader is not None
migration_055 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migration_055)


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
        migration_055.migrate(conn)
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
    path.parent.mkdir(parents=True, exist_ok=True)
    records = [
        {
            "role": "user",
            "message": {"content": [{"type": "text", "text": "Implement source_ref."}]},
        },
        {
            "role": "assistant",
            "message": {
                "content": [{"type": "text", "text": "Stamping admission provenance."}]
            },
        },
    ]
    path.write_text(
        "\n".join(json.dumps(r) for r in records) + "\n",
        encoding="utf-8",
    )


def _session_summary(summary: str) -> str:
    return f"## Session Summary\n\n{summary}\n"


def _payload(
    *,
    session_id: str,
    transcripts_root: Path,
    transcript_depth: str = "verbatim",
    source_ref: str | None = None,
    source_ref_derivation: str | None = None,
) -> dict[str, Any]:
    summary = (
        "Implemented IDE source_ref stamping on session_close for admission provenance."
    )
    body: dict[str, Any] = {
        "session_id": session_id,
        "agent": "cursor",
        "session_summary_md": _session_summary(summary),
        "summary": summary,
        "domains": ["routing"],
        "decisions": ["Stamp source_ref server-side at session_close."],
        "entity_ids": ["todo:unified-admission-ide-source-ref"],
        "transcript_depth": transcript_depth,
        "source_ref": source_ref,
        "source_ref_derivation": source_ref_derivation,
    }
    if transcript_depth == "verbatim":
        jsonl_path = transcripts_root / session_id / f"{session_id}.jsonl"
        _write_jsonl(jsonl_path)
        body["transcript_jsonl_path"] = str(jsonl_path)
    elif transcript_depth == "light":
        body["session_summary_md"] = _session_summary(summary)
    return body


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


def test_source_ref_param_accepts_optional(session_env: dict[str, Path]) -> None:
    result = ops_journals._op_session_close(
        **_payload(
            session_id="cursor-2026-06-08-100000-a01",
            transcripts_root=session_env["transcripts_root"],
        )
    )
    assert "error" not in result, result


def test_stamp_transcript_attribute(session_env: dict[str, Path]) -> None:
    session_id = "cursor-2026-06-08-100100-a02"
    result = ops_journals._op_session_close(
        **_payload(
            session_id=session_id,
            transcripts_root=session_env["transcripts_root"],
            source_ref="todo:flintridge-appeal",
            source_ref_derivation="ide-todo-pickup",
        )
    )
    assert "error" not in result, result
    entity = _query_one(
        session_env["db_path"],
        "SELECT attributes FROM entities WHERE id = ?",
        (f"transcript:{session_id}",),
    )
    assert entity is not None
    attrs = json.loads(entity["attributes"])
    assert attrs["source_ref"] == "todo:flintridge-appeal"


def test_source_ref_provenance(session_env: dict[str, Path]) -> None:
    session_id = "cursor-2026-06-08-100200-a03"
    ops_journals._op_session_close(
        **_payload(
            session_id=session_id,
            transcripts_root=session_env["transcripts_root"],
            source_ref="todo:flintridge-appeal",
            source_ref_derivation="ide-todo-pickup",
        )
    )
    entity = _query_one(
        session_env["db_path"],
        "SELECT attributes FROM entities WHERE id = ?",
        (f"transcript:{session_id}",),
    )
    assert entity is not None
    prov = json.loads(entity["attributes"])["source_ref_provenance"]
    assert prov["external_ref"] == "todo:flintridge-appeal"
    assert prov["canonical_ref"] == "todo:flintridge-appeal"
    assert prov["source_kind"] == "todo"
    assert prov["derivation"] == "ide-todo-pickup"
    assert prov["captured_at"]


def _payload_none(*, session_id: str, source_ref: str | None = None) -> dict[str, Any]:
    summary = "None-depth close with source_ref journal-row mirror only."
    return {
        "session_id": session_id,
        "agent": "cursor",
        "session_summary_md": _session_summary(summary),
        "summary": summary,
        "transcript_depth": "none",
        "source_ref": source_ref,
    }


def test_journal_row_mirror_depth_none(session_env: dict[str, Path]) -> None:
    session_id = "cursor-2026-06-08-100300-a04"
    result = ops_journals._op_session_close(
        **_payload_none(session_id=session_id, source_ref="todo:flintridge-appeal")
    )
    assert "error" not in result, result
    assert result.get("transcript_entity_id") is None
    journal = _query_one(
        session_env["db_path"],
        "SELECT source_ref FROM session_journals WHERE session_id = ?",
        (session_id,),
    )
    assert journal is not None
    assert journal["source_ref"] == "todo:flintridge-appeal"


def test_source_ref_absent_parity(session_env: dict[str, Path]) -> None:
    session_id = "cursor-2026-06-08-100400-a05"
    ops_journals._op_session_close(
        **_payload(
            session_id=session_id,
            transcripts_root=session_env["transcripts_root"],
        )
    )
    entity = _query_one(
        session_env["db_path"],
        "SELECT attributes FROM entities WHERE id = ?",
        (f"transcript:{session_id}",),
    )
    assert entity is not None
    attrs = json.loads(entity["attributes"])
    assert "source_ref" not in attrs
    assert "source_ref_provenance" not in attrs
    journal = _query_one(
        session_env["db_path"],
        "SELECT source_ref FROM session_journals WHERE session_id = ?",
        (session_id,),
    )
    assert journal is not None
    assert journal["source_ref"] is None


def test_source_ref_unparseable_nonblocking(session_env: dict[str, Path]) -> None:
    session_id = "cursor-2026-06-08-100500-a06"
    result = ops_journals._op_session_close(
        **_payload(
            session_id=session_id,
            transcripts_root=session_env["transcripts_root"],
            source_ref="not-a-valid-ref",
        )
    )
    assert "error" not in result, result
    entity = _query_one(
        session_env["db_path"],
        "SELECT attributes FROM entities WHERE id = ?",
        (f"transcript:{session_id}",),
    )
    assert entity is not None
    attrs = json.loads(entity["attributes"])
    assert attrs["source_ref"] == "not-a-valid-ref"
    prov = attrs["source_ref_provenance"]
    assert prov["source_ref_unparseable"] is True
    assert prov["canonical_ref"] is None


def test_plan_phase_shorthand_canonical(session_env: dict[str, Path]) -> None:
    session_id = "cursor-2026-06-08-100600-a07"
    ops_journals._op_session_close(
        **_payload(
            session_id=session_id,
            transcripts_root=session_env["transcripts_root"],
            source_ref="plan:auth-revamp/phase-2",
            source_ref_derivation="ide-implement-plan-executor",
        )
    )
    entity = _query_one(
        session_env["db_path"],
        "SELECT attributes FROM entities WHERE id = ?",
        (f"transcript:{session_id}",),
    )
    assert entity is not None
    attrs = json.loads(entity["attributes"])
    assert attrs["source_ref"] == "plan_phase:auth-revamp/phase-2"
    prov = attrs["source_ref_provenance"]
    assert prov["canonical_ref"] == "plan_phase:auth-revamp/phase-2"
    assert prov["parent_ref"] == "plan:auth-revamp"


def test_source_ref_depth_none_advisory(session_env: dict[str, Path]) -> None:
    result = ops_journals._op_session_close(
        **_payload_none(
            session_id="cursor-2026-06-08-100700-a08", source_ref="todo:example"
        )
    )
    warnings = result.get("audit_warnings") or []
    assert any(w.get("kind") == "source_ref_depth_advisory" for w in warnings)


def test_migration_adds_source_ref_column() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE session_journals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            agent TEXT NOT NULL,
            summary TEXT NOT NULL,
            session_id TEXT
        );
        """
    )
    migration_055.migrate(conn)
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(session_journals)")}
    assert "source_ref" in cols
