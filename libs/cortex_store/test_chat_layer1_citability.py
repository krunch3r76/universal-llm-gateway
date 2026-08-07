"""Arc 6897 — chat-as-layer-1 citability (S1/S2/S3 + Gap A/B)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from cortex_store.close_draft.depth_defaults import (
    SPEECH_SEAT_AGENTS,
    default_depth_for_agent,
)
from cortex_store.close_draft.store import create_draft
from cortex_store.db import cortex_conn, json_encode
from cortex_store.dispatch_ops import ops_misc
from cortex_store.routes.session_close_validate import validate_session_close
from cortex_store.transcript_evidence_validate import validate_transcript_evidence_uris
from cortex_store.transcript_assembly import TURN_HEADING_RE, validate_transcript_turn_grammar
from cortex_store.transcript_turn_resolve import (
    TranscriptResolveError,
    resolve_transcript_turn,
)
from cortex_store.models import SessionCloseRequest

pytestmark = pytest.mark.offline


def _session_summary(summary: str) -> str:
    return (
        "## Session Summary\n\n"
        f"**Decisions:** {summary}\n"
        "**Open items:** none.\n"
    )


def _web_verbatim_md(session_id: str, *, turn_lines: list[str] | None = None) -> str:
    if turn_lines is None:
        turn_lines = [
            "## Turn 1 — kickoff",
            "## Turn 2 — followup",
        ]
    body_parts = [f"# Transcript: {session_id}", ""]
    for idx, heading in enumerate(turn_lines, start=1):
        body_parts.extend(
            [
                heading,
                "",
                "### User",
                "",
                f"User message for turn {idx}.",
                "",
                "### Assistant",
                "",
                f"Assistant reply for turn {idx}.",
                "",
            ]
        )
    return "\n".join(body_parts)


def _patch_files_root(monkeypatch: pytest.MonkeyPatch, files_root: Path) -> None:
    root = str(files_root)
    monkeypatch.setattr("cortex_store.rag_resolver._FILES_ROOT", root)
    monkeypatch.setattr(
        "implement_admission.closeout_helpers.cortex_files_root",
        lambda: files_root,
    )


def _seed_entity(
    *,
    session_id: str,
    depth: str,
    files_root: Path,
    transcript_md: str,
) -> None:
    rel = f"notes/system/transcripts/{session_id}.md"
    path = files_root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(transcript_md, encoding="utf-8")
    attrs = json_encode(
        {
            "transcript_depth": depth,
            "closed_at": "2026-08-07T00:00:00Z",
        }
    )
    with cortex_conn() as conn:
        conn.execute(
            "INSERT INTO entities (id, type, name, description, source_uri, attributes, created_at, updated_at) "
            "VALUES (?, 'transcript', ?, ?, ?, ?, ?, ?)",
            (
                f"transcript:{session_id}",
                "test transcript",
                "test",
                f"files://{rel}",
                attrs,
                "2026-08-07T00:00:00Z",
                "2026-08-07T00:00:00Z",
            ),
        )
        conn.commit()


def _minimal_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> sqlite3.Connection:
    db_path = tmp_path / "minimal.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE entities (
            id TEXT PRIMARY KEY,
            type TEXT,
            name TEXT,
            description TEXT,
            source_uri TEXT,
            attributes TEXT,
            created_at TEXT,
            updated_at TEXT
        );
        CREATE TABLE assertions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_id TEXT,
            claim TEXT,
            confidence TEXT,
            evidence TEXT,
            evidence_uris TEXT,
            derivation_type TEXT,
            observed_at TEXT,
            valid_from TEXT,
            valid_until TEXT,
            superseded_by INTEGER,
            predicate_form TEXT,
            raw_predicate_form TEXT
        );
        CREATE TABLE close_drafts (
            session_id TEXT PRIMARY KEY,
            agent TEXT,
            revision INTEGER,
            fields TEXT,
            ttl_expires_at TEXT,
            created_at TEXT,
            updated_at TEXT,
            committed_at TEXT
        );
        """
    )
    monkeypatch.setattr("cortex_store.db._CORTEX_DB", db_path)
    return conn


# ------------------------------------------------------------------ S1 depth


def test_default_depth_empty_speech_set_is_light() -> None:
    assert SPEECH_SEAT_AGENTS == frozenset()
    assert default_depth_for_agent("web") == "light"
    assert default_depth_for_agent("life") == "light"


def test_default_depth_speech_member_is_verbatim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "cortex_store.close_draft.depth_defaults.SPEECH_SEAT_AGENTS",
        frozenset({"web-anthropic"}),
    )
    assert default_depth_for_agent("web-anthropic") == "verbatim"
    assert default_depth_for_agent("cursor") == "light"


def test_draft_mint_uses_depth_helper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = _minimal_db(tmp_path, monkeypatch)
    draft = create_draft(
        conn,
        session_id="web-2026-08-07-120000-a01",
        agent="web",
    )
    assert draft["fields"]["depth"] == "light"
    conn.close()


# ------------------------------------------------------------------ Gap A grammar


def test_grammar_gate_rejects_non_assembly_heading() -> None:
    bad = _web_verbatim_md("sid", turn_lines=["## Turn 3"])
    err = validate_transcript_turn_grammar(bad)
    assert err is not None
    assert err.reason == "transcript.grammar_invalid"


def test_grammar_gate_accepts_assembly_headings() -> None:
    assert validate_transcript_turn_grammar(_web_verbatim_md("sid")) is None


def test_web_close_rejects_malformed_turn_heading() -> None:
    session_id = "web-2026-08-07-130000-a02"
    summary = "Citability grammar gate rejects malformed web verbatim."
    body = SessionCloseRequest.model_validate(
        {
            "session_id": session_id,
            "agent": "web",
            "transcript_md": _web_verbatim_md(session_id, turn_lines=["## Turn 3"]),
            "session_summary_md": _session_summary(summary),
            "summary": summary,
            "transcript_depth": "verbatim",
        }
    )
    with pytest.raises(Exception) as exc:
        validate_session_close(body)
    assert getattr(exc.value, "status_code", None) == 422
    detail = str(getattr(exc.value, "detail", exc.value))
    assert "transcript.grammar_invalid" in detail or "grammar" in detail.lower()


def test_web_close_accepts_valid_assembly_grammar() -> None:
    session_id = "web-2026-08-07-131000-a03"
    summary = "Citability grammar gate accepts assembly-shaped headings."
    body = SessionCloseRequest.model_validate(
        {
            "session_id": session_id,
            "agent": "web",
            "transcript_md": _web_verbatim_md(session_id),
            "session_summary_md": _session_summary(summary),
            "summary": summary,
            "transcript_depth": "verbatim",
        }
    )
    ctx = validate_session_close(body)
    assert ctx.turn_count == 2


def test_grammar_gate_is_write_time_only_not_on_resolve(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Grandfathered archive on disk: resolve fails turn_out_of_range, not grammar."""
    session_id = "web-2026-08-07-132000-a04"
    files_root = tmp_path / "files"
    files_root.mkdir()
    _minimal_db(tmp_path, monkeypatch)
    _patch_files_root(monkeypatch, files_root)
    _seed_entity(
        session_id=session_id,
        depth="verbatim",
        files_root=files_root,
        transcript_md=_web_verbatim_md(session_id, turn_lines=["## Turn 1 — ok only"]),
    )
    hit = resolve_transcript_turn(f"transcript:{session_id}#turn-1")
    assert "User message for turn 1" in hit["body"]
    with pytest.raises(TranscriptResolveError) as exc:
        resolve_transcript_turn(f"transcript:{session_id}#turn-2")
    assert exc.value.code == "turn_out_of_range"


# ------------------------------------------------------------------ S2 resolve


def test_resolve_transcript_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _minimal_db(tmp_path, monkeypatch)
    _patch_files_root(monkeypatch, tmp_path / "files")
    with pytest.raises(TranscriptResolveError) as exc:
        resolve_transcript_turn("transcript:missing-2026-08-07-140000-a05#turn-1")
    assert exc.value.code == "transcript_absent"
    assert "does not exist" in exc.value.message
    assert "after session_close" in exc.value.message


def test_resolve_transcript_below_verbatim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_id = "web-2026-08-07-141000-a06"
    files_root = tmp_path / "files"
    _minimal_db(tmp_path, monkeypatch)
    _patch_files_root(monkeypatch, files_root)
    _seed_entity(
        session_id=session_id,
        depth="light",
        files_root=files_root,
        transcript_md=_session_summary("light only"),
    )
    with pytest.raises(TranscriptResolveError) as exc:
        resolve_transcript_turn(f"transcript:{session_id}#turn-1")
    assert exc.value.code == "transcript_below_verbatim"
    assert "turn-grain body was never archived" in exc.value.message
    assert "turn missing" not in exc.value.message.lower()


def test_resolve_turn_out_of_range(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_id = "web-2026-08-07-142000-a07"
    files_root = tmp_path / "files"
    _minimal_db(tmp_path, monkeypatch)
    _patch_files_root(monkeypatch, files_root)
    _seed_entity(
        session_id=session_id,
        depth="verbatim",
        files_root=files_root,
        transcript_md=_web_verbatim_md(session_id),
    )
    with pytest.raises(TranscriptResolveError) as exc:
        resolve_transcript_turn(f"transcript:{session_id}#turn-9")
    assert exc.value.code == "turn_out_of_range"


def test_resolve_invalid_turn_number() -> None:
    with pytest.raises(TranscriptResolveError) as exc:
        resolve_transcript_turn("transcript:web-2026-08-07-143000-a08#turn-x")
    assert exc.value.code == "invalid_turn_number"


def test_resolve_hit_returns_user_assistant_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_id = "web-2026-08-07-144000-a09"
    files_root = tmp_path / "files"
    _minimal_db(tmp_path, monkeypatch)
    _patch_files_root(monkeypatch, files_root)
    _seed_entity(
        session_id=session_id,
        depth="verbatim",
        files_root=files_root,
        transcript_md=_web_verbatim_md(session_id),
    )
    out = resolve_transcript_turn(f"transcript:{session_id}#turn-1")
    assert out["resolved"] == "transcript_turn"
    assert "User message for turn 1" in out["body"]
    assert "Assistant reply for turn 1" in out["body"]
    assert "## Turn 1" not in out["body"]


def test_op_resolve_transcript_branch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_id = "web-2026-08-07-145000-a10"
    files_root = tmp_path / "files"
    _minimal_db(tmp_path, monkeypatch)
    _patch_files_root(monkeypatch, files_root)
    _seed_entity(
        session_id=session_id,
        depth="verbatim",
        files_root=files_root,
        transcript_md=_web_verbatim_md(session_id),
    )
    out = ops_misc._op_resolve(uri=f"transcript:{session_id}#turn-2")
    assert out["turn_number"] == 2
    assert "User message for turn 2" in out["body"]


# ------------------------------------------------------------------ S3 evidence


def test_create_rejects_transcript_without_fragment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _minimal_db(tmp_path, monkeypatch)
    with pytest.raises(TranscriptResolveError) as exc:
        validate_transcript_evidence_uris(
            ["transcript:web-2026-08-07-150000-a11"]
        )
    assert exc.value.code == "transcript_missing_turn_fragment"


def test_update_too_early_vs_wrong_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _minimal_db(tmp_path, monkeypatch)
    files_root = tmp_path / "files"
    _patch_files_root(monkeypatch, files_root)
    session_id = "web-2026-08-07-151000-a12"
    with pytest.raises(TranscriptResolveError) as early:
        validate_transcript_evidence_uris([f"transcript:{session_id}#turn-1"])
    assert early.value.code == "transcript_absent"
    assert "after session_close" in early.value.message

    _seed_entity(
        session_id=session_id,
        depth="verbatim",
        files_root=files_root,
        transcript_md=_web_verbatim_md(session_id),
    )
    with pytest.raises(TranscriptResolveError) as wrong:
        validate_transcript_evidence_uris([f"transcript:{session_id}#turn-99"])
    assert wrong.value.code == "turn_out_of_range"

    validate_transcript_evidence_uris([f"transcript:{session_id}#turn-1"])


def test_supersede_rejects_invalid_inherited_transcript_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _minimal_db(tmp_path, monkeypatch)
    session_id = "web-2026-08-07-152000-a13"
    bad_uri = f"transcript:{session_id}#turn-1"
    with pytest.raises(TranscriptResolveError) as exc:
        validate_transcript_evidence_uris([bad_uri])
    assert exc.value.code == "transcript_absent"


def test_turn_heading_re_is_shared_source_of_truth() -> None:
    assert TURN_HEADING_RE.match("## Turn 1 — topic")
    assert not TURN_HEADING_RE.match("## Turn 1")
