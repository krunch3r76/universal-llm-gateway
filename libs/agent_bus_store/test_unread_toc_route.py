"""HTTP tests for GET /turns/unread-toc (recipient-scoped unread digest).

friction 16835: recipient-scoped fetch_unread returned an uncapped List[Turn]
that overflowed the MCP inline response guard at routine multi-thread fan-out
(173.8KB > 128KB, bodies already stripped — the defect was row count). The
digest is bounded by thread count (one row per thread) and carries no turn
bodies. Its per-thread unread_count is recipient-scoped (mirrors get_turns'
recipient/unread/non-superseded filter), NOT the thread-global unread_count on
ThreadDetail.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from agent_bus_store import create_app
from agent_bus_store.auth import require_token


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


def test_unread_toc_one_row_per_thread_recipient_scoped(tmp_path) -> None:
    with TestClient(_app(tmp_path)) as client:
        t1 = _new_thread(
            client, slug="toc-a", frm="cursor", to="web", subject="a1", body="body-a1"
        )
        _add_turn(
            client, thread=t1, frm="cursor", to="web", subject="a2", body="body-a2"
        )
        t2 = _new_thread(
            client, slug="toc-b", frm="cursor", to="web", subject="b1", body="body-b1"
        )
        # A thread whose only unread turn is addressed to someone else must not
        # appear in web's digest (recipient scoping).
        _new_thread(
            client, slug="toc-c", frm="web", to="cursor", subject="c1", body="body-c1"
        )

        resp = client.get("/turns/unread-toc?to=web")
        assert resp.status_code == 200, resp.text
        data = resp.json()

        rows = {row["thread"]: row for row in data["threads"]}
        assert set(rows) == {t1, t2}
        assert rows[t1]["unread_count"] == 2
        assert rows[t2]["unread_count"] == 1
        assert data["total_unread_threads"] == 2
        assert data["total_unread_turns"] == 3
        assert data["marked_read"] == 0
        # Sparse digest: only routing keys, no descriptive/heavy fields (the
        # per-row strings were what bloated the recipient fan-out).
        assert set(rows[t1]) == {"thread", "unread_count", "latest_turn_number"}
        for absent in ("body", "slug", "latest_subject", "latest_from", "latest_to"):
            assert absent not in rows[t1]
        assert rows[t1]["latest_turn_number"] == 2


def test_unread_toc_mark_read_clears_inbox(tmp_path) -> None:
    with TestClient(_app(tmp_path)) as client:
        t1 = _new_thread(
            client, slug="toc-mr", frm="cursor", to="web", subject="m1", body="b1"
        )
        _add_turn(client, thread=t1, frm="cursor", to="web", subject="m2", body="b2")

        # First call clears and reports what was cleared.
        resp = client.get("/turns/unread-toc?to=web&mark_read=true")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["total_unread_turns"] == 2
        assert data["marked_read"] == 2

        # Second call: inbox now empty.
        resp2 = client.get("/turns/unread-toc?to=web")
        data2 = resp2.json()
        assert data2["threads"] == []
        assert data2["total_unread_turns"] == 0
        assert data2["total_unread_threads"] == 0


def test_unread_toc_empty_when_no_unread(tmp_path) -> None:
    with TestClient(_app(tmp_path)) as client:
        resp = client.get("/turns/unread-toc?to=web")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["threads"] == []
        assert data["total_unread_turns"] == 0
        assert data["marked_read"] == 0
