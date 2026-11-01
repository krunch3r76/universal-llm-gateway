"""Logical-seat close → continuity roundtrip (local TestClient DB)."""

from __future__ import annotations

import sys
import threading
from pathlib import Path
from typing import Any

from agent_seat.boot_identity import BootIdentity, resolve_boot_identity_for_seat
from agent_seat.session_id import SessionMintMode, mint_session_id
from fastapi.testclient import TestClient

from cortex_store.db import cortex_conn
from cortex_store.models import SessionCloseRequest
from cortex_store.routes.session_close import close_session

_MCP_ROOT = Path(__file__).resolve().parents[2] / "services" / "mcp-server"
if str(_MCP_ROOT) not in sys.path:
    sys.path.insert(0, str(_MCP_ROOT))


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
    resp = client.post(
        "/close/draft", json={"session_id": session_id, "fields": fields}
    )
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
    alpha_id = mint_session_id("alpha-work", mode=SessionMintMode.LIVE)
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
    with cortex_conn() as conn:
        journal = conn.execute(
            "SELECT id, summary FROM session_journals WHERE session_id = ?",
            (alpha_id,),
        ).fetchone()
    assert journal is not None
    assert journal["summary"]

    beta_id = mint_session_id("beta-work", mode=SessionMintMode.LIVE)
    _stage(cortex_client, beta_id, "beta-work")
    _draft(
        cortex_client, beta_id, _minimal_fields(summary="Arc: newer foreign beta seat.")
    )
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
    with cortex_conn() as conn:
        rows = conn.execute(
            "SELECT id FROM session_journals WHERE session_id = ?",
            (sid,),
        ).fetchall()
    assert len(rows) == 1
    again = cortex_client.post(
        "/close/commit",
        json={"session_id": sid, "checked_revision": rev},
    )
    assert again.status_code == 201
    with cortex_conn() as conn:
        rows_again = conn.execute(
            "SELECT id FROM session_journals WHERE session_id = ?",
            (sid,),
        ).fetchall()
    assert [row["id"] for row in rows_again] == [row["id"] for row in rows]


def test_atomic_close_route_idempotent_twice_one_journal_row(
    cortex_client: TestClient,
) -> None:
    session_id = mint_session_id("alpha-work", mode=SessionMintMode.LIVE)
    payload = {
        "session_id": session_id,
        "agent": "alpha-work",
        "session_summary_md": "## Session Summary\n\nAtomic close roundtrip.",
        "summary": "Atomic close roundtrip for logical alpha-work.",
        "transcript_depth": "light",
    }
    first = cortex_client.post("/session-journals/close", json=payload)
    assert first.status_code == 201, first.text
    second = cortex_client.post("/session-journals/close", json=payload)
    assert second.status_code == 201, second.text
    assert second.json().get("already_closed") is True

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
    third = cortex_client.post("/session-journals/close", json=payload)
    assert third.status_code == 201
    with cortex_conn() as conn:
        journal_id_2 = conn.execute(
            "SELECT id FROM session_journals WHERE session_id = ?",
            (session_id,),
        ).fetchone()["id"]
    assert journal_id_2 == journal_id


def test_atomic_close_empty_summary_rejected(cortex_client: TestClient) -> None:
    session_id = mint_session_id("alpha-work", mode=SessionMintMode.LIVE)
    resp = cortex_client.post(
        "/session-journals/close",
        json={
            "session_id": session_id,
            "agent": "alpha-work",
            "session_summary_md": "",
            "summary": "",
            "transcript_depth": "light",
        },
    )
    assert resp.status_code == 422


def test_close_session_handler_twice_preserves_one_journal_row(
    cortex_client: TestClient,
) -> None:
    """Breaks when a second atomic close inserts another journal row or a new id."""
    session_id = mint_session_id("alpha-work", mode=SessionMintMode.LIVE)
    body = SessionCloseRequest.model_validate(
        {
            "session_id": session_id,
            "agent": "alpha-work",
            "session_summary_md": "## Session Summary\n\nHandler-level atomic close.",
            "summary": "Handler-level atomic close for logical alpha-work.",
            "transcript_depth": "light",
        }
    )
    first = close_session(body)
    second = close_session(body)
    assert second.already_closed is True
    assert first.session_id == second.session_id == session_id
    with cortex_conn() as conn:
        rows = conn.execute(
            "SELECT id, summary FROM session_journals WHERE session_id = ?",
            (session_id,),
        ).fetchall()
    assert len(rows) == 1
    assert rows[0]["summary"]
    third = close_session(body)
    with cortex_conn() as conn:
        again = conn.execute(
            "SELECT id FROM session_journals WHERE session_id = ?",
            (session_id,),
        ).fetchall()
    assert [row["id"] for row in again] == [rows[0]["id"]]
    assert third.already_closed is True


def _registry(tmp_path: Path, monkeypatch: Any) -> None:
    cfg = tmp_path / "boot-seats.yaml"
    cfg.write_text(
        """
schema_version: 1
seats:
  alpha-work:
    family: gpt
    platform: web
    inbox_recipient: alpha-inbox
  beta-work:
    family: gpt
    platform: web
    inbox_recipient: beta-inbox
  gamma-work:
    family: gpt
    platform: web
    inbox_recipient: gamma-inbox
""".strip()
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CORTEX_BOOT_IDENTITIES_PATH", str(cfg))


def _seat(name: str) -> BootIdentity:
    resolved = resolve_boot_identity_for_seat(name)
    assert isinstance(resolved, BootIdentity)
    return resolved


def test_next_brief_on_same_client_separates_seat_history(
    cortex_client: TestClient,
    session_env: dict,
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    """Breaks when the next brief reads a different DB or shows another seat's session."""
    del session_env
    _registry(tmp_path, monkeypatch)
    alpha_id = mint_session_id("alpha-work", mode=SessionMintMode.LIVE)
    _stage(cortex_client, alpha_id, "alpha-work")
    _draft(
        cortex_client,
        alpha_id,
        _minimal_fields(summary="Arc: alpha-owned history for this logical seat."),
    )
    check_a = _check(cortex_client, alpha_id)
    assert check_a["status"] == "PASS"
    assert isinstance(check_a["checked_revision"], int)
    _commit(cortex_client, alpha_id, check_a["checked_revision"])
    beta_id = mint_session_id("beta-work", mode=SessionMintMode.LIVE)
    _stage(cortex_client, beta_id, "beta-work")
    _draft(
        cortex_client,
        beta_id,
        _minimal_fields(summary="Arc: beta-owned history for this logical seat."),
    )
    check_b = _check(cortex_client, beta_id)
    _commit(cortex_client, beta_id, check_b["checked_revision"])

    from tools.cortex_named_tools import _boot_data_fetch as data_fetch
    from tools.cortex_named_tools import _boot_runner as boot_runner
    from tools.cortex_named_tools._boot_runner import BootMode, run_cortex_brief

    lock = threading.Lock()
    continuity_error = {"on": False}

    def _cx(method: str, path: str, *_a: Any, **_k: Any) -> Any:
        assert method == "GET"
        if continuity_error["on"] and path.startswith("/boot-continuity"):
            return {"error": "continuity_down"}
        with lock:
            resp = cortex_client.get(path)
        if resp.status_code >= 400:
            return {"error": resp.text, "status": resp.status_code}
        return resp.json()

    def _relay(_sandbox: str, method: str, path: str, *_a: Any, **_k: Any) -> dict:
        return {
            "threads": [],
            "total_unread_threads": 0,
            "total_unread_turns": 0,
            "active_since": "14d",
        }

    monkeypatch.setattr(data_fetch, "cortex_cx", _cx)
    monkeypatch.setattr(data_fetch, "_relay", _relay)
    monkeypatch.setattr(
        data_fetch, "_fetch_async_dispatches_from_events", lambda _agent: []
    )
    monkeypatch.setattr(boot_runner, "_list_files", lambda _prefix: {"files": []})
    monkeypatch.setattr(boot_runner, "resolve_transcript", lambda _tid: None)
    monkeypatch.setattr(boot_runner, "record", lambda *_a, **_k: None)
    monkeypatch.setattr(boot_runner, "_OPS_CONTEXT_DIR", tmp_path / "ops")
    monkeypatch.setattr(boot_runner, "_SKILLS_INDEX_DIR", tmp_path / "skills")

    def _brief(seat: str) -> str:
        result = run_cortex_brief(identity=_seat(seat), mode=BootMode.INSPECT)
        assert result["session_identity"]["agent"] == seat
        return result["briefing_card"]

    alpha_card = _brief("alpha-work")
    beta_card = _brief("beta-work")
    gamma_card = _brief("gamma-work")
    assert alpha_id in alpha_card
    assert "alpha-owned history" in alpha_card
    assert beta_id not in alpha_card
    assert beta_id in beta_card
    assert "beta-owned history" in beta_card
    assert alpha_id not in beta_card
    assert alpha_id not in gamma_card
    assert beta_id not in gamma_card

    continuity_error["on"] = True
    failed = _brief("alpha-work")
    assert beta_id not in failed
    assert "beta-owned history" not in failed
