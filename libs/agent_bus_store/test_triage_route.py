"""E6 tests: bulk triage preview + execute guardrails."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from agent_bus_store import create_app
from agent_bus_store.auth import require_token
from agent_bus_store.db.connection import connect
from agent_bus_store.db.threads import (
    consume_triage_confirm_token,
    issue_triage_confirm_token,
)
from agent_bus_store.turns_models import TRIAGE_THREAD_CAP


def _app(tmp_path):
    app = create_app(db_path=str(tmp_path / "bus.db"))
    app.dependency_overrides[require_token] = lambda: None
    return app


def _new_thread(client, *, slug, frm, to, subject, body):
    resp = client.post(
        "/threads/with-turn",
        json={"slug": slug, "from": frm, "to": to, "subject": subject, "body": body},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["thread"]["id"]


def _add_turn(client, *, thread, frm, to, subject, body):
    resp = client.post(
        "/turns",
        json={
            "thread": thread,
            "from": frm,
            "to": to,
            "subject": subject,
            "body": body,
        },
    )
    assert resp.status_code == 201, resp.text


def _backdate_unread(thread_id: str, *, days: int) -> None:
    old = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    with connect() as conn:
        conn.execute(
            "UPDATE turns SET created_at = ? WHERE thread = ? AND read_at IS NULL",
            (old, thread_id),
        )


def _triage(client, **payload):
    return client.post("/threads/triage", json=payload)


def test_triage_dry_run_returns_confirm_token(tmp_path) -> None:
    with TestClient(_app(tmp_path)) as client:
        thread_id = _new_thread(
            client,
            slug="tri-old",
            frm="web",
            to="cursor",
            subject="stale",
            body="body",
        )
        _backdate_unread(thread_id, days=10)

        resp = _triage(
            client,
            **{
                "from": "cursor",
                "older_than": "7d",
                "action": "mark_read",
                "dry_run": True,
            },
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["total_candidates"] == 1
        assert data["capped"] is False
        assert len(data["candidates"]) == 1
        assert data["candidates"][0]["id"] == thread_id
        assert data["confirm_token"]
        assert data["expires_at"]


def test_triage_floors_422(tmp_path) -> None:
    with TestClient(_app(tmp_path)) as client:
        resp = _triage(
            client,
            **{"from": "cursor", "older_than": "1d", "action": "close", "dry_run": True},
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["code"] == "older_than_below_floor"

        resp2 = _triage(
            client,
            **{
                "from": "cursor",
                "older_than": "12h",
                "action": "mark_read",
                "dry_run": True,
            },
        )
        assert resp2.status_code == 422
        assert resp2.json()["detail"]["code"] == "older_than_below_floor"


def test_triage_excludes_other_recipient_and_broadcast(tmp_path) -> None:
    with TestClient(_app(tmp_path)) as client:
        owned = _new_thread(
            client,
            slug="owned",
            frm="web",
            to="cursor",
            subject="mine",
            body="b",
        )
        mixed = _new_thread(
            client,
            slug="mixed",
            frm="web",
            to="cursor",
            subject="mine",
            body="b",
        )
        _add_turn(
            client,
            thread=mixed,
            frm="web",
            to="web",
            subject="theirs",
            body="other",
        )
        broadcast = _new_thread(
            client,
            slug="bc",
            frm="web",
            to="all",
            subject="all",
            body="b",
        )
        for tid in (owned, mixed, broadcast):
            _backdate_unread(tid, days=10)

        resp = _triage(
            client,
            **{"from": "cursor", "older_than": "8d", "action": "close", "dry_run": True},
        )
        assert resp.status_code == 200, resp.text
        ids = {row["id"] for row in resp.json()["candidates"]}
        assert owned in ids
        assert mixed not in ids
        assert broadcast not in ids


def test_triage_excludes_blocked_and_pending_lifecycle(tmp_path) -> None:
    with TestClient(_app(tmp_path)) as client:
        blocked = _new_thread(
            client,
            slug="blocked",
            frm="web",
            to="cursor",
            subject="b",
            body="b",
        )
        client.patch(f"/threads/{blocked}", json={"status": "blocked"})
        pending = client.post(
            "/threads/with-turn",
            json={
                "slug": "pending",
                "from": "web",
                "to": "cursor",
                "subject": "p",
                "body": "b",
                "lifecycle_state": "pending",
            },
        ).json()["thread"]["id"]
        ok = _new_thread(
            client,
            slug="ok",
            frm="web",
            to="cursor",
            subject="ok",
            body="b",
        )
        for tid in (blocked, pending, ok):
            _backdate_unread(tid, days=10)

        resp = _triage(
            client,
            **{"from": "cursor", "older_than": "7d", "action": "mark_read", "dry_run": True},
        )
        ids = {row["id"] for row in resp.json()["candidates"]}
        assert ok in ids
        assert blocked not in ids
        assert pending not in ids


def test_triage_cap_flag(tmp_path) -> None:
    with TestClient(_app(tmp_path)) as client:
        for i in range(TRIAGE_THREAD_CAP + 3):
            tid = _new_thread(
                client,
                slug=f"cap-{i}",
                frm="web",
                to="cursor",
                subject=f"s{i}",
                body="b",
            )
            _backdate_unread(tid, days=10)

        resp = _triage(
            client,
            **{"from": "cursor", "older_than": "7d", "action": "mark_read", "dry_run": True},
        )
        data = resp.json()
        assert data["total_candidates"] == TRIAGE_THREAD_CAP + 3
        assert data["capped"] is True
        assert len(data["candidates"]) == TRIAGE_THREAD_CAP


def test_triage_confirm_token_binding(tmp_path) -> None:
    with TestClient(_app(tmp_path)) as client:
        thread_id = _new_thread(
            client,
            slug="bind",
            frm="web",
            to="cursor",
            subject="s",
            body="b",
        )
        _backdate_unread(thread_id, days=10)

        preview = _triage(
            client,
            **{"from": "cursor", "older_than": "7d", "action": "mark_read", "dry_run": True},
        ).json()
        token = preview["confirm_token"]

        bad_filter = _triage(
            client,
            **{
                "from": "cursor",
                "older_than": "8d",
                "action": "mark_read",
                "dry_run": False,
                "confirm_token": token,
            },
        )
        assert bad_filter.status_code == 409
        assert bad_filter.json()["detail"]["code"] == "confirm_token_filter_mismatch"

        execute = _triage(
            client,
            **{
                "from": "cursor",
                "older_than": "7d",
                "action": "mark_read",
                "dry_run": False,
                "confirm_token": token,
            },
        )
        assert execute.status_code == 200, execute.text
        body = execute.json()
        assert body["thread_count"] == 1
        assert body["marked_read"] >= 1

        reuse = _triage(
            client,
            **{
                "from": "cursor",
                "older_than": "7d",
                "action": "mark_read",
                "dry_run": False,
                "confirm_token": token,
            },
        )
        assert reuse.status_code == 409
        assert reuse.json()["detail"]["code"] == "confirm_token_invalid"


def test_triage_mark_read_excludes_thread_with_to_all_unread(tmp_path) -> None:
    with TestClient(_app(tmp_path)) as client:
        thread_id = _new_thread(
            client,
            slug="skip-all",
            frm="web",
            to="cursor",
            subject="direct",
            body="b",
        )
        _add_turn(
            client,
            thread=thread_id,
            frm="web",
            to="all",
            subject="broadcast",
            body="bc",
        )
        _backdate_unread(thread_id, days=10)

        preview = _triage(
            client,
            **{"from": "cursor", "older_than": "7d", "action": "mark_read", "dry_run": True},
        )
        assert preview.status_code == 200
        assert preview.json()["total_candidates"] == 0


def test_consume_token_expired_unit() -> None:
    token, _expires = issue_triage_confirm_token(
        agent="cursor",
        action="mark_read",
        older_than="7d",
        status=None,
        candidate_ids=["001"],
    )
    from agent_bus_store.db import threads as threads_mod

    with threads_mod._triage_tokens_lock:
        entry = threads_mod._triage_tokens[token]
        entry.expires_at = datetime.now(UTC) - timedelta(minutes=1)

    status = consume_triage_confirm_token(
        token_id=token,
        agent="cursor",
        action="mark_read",
        older_than="7d",
        status=None,
        candidate_ids=["001"],
    )
    assert status == "expired"
