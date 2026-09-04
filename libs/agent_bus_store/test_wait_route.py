"""HTTP tests for GET /threads/{thread_id}/wait."""

from __future__ import annotations

from fastapi.testclient import TestClient

from agent_bus_store import create_app
from agent_bus_store.auth import require_token


def _app(tmp_path):
    app = create_app(db_path=str(tmp_path / "bus.db"))
    app.dependency_overrides[require_token] = lambda: None
    return app


def test_wait_zero_returns_snapshot_no_new_turn(tmp_path) -> None:
    with TestClient(_app(tmp_path)) as client:
        created = client.post(
            "/threads/with-turn",
            json={
                "slug": "wait-snapshot-test",
                "from": "claude-cursor",
                "to": "web",
                "subject": "handoff",
                "body": "brief",
            },
        )
        assert created.status_code == 201
        thread_id = created.json()["thread"]["id"]

        resp = client.get(
            f"/threads/{thread_id}/wait"
            "?after_turn=1&wait=0&completion=first_reply_from&from_agent=claude-web"
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "no_new_turn"
        assert body["complete"] is False
        assert body["push_required"] is False
        assert body["thread_id"] == thread_id


def test_wait_complete_after_qualifying_reply(tmp_path) -> None:
    with TestClient(_app(tmp_path)) as client:
        created = client.post(
            "/threads/with-turn",
            json={
                "slug": "wait-complete-test",
                "from": "claude-cursor",
                "to": "web",
                "subject": "handoff",
                "body": "brief",
                "tags": ["bus_lifecycle:persistent"],
            },
        )
        assert created.status_code == 201
        thread_id = created.json()["thread"]["id"]

        reply = client.post(
            "/turns",
            json={
                "thread": thread_id,
                "from": "claude-web",
                "to": "cursor",
                "subject": "re: handoff",
                "body": "done",
                "after_turn": 1,
            },
        )
        assert reply.status_code == 201

        resp = client.get(
            f"/threads/{thread_id}/wait"
            "?after_turn=1&wait=0&completion=first_reply_from&from_agent=claude-web"
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "complete"
        assert body["complete"] is True
        assert body["qualifying_reply_turn"] == 2
        nudge = body["suggested_next"]
        assert nudge is not None
        assert nudge["phase"] == "consult_turn_posted"
        assert nudge["consult_turn"] == 2
        assert nudge["pointer_turn"] == 1
        assert any(s["action"] == "close_handoff_thread" for s in nudge["steps"])


def test_wait_suggested_next_absent_when_thread_closed(tmp_path) -> None:
    with TestClient(_app(tmp_path)) as client:
        created = client.post(
            "/threads/with-turn",
            json={
                "slug": "wait-closed-test",
                "from": "claude-cursor",
                "to": "web",
                "subject": "handoff",
                "body": "brief",
            },
        )
        thread_id = created.json()["thread"]["id"]
        client.post(
            "/turns",
            json={
                "thread": thread_id,
                "from": "claude-web",
                "to": "cursor",
                "subject": "re",
                "body": "done",
                "after_turn": 1,
            },
        )
        close = client.patch(
            f"/threads/{thread_id}/close",
            json={"summary": "done"},
        )
        assert close.status_code == 200
        resp = client.get(
            f"/threads/{thread_id}/wait"
            "?after_turn=1&wait=0&completion=first_reply_from&from_agent=claude-web"
        )
        body = resp.json()
        assert body["complete"] is True
        assert body["suggested_next"] is None


def test_wait_alias_canonical_hint_legacy_reply(tmp_path) -> None:
    with TestClient(_app(tmp_path)) as client:
        created = client.post(
            "/threads/with-turn",
            json={
                "slug": "wait-alias-test",
                "from": "claude-cursor",
                "to": "claude-cursor",
                "subject": "handoff",
                "body": "brief",
            },
        )
        thread_id = created.json()["thread"]["id"]
        client.post(
            "/turns",
            json={
                "thread": thread_id,
                "from": "cursor",
                "to": "claude-cursor",
                "subject": "re",
                "body": "findings",
                "after_turn": 1,
            },
        )
        resp = client.get(
            f"/threads/{thread_id}/wait"
            "?after_turn=1&wait=0&completion=first_reply_from&from_agent=claude-cursor"
        )
        body = resp.json()
        assert body["complete"] is True
        assert body["qualifying_reply_turn"] == 2


def test_wait_disposition_one_correction_dead_wait_422(tmp_path) -> None:
    with TestClient(_app(tmp_path)) as client:
        created = client.post(
            "/threads/with-turn",
            json={
                "slug": "wait-dead-wait-test",
                "from": "web-anthropic",
                "to": "cursor",
                "subject": "DISPOSITION CLOSEOUT — one correction",
                "body": (
                    "TYPE: DISPOSITION\n"
                    "verdict: one correction\n\n"
                    "Amend the closeout fields.\n"
                ),
            },
        )
        assert created.status_code == 201
        thread_id = created.json()["thread"]["id"]

        resp = client.get(
            f"/threads/{thread_id}/wait"
            "?after_turn=1&wait=55&completion=first_reply_from&from_agent=cursor"
        )
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["error"] == "dead_wait_no_auto_producer"
        assert detail["pointer_turn"] == 1
        assert "agent_bus.request" in detail["message"]


def test_wait_disposition_ratify_does_not_422(tmp_path) -> None:
    with TestClient(_app(tmp_path)) as client:
        created = client.post(
            "/threads/with-turn",
            json={
                "slug": "wait-ratify-ok",
                "from": "web-anthropic",
                "to": "cursor",
                "subject": "DISPOSITION — RATIFIED",
                "body": "TYPE: DISPOSITION\nverdict: ratify\n",
            },
        )
        thread_id = created.json()["thread"]["id"]
        resp = client.get(
            f"/threads/{thread_id}/wait"
            "?after_turn=1&wait=0&completion=first_reply_from&from_agent=cursor"
        )
        assert resp.status_code == 200
        assert resp.json()["complete"] is False


def test_wait_status_done_with_admit_turn_is_predicate_unmet(tmp_path) -> None:
    """HTTP snapshot: status:done wait over an admit turn is not no_new_turn."""
    with TestClient(_app(tmp_path)) as client:
        created = client.post(
            "/threads/with-turn",
            json={
                "slug": "wait-predicate-unmet",
                "from": "web-anthropic",
                "to": "cursor-auto",
                "subject": "request",
                "body": "DIRECTIVE",
            },
        )
        assert created.status_code == 201
        thread_id = created.json()["thread"]["id"]
        admit = client.post(
            "/turns",
            json={
                "thread": thread_id,
                "from": "cursor-auto",
                "to": "web-anthropic",
                "subject": "status:admitted — nested dispatch",
                "body": "admitted",
                "after_turn": 1,
            },
        )
        assert admit.status_code == 201

        empty = client.get(
            f"/threads/{thread_id}/wait"
            "?after_turn=2&wait=0&completion=status:done"
        )
        assert empty.status_code == 200
        assert empty.json()["status"] == "no_new_turn"
        assert empty.json()["complete"] is False
        assert empty.json()["qualifying_reply_turn"] is None

        advanced = client.get(
            f"/threads/{thread_id}/wait"
            "?after_turn=1&wait=0&completion=status:done"
        )
        assert advanced.status_code == 200
        body = advanced.json()
        assert body["status"] == "predicate_unmet"
        assert body["complete"] is False
        assert body["turn_count"] == 2
        assert body["qualifying_reply_turn"] is None


def test_wait_proof_reply_from_specimen_346_predicate_unmet(tmp_path) -> None:
    from chat_harvest.test_chrome import SPECIMEN_346_BODY

    with TestClient(_app(tmp_path)) as client:
        created = client.post(
            "/threads/with-turn",
            json={
                "slug": "wait-proof-chrome-stub",
                "from": "claude-cursor",
                "to": "web-anthropic",
                "subject": "handoff",
                "body": "brief",
            },
        )
        thread_id = created.json()["thread"]["id"]
        client.post(
            "/turns",
            json={
                "thread": thread_id,
                "from": "web-anthropic",
                "to": "cursor",
                "subject": "cdp reply — a76a67d3",
                "body": SPECIMEN_346_BODY,
                "after_turn": 1,
            },
        )
        resp = client.get(
            f"/threads/{thread_id}/wait"
            "?after_turn=1&wait=0&completion=proof_reply_from&from_agent=web-anthropic"
        )
        body = resp.json()
        assert body["status"] == "predicate_unmet"
        assert body["complete"] is False
        assert body["qualifying_reply_turn"] is None


def test_wait_proof_reply_from_specimen_347_complete(tmp_path) -> None:
    from chat_harvest.test_chrome import SPECIMEN_347_BODY

    with TestClient(_app(tmp_path)) as client:
        created = client.post(
            "/threads/with-turn",
            json={
                "slug": "wait-proof-substantive",
                "from": "claude-cursor",
                "to": "web-anthropic",
                "subject": "handoff",
                "body": "brief",
            },
        )
        thread_id = created.json()["thread"]["id"]
        client.post(
            "/turns",
            json={
                "thread": thread_id,
                "from": "web-anthropic",
                "to": "cursor",
                "subject": "cdp reply — b87b78e4",
                "body": SPECIMEN_347_BODY,
                "after_turn": 1,
            },
        )
        resp = client.get(
            f"/threads/{thread_id}/wait"
            "?after_turn=1&wait=0&completion=proof_reply_from&from_agent=web-anthropic"
        )
        body = resp.json()
        assert body["status"] == "complete"
        assert body["complete"] is True
        assert body["qualifying_reply_turn"] == 2
