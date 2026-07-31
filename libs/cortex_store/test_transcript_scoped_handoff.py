"""Boot continuity and transcript-scoped handoff retrieval tests."""

# ruff: noqa: F811 — pytest fixture injection reuses imported `session_env` name

from __future__ import annotations

import json
from pathlib import Path

from cortex_store.dispatch_ops import ops_journals
from cortex_store.handoff_surface import apply_handoff_read_projection
from cortex_store.models import SessionHandoffUpsertRequest
from cortex_store.routes.boot import continuity
from cortex_store.routes.session_handoff import upsert_session_handoff
from cortex_store.test_session_close_handoff import (
    _anchored_handoff,
    _write_jsonl,
)


def _session_summary(summary: str) -> str:
    return (
        "## Session Summary\n\n"
        f"**Summary:** {summary}\n\n"
        "**Decisions:**\n- One decision recorded.\n"
    )


def _close_with_handoff(
    session_env: dict[str, Path],
    *,
    session_id: str = "cursor-2026-05-31-120000-a01",
    handoff: str = "Resume with the explicit-load gate.",
) -> dict[str, object]:
    jsonl = session_env["transcripts_root"] / f"{session_id}.jsonl"
    _write_jsonl(jsonl)
    anchored = _anchored_handoff(session_id, handoff)
    result = ops_journals._op_session_close(
        session_id=session_id,
        agent="cursor",
        transcript_jsonl_path=str(jsonl),
        session_summary_md=_session_summary("Closed with handoff for tests."),
        summary="Closed with handoff for tests — long enough summary.",
        handoff_prompt=anchored,
    )
    assert "error" not in result, result
    return result


def _query_one(db_path: Path, sql: str, params: tuple[object, ...]) -> dict | None:
    conn = __import__("sqlite3").connect(db_path)
    conn.row_factory = __import__("sqlite3").Row
    try:
        row = conn.execute(sql, params).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def test_boot_continuity_omits_handoff(session_env: dict[str, Path]) -> None:
    handoff = "This must not appear at boot."
    _close_with_handoff(session_env, handoff=handoff)

    payload = continuity.get_boot_continuity(agent="cursor")

    assert "handoff" not in payload
    assert payload["last_session"] is not None
    assert payload["last_session"]["session_id"] == "cursor-2026-05-31-120000-a01"
    assert payload["continuity_chain"] == ["cursor-2026-05-31-120000-a01"]
    assert payload["continuations"] == []
    assert payload["hints"] == []


def test_close_mirrors_handoff_to_transcript_entity(
    session_env: dict[str, Path],
) -> None:
    handoff = "Mirror me on the transcript entity."
    result = _close_with_handoff(session_env, handoff=handoff)
    assert result["transcript_entity_id"] == "transcript:cursor-2026-05-31-120000-a01"

    entity = _query_one(
        session_env["db_path"],
        "SELECT attributes FROM entities WHERE id = ?",
        ("transcript:cursor-2026-05-31-120000-a01",),
    )
    assert entity is not None
    attrs = json.loads(entity["attributes"])
    assert attrs["handoff_prompt"] == _anchored_handoff(
        "cursor-2026-05-31-120000-a01", handoff
    )


def test_post_close_handoff_upsert_replaces_and_mirrors(
    session_env: dict[str, Path],
) -> None:
    _close_with_handoff(session_env, handoff="Original handoff text.")
    updated = "Revised post-close handoff — upsert replaces prior."

    response = upsert_session_handoff(
        "cursor-2026-05-31-120000-a01",
        SessionHandoffUpsertRequest(handoff_prompt=updated),
    )

    assert response.handoff_prompt == updated
    assert response.journal_row_id > 0
    assert response.transcript_entity_id == "transcript:cursor-2026-05-31-120000-a01"

    journal = _query_one(
        session_env["db_path"],
        "SELECT handoff_prompt FROM session_journals WHERE session_id = ?",
        ("cursor-2026-05-31-120000-a01",),
    )
    assert journal is not None
    assert journal["handoff_prompt"] == updated

    entity = _query_one(
        session_env["db_path"],
        "SELECT attributes FROM entities WHERE id = ?",
        ("transcript:cursor-2026-05-31-120000-a01",),
    )
    assert entity is not None
    assert json.loads(entity["attributes"])["handoff_prompt"] == updated


def test_post_close_handoff_on_none_depth_session(
    session_env: dict[str, Path],
) -> None:
    session_id = "cursor-2026-05-31-130000-a02"
    none_result = ops_journals._op_session_close(
        session_id=session_id,
        agent="cursor",
        session_summary_md=_session_summary("None depth close."),
        summary="None depth close — long enough summary.",
        transcript_depth="none",
    )
    assert "error" not in none_result, none_result

    response = upsert_session_handoff(
        "cursor-2026-05-31-130000-a02",
        SessionHandoffUpsertRequest(handoff_prompt="Updated journal-only handoff."),
    )

    assert response.transcript_entity_id is None
    journal = _query_one(
        session_env["db_path"],
        "SELECT handoff_prompt FROM session_journals WHERE session_id = ?",
        ("cursor-2026-05-31-130000-a02",),
    )
    assert journal is not None
    assert journal["handoff_prompt"] == "Updated journal-only handoff."


def test_entity_get_surfaces_unverified_handoff_flag(
    session_env: dict[str, Path],
) -> None:
    """Detached handoff (source_file:null) is surfaced with handoff_surface flag."""
    session_id = "cursor-2026-06-03-120100-a03"
    result = _close_with_handoff(
        session_env,
        session_id=session_id,
        handoff="Detached prompt for surface-but-flag test.",
    )
    assert "error" not in result, result
    entity = _query_one(
        session_env["db_path"],
        "SELECT attributes FROM entities WHERE id = ?",
        (f"transcript:{session_id}",),
    )
    assert entity is not None
    attrs = json.loads(entity["attributes"])
    assert attrs["handoff_prompt"] == _anchored_handoff(
        session_id, "Detached prompt for surface-but-flag test."
    )
    assert attrs["handoff_provenance"]["source_file"] == (
        f"notes/system/handoffs/{session_id}.md"
    )
    assert attrs["handoff_provenance"]["derivation"] == "auto_persisted"

    projected, hints = apply_handoff_read_projection(
        {"id": f"transcript:{session_id}", "attributes": attrs},
    )
    surface = projected["attributes"]["handoff_surface"]
    assert surface["surfaced"] is True
    assert surface["verified"] is False
    assert surface["flag"] == "unverified"
    assert hints is not None
    assert any(h.category == "handoff_unverified" for h in hints)


def test_entity_get_verified_marker_handoff(
    session_env: dict[str, Path],
) -> None:
    """File-backed section derivation surfaces verified handoff_surface."""
    files_root = session_env["files_root"]
    session_id = "cursor-2026-06-03-120200-a04"
    rel = f"notes/system/transcripts/{session_id}/verified-handoff.md"
    body = "Next: continue the arc."
    src = files_root / rel
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text(
        f"<!-- handoff:start -->\n{body}\n<!-- handoff:end -->\n",
        encoding="utf-8",
    )
    jsonl = session_env["transcripts_root"] / f"{session_id}.jsonl"
    _write_jsonl(jsonl)
    result = ops_journals._op_session_close(
        session_id=session_id,
        agent="cursor",
        transcript_jsonl_path=str(jsonl),
        session_summary_md=_session_summary("Verified handoff surface test."),
        summary="Verified handoff surface test — long enough summary.",
        handoff_source_path=rel,
        expected_handoff_prompt=body,
        handoff_prompt=_anchored_handoff(session_id, body),
    )
    assert "error" not in result, result

    entity = _query_one(
        session_env["db_path"],
        "SELECT attributes FROM entities WHERE id = ?",
        (f"transcript:{session_id}",),
    )
    assert entity is not None
    attrs = json.loads(entity["attributes"])
    projected, hints = apply_handoff_read_projection(
        {"id": f"transcript:{session_id}", "attributes": attrs},
    )
    surface = projected["attributes"]["handoff_surface"]
    assert surface["verified"] is True
    assert surface["derivation"] == "section"
    assert surface["source_file"] == rel
    assert "flag" not in surface
    assert hints is None or hints == []
