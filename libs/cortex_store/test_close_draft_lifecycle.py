"""Close draft lifecycle tests — stage/draft/check/commit matrix."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cortex_store.close_draft.reaper import reap_expired_drafts
from cortex_store.conftest import bind_cortex_db
from cortex_store.db import cortex_conn


def _session_id(agent: str = "test-agent") -> str:
    ts = datetime.now(UTC).strftime("%Y-%m-%d-%H%M%S")
    return f"{agent}-{ts}-abc"


def _minimal_fields(**overrides: object) -> dict:
    base = {
        "summary": "Arc: test close draft lifecycle validation run.",
        "session_summary_md": "## Session Summary\n\nTest body for close draft.",
        "depth": "light",
    }
    base.update(overrides)
    return base


def _stage(cortex_client: TestClient, session_id: str, agent: str = "test-agent") -> dict:
    resp = cortex_client.post(
        "/close/stage", json={"session_id": session_id, "agent": agent}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _draft(
    cortex_client: TestClient, session_id: str, fields: dict
) -> dict:
    resp = cortex_client.post(
        "/close/draft", json={"session_id": session_id, "fields": fields}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _check(cortex_client: TestClient, session_id: str) -> dict:
    resp = cortex_client.post("/close/check", json={"session_id": session_id})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _commit(cortex_client: TestClient, session_id: str, revision: int) -> dict:
    resp = cortex_client.post(
        "/close/commit",
        json={"session_id": session_id, "checked_revision": revision},
    )
    return resp


def test_happy_path_stage_draft_check_commit(
    cortex_client: TestClient,
    session_env: dict[str, Path],
) -> None:
    sid = _session_id()
    stage = _stage(cortex_client, sid)
    assert stage["draft_revision"] == 1
    draft = _draft(cortex_client, sid, _minimal_fields())
    assert draft["draft_revision"] == 2
    check = _check(cortex_client, sid)
    assert check["status"] == "PASS"
    rev = check["checked_revision"]
    resp = _commit(cortex_client, sid, rev)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body.get("journal_row_id")
    assert body.get("stop")


def test_commit_without_check_422(cortex_client: TestClient) -> None:
    sid = _session_id()
    _stage(cortex_client, sid)
    _draft(cortex_client, sid, _minimal_fields())
    resp = _commit(cortex_client, sid, 2)
    assert resp.status_code == 422


def test_empty_draft_check_fails_missing_summary(cortex_client: TestClient) -> None:
    """Regression: check must not PASS with only depth=light (friction life close)."""
    sid = _session_id()
    _stage(cortex_client, sid)
    check = _check(cortex_client, sid)
    assert check["status"] == "FAIL"
    codes = {g["code"] for g in check["report"]["gaps"]}
    assert "summary.too_short" in codes
    assert "session_summary.required" in codes


def test_flat_top_level_draft_fields_folded(cortex_client: TestClient) -> None:
    """Flat summary/session_summary_md at request top level must land in fields."""
    sid = _session_id()
    _stage(cortex_client, sid)
    resp = cortex_client.post(
        "/close/draft",
        json={
            "session_id": sid,
            "summary": "Arc: flat alias draft fields must persist for commit.",
            "session_summary_md": "## Session Summary\n\nFlat alias body.",
            "depth": "light",
        },
    )
    assert resp.status_code == 200, resp.text
    fields = resp.json()["fields"]
    assert len(fields.get("summary") or "") >= 20
    assert "## Session Summary" in (fields.get("session_summary_md") or "")
    check = _check(cortex_client, sid)
    assert check["status"] == "PASS", check
    resp_c = _commit(cortex_client, sid, check["checked_revision"])
    assert resp_c.status_code == 201, resp_c.text


def test_commit_rejects_summary_override_extra(cortex_client: TestClient) -> None:
    """summary on commit is forbidden — belongs on draft (extra=forbid)."""
    sid = _session_id()
    _stage(cortex_client, sid)
    _draft(cortex_client, sid, _minimal_fields())
    check = _check(cortex_client, sid)
    assert check["status"] == "PASS"
    resp = cortex_client.post(
        "/close/commit",
        json={
            "session_id": sid,
            "checked_revision": check["checked_revision"],
            "summary": "Arc: this must not be accepted on the commit call itself.",
        },
    )
    assert resp.status_code == 422


def test_stale_revision_after_draft_422(cortex_client: TestClient) -> None:
    sid = _session_id()
    _stage(cortex_client, sid)
    _draft(cortex_client, sid, _minimal_fields())
    check = _check(cortex_client, sid)
    assert check["status"] == "PASS"
    checked_rev = check["checked_revision"]
    _draft(cortex_client, sid, {"summary": "Arc: updated summary after check pass."})
    resp = _commit(cortex_client, sid, checked_rev)
    assert resp.status_code == 422


def test_draft_after_commit_409(cortex_client: TestClient) -> None:
    sid = _session_id()
    _stage(cortex_client, sid)
    _draft(cortex_client, sid, _minimal_fields())
    check = _check(cortex_client, sid)
    resp = _commit(cortex_client, sid, check["checked_revision"])
    assert resp.status_code == 201
    resp2 = cortex_client.post(
        "/close/draft",
        json={"session_id": sid, "fields": {"summary": "Arc: too late."}},
    )
    assert resp2.status_code == 409


def test_idempotent_recommit_success(cortex_client: TestClient) -> None:
    sid = _session_id()
    _stage(cortex_client, sid)
    _draft(cortex_client, sid, _minimal_fields())
    check = _check(cortex_client, sid)
    rev = check["checked_revision"]
    first = _commit(cortex_client, sid, rev)
    assert first.status_code == 201
    second = _commit(cortex_client, sid, rev)
    assert second.status_code == 201
    assert second.json().get("already_closed") is True


def test_graph_write_keys_rejected_422(cortex_client: TestClient) -> None:
    sid = _session_id()
    _stage(cortex_client, sid)
    resp = cortex_client.post(
        "/close/draft",
        json={"session_id": sid, "fields": {"entity": {"id": "todo:foo"}}},
    )
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail.get("reason") == "close_draft.graph_write_forbidden"


def test_verbatim_requires_transcript_path(cortex_client: TestClient) -> None:
    sid = _session_id()
    _stage(cortex_client, sid)
    _draft(
        cortex_client,
        sid,
        _minimal_fields(depth="verbatim"),
    )
    check = _check(cortex_client, sid)
    assert check["status"] == "FAIL"
    codes = {g["code"] for g in check["report"]["gaps"]}
    assert "verbatim.missing_transcript_path" in codes


def test_transcript_path_rejected_at_light_depth(cortex_client: TestClient) -> None:
    sid = _session_id()
    _stage(cortex_client, sid)
    _draft(
        cortex_client,
        sid,
        _minimal_fields(transcript_md_path="notes/system/x.md", depth="light"),
    )
    check = _check(cortex_client, sid)
    assert check["status"] == "FAIL"
    codes = {g["code"] for g in check["report"]["gaps"]}
    assert "transcript_path.depth_mismatch" in codes


def test_reflections_persist_at_commit(
    cortex_client: TestClient,
    session_env: dict[str, Path],
) -> None:
    sid = _session_id()
    _stage(cortex_client, sid)
    _draft(
        cortex_client,
        sid,
        {
            **_minimal_fields(),
            "reflections": [
                {
                    "register": "debug",
                    "entry": "Mid-session reflection for RJ round-trip.",
                    "kind": "reflection",
                }
            ],
        },
    )
    check = _check(cortex_client, sid)
    resp = _commit(cortex_client, sid, check["checked_revision"])
    assert resp.status_code == 201
    with cortex_conn() as conn:
        rows = conn.execute(
            "SELECT entry, session_id FROM reflective_journal WHERE session_id = ?",
            (sid,),
        ).fetchall()
    assert len(rows) >= 1
    assert rows[0]["session_id"] == sid


def test_short_ttl_skips_reflection_bearing_draft(
    cortex_client: TestClient,
    migrated_db_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sid = _session_id()
    _stage(cortex_client, sid)
    _draft(
        cortex_client,
        sid,
        {
            **_minimal_fields(),
            "reflections": [{"register": "r", "entry": "keep me", "kind": "reflection"}],
        },
    )
    with cortex_conn() as conn:
        row = conn.execute(
            "SELECT ttl_expires_at FROM close_drafts WHERE session_id = ?",
            (sid,),
        ).fetchone()
    assert row["ttl_expires_at"] is None


def test_long_stop_flush_then_reap(
    cortex_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sid = _session_id()
    _stage(cortex_client, sid)
    _draft(
        cortex_client,
        sid,
        {
            **_minimal_fields(),
            "reflections": [{"register": "r", "entry": "flush target", "kind": "reflection"}],
        },
    )
    past = (datetime.now(UTC) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with cortex_conn() as conn:
        conn.execute(
            "UPDATE close_drafts SET reflection_flush_after = ? WHERE session_id = ?",
            (past, sid),
        )
        conn.commit()
    result = reap_expired_drafts()
    assert result["reflections_flushed"] >= 1
    with cortex_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM close_drafts WHERE session_id = ?", (sid,)
        ).fetchone()
    assert row is None


def test_single_write_site_for_fields() -> None:
    import subprocess

    repo = Path(__file__).resolve().parents[2]
    proc = subprocess.run(
        [
            "rg",
            "-n",
            "UPDATE close_drafts SET fields",
            str(repo / "libs" / "cortex_store"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    matches = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert len(matches) == 1
    assert "close_draft/store.py" in matches[0]


def test_cap_enforced_on_stage(cortex_client: TestClient) -> None:
    agent = "cap-test-agent"
    for i in range(20):
        sid = f"{agent}-2026-07-12-120000-{i:03x}"
        _stage(cortex_client, sid, agent)
    resp = cortex_client.post(
        "/close/stage",
        json={"session_id": f"{agent}-2026-07-12-120001-000", "agent": agent},
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["reason"] == "close_draft.cap_exceeded"


def test_depth_none_rejected_with_decisions(cortex_client: TestClient) -> None:
    sid = _session_id()
    _stage(cortex_client, sid)
    _draft(
        cortex_client,
        sid,
        _minimal_fields(depth="none", decisions=[{"claim": "settled", "by": "agent"}]),
    )
    check = _check(cortex_client, sid)
    assert check["status"] == "FAIL"
    codes = {g["code"] for g in check["report"]["gaps"]}
    assert "depth.none_with_content" in codes


def test_handoff_requires_depth_not_none(cortex_client: TestClient) -> None:
    sid = _session_id()
    _stage(cortex_client, sid)
    _draft(
        cortex_client,
        sid,
        _minimal_fields(depth="none", handoff="Next session picks up here."),
    )
    check = _check(cortex_client, sid)
    assert check["status"] == "FAIL"
    codes = {g["code"] for g in check["report"]["gaps"]}
    assert "handoff.requires_transcript_entity" in codes


def test_open_todo_in_entity_ids_blocks_check(
    cortex_client: TestClient,
    migrated_db_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cortex_store.db import cortex_conn

    bind_cortex_db(monkeypatch, migrated_db_path)
    todo_id = "todo:life-close-test-open"
    with cortex_conn() as conn:
        conn.execute(
            "INSERT INTO entities (id, type, name, workflow_state, attributes, created_at, updated_at) "
            "VALUES (?, 'todo', 'Open todo gate test', 'open', '{}', datetime('now'), datetime('now'))",
            (todo_id,),
        )
        conn.commit()
    sid = _session_id()
    _stage(cortex_client, sid)
    _draft(cortex_client, sid, _minimal_fields(entity_ids=[todo_id]))
    check = _check(cortex_client, sid)
    assert check["status"] == "FAIL"
    codes = {g["code"] for g in check["report"]["gaps"]}
    assert "todo_reconciliation.required" in codes
    gap = next(g for g in check["report"]["gaps"] if g["code"] == "todo_reconciliation.required")
    assert "Drop open todos" in gap["action"]


def test_concurrency_race_draft_after_check_forces_422(
    cortex_client: TestClient,
) -> None:
    """Interleaved draft-write in check→commit window must 422 (CAS / revision gate)."""
    sid = _session_id()
    _stage(cortex_client, sid)
    _draft(cortex_client, sid, _minimal_fields())
    check = _check(cortex_client, sid)
    assert check["status"] == "PASS"
    checked_rev = check["checked_revision"]
    race = _draft(
        cortex_client,
        sid,
        {"summary": "Arc: concurrent draft bump after check stamped revision."},
    )
    assert race["draft_revision"] > checked_rev
    resp = _commit(cortex_client, sid, checked_rev)
    assert resp.status_code == 422
    assert resp.json()["detail"]["reason"] == "stale_or_unchecked_revision"
