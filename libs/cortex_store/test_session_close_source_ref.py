"""Session-close ``source_ref`` stamping tests (unified-admission Step 3)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from cortex_store.dispatch_ops import ops_journals


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
    import importlib.util

    mig = Path(__file__).parent / "migrations" / "055_session_journals_source_ref.py"
    if not mig.is_file():
        pytest.skip("migration file absent from this worktree: 055_session_journals_source_ref.py")
    spec = importlib.util.spec_from_file_location(
        "migration_055_session_journals_source_ref", mig
    )
    assert spec is not None and spec.loader is not None
    migration_055 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration_055)
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


@pytest.mark.offline
def test_journal_write_stamps_transcript_timestamps(
    session_env: dict[str, Path],
) -> None:
    """Regression (friction a16837): journal_write must not 500 on json_encode.

    ``session_close_helpers._stamp_transcript_timestamps`` referenced
    ``json_encode`` without importing it, so every ``journal_write`` raised
    HTTP 500 ``NameError: name 'json_encode' is not defined`` (input-independent).
    This exercises the full ``_op_journal_write`` path and asserts the transcript
    entity's attributes carry ``opened_at`` / ``closed_at`` with no NameError.
    """
    session_id = "claude-web-2026-06-11-043000-a16"
    result = ops_journals._op_journal_write(
        timestamp="2026-06-11T04:30:00Z",
        agent="claude-web",
        summary="a16837 regression guard",
        session_id=session_id,
    )
    assert "error" not in result, result
    entity = _query_one(
        session_env["db_path"],
        "SELECT attributes FROM entities WHERE id = ?",
        (f"transcript:{session_id}",),
    )
    assert entity is not None
    attrs = json.loads(entity["attributes"])
    assert attrs["closed_at"] == "2026-06-11T04:30:00Z"
    assert attrs["opened_at"] == "2026-06-11T04:30:00Z"
