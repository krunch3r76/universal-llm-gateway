from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from cortex_store.dispatch_ops import ops_journals
from cortex_store.routes import session_close_persist


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


def _anchored_handoff(session_id: str, body: str) -> str:
    return (
        f"**Closing session:** transcript:{session_id}\n"
        f"**Load context:** fs(cortex, op=read, path=notes/system/transcripts/"
        f"{session_id}.md)\n"
        f"{body}"
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
    session_id = "orion-2026-05-04-084400-a01"
    handoff = _anchored_handoff(
        session_id,
        "Start with the openapi and tool-doc pass, then verify rollback tests.",
    )

    result = ops_journals._op_session_close(
        **_payload(
            session_id=session_id,
            prior_session_id="orion-2026-05-04-070000-a02",
            handoff_prompt=handoff,
            transcripts_root=session_env["transcripts_root"],
        )
    )

    # The retired RJ-row id is absent from the response; journal_row_id is the durable handle.
    assert ("handoff_" + "entry_id") not in result
    assert result["transcript_entity_id"] == "transcript:orion-2026-05-04-084400-a01"
    assert (files_root / result["transcript_path"]).is_file()

    journal = _query_one(
        db_path,
        "SELECT * FROM session_journals WHERE session_id = ?",
        ("orion-2026-05-04-084400-a01",),
    )
    assert journal is not None
    assert journal["handoff_prompt"] == handoff

    entity = _query_one(
        db_path,
        "SELECT attributes FROM entities WHERE id = ?",
        ("transcript:orion-2026-05-04-084400-a01",),
    )
    assert entity is not None
    assert json.loads(entity["attributes"])["handoff_prompt"] == handoff

    edge = _query_one(
        db_path,
        "SELECT * FROM session_edges WHERE from_node = ? AND to_node = ? AND edge_type = 'continues'",
        (
            "transcript:orion-2026-05-04-084400-a01",
            "transcript:orion-2026-05-04-070000-a02",
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
    session_a = "orion-2026-05-04-070000-a02"
    session_b = "orion-2026-05-04-084700-a03"

    handoff = _anchored_handoff(
        session_a, "Resume from the G2 confirmed+inference policy."
    )
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
            session_id="cursor-2026-05-04-084400-a04",
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
        ("cursor-2026-05-04-084400-a04",),
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
            session_id="orion-2026-05-04-084500-a05",
            prior_session_id="orion-2026-05-04-070000-a02",
            handoff_prompt=_anchored_handoff(
                "orion-2026-05-04-084500-a05", "Resume with rollback verification."
            ),
            transcripts_root=session_env["transcripts_root"],
        )
    )

    assert "Session close failed" in result["error"]
    assert not (
        files_root / "notes/system/transcripts/orion-2026-05-04-084500-a05.md"
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
        **_web_payload(session_id="web-2026-05-17-041000-a06")
    )
    assert "error" not in result, result
    assert result["transcript_entity_id"] == "transcript:web-2026-05-17-041000-a06"
    assert result["turn_count"] == 2
    assert result["content_hash"].startswith("sha256:")
    on_disk = (files_root / result["transcript_path"]).read_text(encoding="utf-8")
    assert "## Turn 1" in on_disk
    assert "## Session Summary" in on_disk
    journal = _query_one(
        db_path,
        "SELECT * FROM session_journals WHERE session_id = ?",
        ("web-2026-05-17-041000-a06",),
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
        session_id="web-2026-05-17-041100-a07",
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
        session_id="cursor-2026-05-17-041200-a08",
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


def test_session_close_rejects_handoff_missing_transcript_anchor(
    session_env: dict[str, Path],
) -> None:
    """Root cause 3: a handoff without the closing-session anchor is a 422,
    not a post-close warning — rejected before any journal row is written."""
    db_path = session_env["db_path"]
    result = ops_journals._op_session_close(
        **_payload(
            session_id="orion-2026-05-04-084800-a09",
            handoff_prompt="Poll agent-bus thread 99; integrate findings.",
            transcripts_root=session_env["transcripts_root"],
        )
    )
    assert "error" in result
    assert result.get("reason") == "handoff.missing_transcript_anchor"
    journal = _query_one(
        db_path,
        "SELECT id FROM session_journals WHERE session_id = ?",
        ("orion-2026-05-04-084800-a09",),
    )
    assert journal is None


def test_session_close_handoff_anchor_via_source_path_no_reject(
    session_env: dict[str, Path],
) -> None:
    """Anchor satisfied when handoff_source_path names the session — no 422."""
    session_id = "orion-2026-05-04-085100-a10"
    files_root = session_env["files_root"]
    handoff_file = files_root / f"notes/system/transcripts/{session_id}.md"
    handoff_file.parent.mkdir(parents=True, exist_ok=True)
    handoff_file.write_text(
        "<!-- handoff:start -->\nPoll agent-bus thread 99.\n<!-- handoff:end -->\n",
        encoding="utf-8",
    )
    payload = _payload(
        session_id=session_id,
        transcripts_root=session_env["transcripts_root"],
    )
    payload["handoff_source_path"] = f"notes/system/transcripts/{session_id}.md"
    payload["handoff_source_section"] = None
    result = ops_journals._op_session_close(**payload)
    assert "error" not in result, result


def test_session_close_handoff_with_transcript_anchor_no_anchor_warning(
    session_env: dict[str, Path],
) -> None:
    session_id = "orion-2026-05-04-084900-a11"
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
            session_id="orion-2026-05-04-070000-a02",
            handoff_prompt=_anchored_handoff(
                "orion-2026-05-04-070000-a02",
                "Next session should continue the handoff capture work.",
            ),
            transcripts_root=session_env["transcripts_root"],
        )
    )
    assert "error" not in first

    second = ops_journals._op_session_close(
        **_payload(
            session_id="orion-2026-05-04-084700-a03",
            handoff_prompt=_anchored_handoff(
                "orion-2026-05-04-084700-a03",
                "Resume with the final documentation pass.",
            ),
            transcripts_root=session_env["transcripts_root"],
        )
    )

    findings = second.get("_warning", {}).get("post_close_findings", [])
    assert any(f["kind"] == "prior_session_id_omitted" for f in findings)


def test_session_close_suppresses_prior_session_warning_without_continuation_claim(
    session_env: dict[str, Path],
) -> None:
    first = ops_journals._op_session_close(
        **_payload(
            session_id="orion-2026-05-04-070000-a02",
            transcripts_root=session_env["transcripts_root"],
        )
    )
    assert "error" not in first

    second = ops_journals._op_session_close(
        **_payload(
            session_id="orion-2026-05-04-084700-a03",
            transcripts_root=session_env["transcripts_root"],
        )
    )
    assert "error" not in second
    findings = second.get("_warning", {}).get("post_close_findings", [])
    assert not any(f["kind"] == "prior_session_id_omitted" for f in findings)


def test_preflight_returns_copy_paste_session_id_when_supplied_differs(
    session_env: dict[str, Path],
) -> None:
    jsonl_path = session_env["transcripts_root"] / "cursor-uuid" / "cursor-uuid.jsonl"
    _write_jsonl(jsonl_path)
    result = ops_journals._op_session_close_preflight(
        session_id="cursor-2099-12-31-235959-fff",
        agent="cursor",
        transcript_jsonl_path=str(jsonl_path),
        session_summary_md=_session_summary("Preflight session_id anchor."),
        summary="Preflight session_id anchor.",
    )
    assert result["ok"] is True
    assert "session_id_from_jsonl_start" in result
    assert result.get("session_id") == result["session_id_from_jsonl_start"]


# ---- transcript_depth dial tests (Phase 1 of session-close-transcript-depth-dial) ----


def test_close_accepts_new_format_session_id(session_env: dict[str, Path]) -> None:
    """Post-13697 session IDs (HHMMSS + 3-hex suffix) pass validation and close."""
    files_root = session_env["files_root"]
    session_id = "claude-cursor-2026-06-10-012830-abc"
    payload = _payload(
        session_id=session_id,
        agent="claude-cursor",
        transcripts_root=session_env["transcripts_root"],
    )
    result = ops_journals._op_session_close(**payload)
    assert "error" not in result, result
    assert result["transcript_entity_id"] == f"transcript:{session_id}"
    assert result["transcript_path"] is not None
    assert (files_root / result["transcript_path"]).is_file()


def test_close_verbatim_default_is_backward_compatible(
    session_env: dict[str, Path],
) -> None:
    """No transcript_depth arg ⟹ defaults to verbatim; response carries field."""
    files_root = session_env["files_root"]
    payload = _payload(
        session_id="cursor-2026-05-27-100000-a12",
        agent="cursor",
        transcripts_root=session_env["transcripts_root"],
    )
    result = ops_journals._op_session_close(**payload)
    assert "error" not in result, result
    assert result["transcript_depth"] == "verbatim"
    assert result["transcript_entity_id"] == "transcript:cursor-2026-05-27-100000-a12"
    assert result["transcript_path"] is not None
    assert (files_root / result["transcript_path"]).is_file()
    assert result["content_hash"].startswith("sha256:")


def test_close_verbatim_explicit(session_env: dict[str, Path]) -> None:
    """transcript_depth='verbatim' explicit ⟹ identical to default."""
    payload = _payload(
        session_id="cursor-2026-05-27-100100-a13",
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
        session_id="web-2026-05-27-100200-a14",
        agent="web",
        transcript_md=_web_transcript_md("web-2026-05-27-100200-a14"),
        session_summary_md=summary_md,
        summary=summary,
        transcript_depth="light",
    )
    assert "error" not in result, result
    assert result["transcript_depth"] == "light"
    assert result["transcript_entity_id"] == "transcript:web-2026-05-27-100200-a14"
    assert result["transcript_path"] is not None
    assert result["turn_count"] == 0
    on_disk = (files_root / result["transcript_path"]).read_text(encoding="utf-8")
    # Light file is the structural layer verbatim — no Turn blocks, no User voice.
    assert on_disk == summary_md
    # Entity attributes carry transcript_depth.
    row = _query_one(
        db_path,
        "SELECT attributes FROM entities WHERE id = ?",
        ("transcript:web-2026-05-27-100200-a14",),
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
        session_id="web-2026-05-27-100300-a15",
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
        session_id="web-2026-05-27-100400-a16",
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
        files_root / "notes/system/transcripts/web-2026-05-27-100400-a16.md"
    ).exists()
    # No transcript entity exists.
    ent = _query_one(
        db_path,
        "SELECT id FROM entities WHERE id = ?",
        ("transcript:web-2026-05-27-100400-a16",),
    )
    assert ent is None
    # Journal row exists with file_path NULL.
    jr = _query_one(
        db_path,
        "SELECT file_path FROM session_journals WHERE session_id = ?",
        ("web-2026-05-27-100400-a16",),
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
        session_id="web-2026-05-27-100500-a17",
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
        ("web-2026-05-27-100500-a17",),
    )
    assert journal is None


def test_close_none_with_handoff_source_path_rejected(
    session_env: dict[str, Path],
) -> None:
    """none + handoff_source_path ⟹ same 422 (derivation still needs an entity)."""
    summary = "None-depth with handoff_source_path — rejected."
    result = ops_journals._op_session_close(
        session_id="web-2026-05-27-101500-a18",
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
    session_id = "web-2026-05-27-101600-a19"
    handoff = _anchored_handoff(
        session_id, "Pick up phase 3 bus handoff — verify thread state first."
    )
    summary = "Light-depth close with handoff mirrored to transcript entity."
    result = ops_journals._op_session_close(
        session_id=session_id,
        agent="web",
        session_summary_md=_session_summary(summary),
        summary=summary,
        transcript_depth="light",
        handoff_prompt=handoff,
    )
    assert "error" not in result, result
    assert result["transcript_entity_id"] == "transcript:web-2026-05-27-101600-a19"
    entity = _query_one(
        session_env["db_path"],
        "SELECT attributes FROM entities WHERE id = ?",
        ("transcript:web-2026-05-27-101600-a19",),
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
        session_id="web-2026-05-27-100600-a20",
        agent="web",
        session_summary_md=_session_summary(summary),
        summary=summary,
        transcript_depth="none",
        prior_session_id="web-2026-05-27-095900-a21",
    )
    assert "error" not in result, result
    edge = _query_one(
        db_path,
        "SELECT * FROM session_edges WHERE from_node = ? AND to_node = ? "
        "AND edge_type = 'continues'",
        (
            "transcript:web-2026-05-27-100600-a20",
            "transcript:web-2026-05-27-095900-a21",
        ),
    )
    assert edge is not None


def test_close_verbatim_missing_source_still_422(
    session_env: dict[str, Path],
) -> None:
    """verbatim default + no source ⟹ 422 transcript_source.missing (preserved)."""
    summary = "Verbatim missing source — still rejected after depth dial lands."
    result = ops_journals._op_session_close(
        session_id="web-2026-05-27-100700-a22",
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
        session_id="cursor-2026-05-27-100800-a23",
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
        session_id="web-2026-05-27-100900-a24",
        agent="web",
        session_summary_md=_session_summary(summary_a),
        summary=summary_a,
        transcript_depth="none",
    )
    assert "error" not in first, first
    assert first["transcript_depth"] == "none"

    summary_b = "Second close attempt — idempotent when handoff unchanged."
    second = ops_journals._op_session_close(
        session_id="web-2026-05-27-100900-a24",
        agent="web",
        transcript_md=_web_transcript_md("web-2026-05-27-100900-a24"),
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
        session_id="web-2026-05-27-101000-a25",
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
        session_id="web-2026-05-27-101100-a26",
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
        files_root / "notes/system/transcripts/web-2026-05-27-101100-a26.md"
    ).exists()
    assert (
        _query_count(
            db_path,
            "SELECT COUNT(*) FROM session_journals WHERE session_id = ?",
            ("web-2026-05-27-101100-a26",),
        )
        == 0
    )
    assert (
        _query_count(
            db_path,
            "SELECT COUNT(*) FROM entities WHERE id = ?",
            ("transcript:web-2026-05-27-101100-a26",),
        )
        == 0
    )


def test_dry_run_missing_anchor_would_fail(
    session_env: dict[str, Path],
) -> None:
    """A — dry_run with handoff_prompt that omits the anchor ⟹ would_fail.

    Before this fix, dry_run was blind to the anchor gate and returned
    would_succeed even when the prompt lacked the closing-session anchor.
    """
    db_path = session_env["db_path"]
    files_root = session_env["files_root"]
    session_id = "web-2026-06-10-120000-abc"
    result = ops_journals._op_session_close(
        session_id=session_id,
        agent="web",
        transcript_md=_web_transcript_md(session_id),
        session_summary_md=_session_summary("Dry-run anchor-gate check."),
        summary="Dry-run anchor-gate check.",
        handoff_prompt="Poll agent-bus and integrate findings.",  # no anchor
        dry_run=True,
    )
    assert result["dry_run"] is True
    assert result.get("would_fail") is True, result
    assert result.get("reason") == "handoff.missing_transcript_anchor", result
    assert "would_succeed" not in result
    # Anchor finding appears in the handoff findings list.
    findings = result.get("findings", [])
    assert any(
        f.get("kind") == "handoff_missing_transcript_anchor" for f in findings
    ), findings
    # No side effects — dry_run writes nothing.
    assert not (files_root / f"notes/system/transcripts/{session_id}.md").exists()
    assert (
        _query_count(
            db_path,
            "SELECT COUNT(*) FROM session_journals WHERE session_id = ?",
            (session_id,),
        )
        == 0
    )


def test_validate_session_close_rejects_missing_anchor(
    session_env: dict[str, Path],
) -> None:
    """B — anchor gate fires inside validate_session_close, not only at persist.

    Proves defense-in-depth: the 422 is reachable from the validation pass
    (before any file/DB write) when handoff_prompt omits the closing-session
    transcript anchor.
    """
    from fastapi import HTTPException

    from cortex_store.models import SessionCloseRequest
    from cortex_store.routes.session_close_validate import validate_session_close

    session_id = "gatherer-2026-06-10-130000-def"
    body = SessionCloseRequest(
        session_id=session_id,
        agent="gatherer",
        session_summary_md=_session_summary("Anchor gate validation-phase check."),
        summary="Anchor gate validation-phase check.",
        transcript_depth="light",
        handoff_prompt="Continue the integration work.",  # no anchor
    )
    with pytest.raises(HTTPException) as exc_info:
        validate_session_close(body)
    detail = exc_info.value.detail
    assert detail.get("reason") == "handoff.missing_transcript_anchor", detail
