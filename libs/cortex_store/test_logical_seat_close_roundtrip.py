"""Logical-seat close → continuity roundtrip (local TestClient DB)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from cortex_store.conftest import bind_cortex_db
from cortex_store.db import cortex_conn
from cortex_store.models import SessionCloseRequest
from cortex_store.routes.session_close import close_session
from cortex_store.routes.session_close_persist import try_idempotent_session_close
from cortex_store.routes.session_close_validate import validate_session_close


def _minimal_fields(**overrides: object) -> dict:
    base = {
        "summary": "Arc: logical seat alpha-work close roundtrip validation.",
        "session_summary_md": "## Session Summary\n\nLogical seat close body.",
        "depth": "light",
    }
    base.update(overrides)
    return base


def _stage(client: TestClient, session_id: str, agent: str) -> None:
    resp = client.post("/close/stage", json={"session_id": session_id, "agent": agent})
    assert resp.status_code == 200, resp.text


def _draft(client: TestClient, session_id: str, fields: dict) -> None:
    resp = client.post("/close/draft", json={"session_id": session_id, "fields": fields})
    assert resp.status_code == 200, resp.text


def _check(client: TestClient, session_id: str) -> dict:
    resp = client.post("/close/check", json={"session_id": session_id})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _commit(client: TestClient, session_id: str, revision: int) -> dict:
    resp = client.post(
        "/close/commit",
        json={"session_id": session_id, "checked_revision": revision},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_logical_seat_close_draft_lifecycle_and_agent_column(
    cortex_client: TestClient,
    session_env: dict,
) -> None:
    alpha_id = "alpha-work-2026-06-10-012830-a1b"
    _stage(cortex_client, alpha_id, "alpha-work")
    _draft(cortex_client, alpha_id, _minimal_fields())
    check = _check(cortex_client, alpha_id)
    assert check["status"] == "PASS"
    _commit(cortex_client, alpha_id, check["checked_revision"])

    with cortex_conn() as conn:
        row = conn.execute(
            "SELECT agent FROM session_lids WHERE session_id = ?",
            (alpha_id,),
        ).fetchone()
    assert row is not None
    assert row["agent"] == "alpha-work"

    beta_id = "beta-work-2026-06-11-012830-d2c"
    _stage(cortex_client, beta_id, "beta-work")
    _draft(cortex_client, beta_id, _minimal_fields(summary="Arc: newer foreign beta seat."))
    check_b = _check(cortex_client, beta_id)
    _commit(cortex_client, beta_id, check_b["checked_revision"])

    cont = cortex_client.get("/boot-continuity", params={"agent": "alpha-work"})
    assert cont.status_code == 200
    body = cont.json()
    last = (body.get("last_session") or {}).get("session_id")
    assert last == alpha_id
    assert beta_id not in str(body)

    journals = cortex_client.get("/session-journals", params={"agent": "alpha-work"})
    assert journals.status_code == 200
    payload = journals.json()
    rows = payload.get("items", []) if isinstance(payload, dict) else payload
    agents = {r.get("agent") for r in rows if isinstance(r, dict)}
    assert agents <= {"alpha-work"}
    assert beta_id not in {r.get("session_id") for r in rows if isinstance(r, dict)}


def test_logical_seat_idempotent_recommit(
    cortex_client: TestClient,
) -> None:
    sid = "alpha-work-2026-06-12-012830-e3f"
    _stage(cortex_client, sid, "alpha-work")
    _draft(cortex_client, sid, _minimal_fields())
    check = _check(cortex_client, sid)
    rev = check["checked_revision"]
    first = cortex_client.post(
        "/close/commit",
        json={"session_id": sid, "checked_revision": rev},
    )
    assert first.status_code == 201
    second = cortex_client.post(
        "/close/commit",
        json={"session_id": sid, "checked_revision": rev},
    )
    assert second.status_code == 201
    assert second.json().get("already_closed") is True


def test_atomic_close_idempotent_twice_one_journal_row(
    session_env: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bind_cortex_db(monkeypatch, session_env["db_path"])
    session_id = "alpha-work-2026-06-13-012830-f4a"
    body = SessionCloseRequest(
        session_id=session_id,
        agent="alpha-work",
        session_summary_md="## Session Summary\n\nAtomic close roundtrip.",
        summary="Atomic close roundtrip for logical alpha-work.",
        transcript_depth="light",
    )
    ctx = validate_session_close(body)
    first, _ = try_idempotent_session_close(body, ctx)
    assert first is None
    close_session(body)
    second, _ = try_idempotent_session_close(body, ctx)
    assert second is not None
    assert second.already_closed is True

    with cortex_conn() as conn:
        count = conn.execute(
            "SELECT COUNT(*) AS n FROM session_journals WHERE session_id = ?",
            (session_id,),
        ).fetchone()["n"]
        journal_id = conn.execute(
            "SELECT id FROM session_journals WHERE session_id = ?",
            (session_id,),
        ).fetchone()["id"]
    assert count == 1
    third, _ = try_idempotent_session_close(body, ctx)
    assert third is not None
    with cortex_conn() as conn:
        journal_id_2 = conn.execute(
            "SELECT id FROM session_journals WHERE session_id = ?",
            (session_id,),
        ).fetchone()["id"]
    assert journal_id_2 == journal_id
