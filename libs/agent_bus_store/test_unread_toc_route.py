"""HTTP tests for GET /turns/unread-toc (recipient-scoped unread digest)."""

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


def test_unread_toc_enriched_recipient_scoped(tmp_path) -> None:
    with TestClient(_app(tmp_path)) as client:
        root_resp = client.post("/threads", json={"slug": "toc-root"})
        assert root_resp.status_code == 201, root_resp.text
        root = root_resp.json()["id"]
        t1 = _new_thread(
            client, slug="toc-a", frm="cursor", to="web", subject="a1", body="body-a1"
        )
        bind = client.post(
            f"/threads/{t1}/lane-bind",
            json={"parent_thread_id": root, "lane_role": "sub_mission"},
        )
        assert bind.status_code == 200, bind.text
        _add_turn(
            client, thread=t1, frm="cursor", to="web", subject="a2", body="body-a2"
        )
        t2 = _new_thread(
            client, slug="toc-b", frm="cursor", to="web", subject="b1", body="body-b1"
        )
        _new_thread(
            client, slug="toc-c", frm="web", to="cursor", subject="c1", body="body-c1"
        )

        resp = client.get("/turns/unread-toc?to=web")
        assert resp.status_code == 200, resp.text
        data = resp.json()

        rows = {row["thread"]: row for row in data["threads"]}
        assert set(rows) == {t1, t2}
        assert rows[t1]["parent_thread"] == root
        assert rows[t1]["lane_role"] == "sub_mission"
        assert rows[t2]["parent_thread"] is None
        assert rows[t2]["lane_role"] is None
        assert rows[t1]["unread_count"] == 2
        assert rows[t1]["slug"] == "toc-a"
        assert rows[t1]["last_subject"] == "a2"
        assert "last_activity_at" in rows[t1]
        assert data["total_unread_threads"] == 2
        assert data["total_unread_turns"] == 3
        assert data["marked_read"] == 0
        assert data["truncated"] is False
        assert rows[t1]["latest_turn_number"] == 2


def test_unread_toc_limit_truncated_unwindowed_totals(tmp_path) -> None:
    with TestClient(_app(tmp_path)) as client:
        for i in range(5):
            _new_thread(
                client,
                slug=f"lim-{i}",
                frm="cursor",
                to="web",
                subject=f"s{i}",
                body="b",
            )
        resp = client.get("/turns/unread-toc?to=web&limit=2")
        data = resp.json()
        assert len(data["threads"]) == 2
        assert data["total_unread_threads"] == 5
        assert data["total_unread_turns"] == 5
        assert data["truncated"] is True


def test_unread_toc_active_since_422(tmp_path) -> None:
    with TestClient(_app(tmp_path)) as client:
        bad = client.get("/turns/unread-toc?to=web&active_since=not-a-date")
        assert bad.status_code == 422
        assert bad.json()["detail"]["code"] == "invalid_active_since"


def test_unread_toc_mark_read_clears_inbox(tmp_path) -> None:
    with TestClient(_app(tmp_path)) as client:
        t1 = _new_thread(
            client, slug="toc-mr", frm="cursor", to="web", subject="m1", body="b1"
        )
        _add_turn(client, thread=t1, frm="cursor", to="web", subject="m2", body="b2")

        resp = client.get("/turns/unread-toc?to=web&mark_read=true")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["total_unread_turns"] == 2
        assert data["marked_read"] == 2

        resp2 = client.get("/turns/unread-toc?to=web")
        data2 = resp2.json()
        assert data2["threads"] == []
        assert data2["total_unread_turns"] == 0


def test_unread_toc_empty_when_no_unread(tmp_path) -> None:
    with TestClient(_app(tmp_path)) as client:
        resp = client.get("/turns/unread-toc?to=web")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["threads"] == []
        assert data["total_unread_turns"] == 0
        assert data["marked_read"] == 0
