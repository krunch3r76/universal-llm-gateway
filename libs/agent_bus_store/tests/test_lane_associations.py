"""Verification tests for append-only lane parentage associations."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from agent_bus_store.auth import require_token
from agent_bus_store.db import init_db
from agent_bus_store.db.connection import connect
from agent_bus_store.lane_roles import LANE_ROLES
from agent_bus_store.server import create_app
from fastapi.testclient import TestClient


@pytest.fixture()
def bus_client(tmp_path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "bus.db"
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(db_path))
    init_db()
    app = create_app()
    app.dependency_overrides[require_token] = lambda: None
    return TestClient(app)


def _create_lane(client: TestClient, slug: str) -> str:
    resp = client.post("/threads", json={"slug": slug})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def test_fold_newest_row(bus_client: TestClient) -> None:
    child = _create_lane(bus_client, "lane-child")
    parent = _create_lane(bus_client, "lane-parent")

    first = bus_client.post(
        f"/threads/{child}/lane-bind",
        json={"parent_thread_id": parent, "lane_role": "sub_mission"},
    )
    assert first.status_code == 200
    id1 = first.json()["id"]

    second = bus_client.post(
        f"/threads/{child}/lane-bind",
        json={"parent_thread_id": parent, "lane_role": "spillover"},
    )
    assert second.status_code == 200
    assert second.json()["id"] > id1
    assert second.json()["lane_role"] == "spillover"

    current = bus_client.get(f"/threads/{child}/lane-current")
    assert current.json()["state"] == "associated"
    assert current.json()["lane_role"] == "spillover"


def test_rebind_appends_and_flips_fold(bus_client: TestClient) -> None:
    child = _create_lane(bus_client, "rebind-child")
    parent_a = _create_lane(bus_client, "rebind-parent-a")
    parent_b = _create_lane(bus_client, "rebind-parent-b")

    first = bus_client.post(
        f"/threads/{child}/lane-bind",
        json={"parent_thread_id": parent_a, "lane_role": "hop"},
    ).json()
    bus_client.post(
        f"/threads/{child}/lane-bind",
        json={"parent_thread_id": parent_b, "lane_role": "side"},
    )

    with connect() as conn:
        rows = conn.execute(
            "SELECT id, parent_thread_id, lane_role FROM thread_lane_associations "
            "WHERE thread_id = ? ORDER BY id",
            (child,),
        ).fetchall()
    assert len(rows) == 2
    assert rows[0]["id"] == first["id"]
    assert rows[0]["lane_role"] == "hop"
    assert rows[1]["lane_role"] == "side"

    current = bus_client.get(f"/threads/{child}/lane-current").json()
    assert current["parent_thread"] == parent_b


def test_unknown_thread_404(bus_client: TestClient) -> None:
    parent = _create_lane(bus_client, "known-parent")
    resp = bus_client.post(
        "/threads/999999/lane-bind",
        json={"parent_thread_id": parent, "lane_role": "hop"},
    )
    assert resp.status_code == 404


def test_self_parent_rejected(bus_client: TestClient) -> None:
    lane = _create_lane(bus_client, "self-parent")
    resp = bus_client.post(
        f"/threads/{lane}/lane-bind",
        json={"parent_thread_id": lane, "lane_role": "hop"},
    )
    assert resp.status_code == 422


def test_client_ordering_tokens_rejected(bus_client: TestClient) -> None:
    child = _create_lane(bus_client, "token-child")
    parent = _create_lane(bus_client, "token-parent")
    for payload in (
        {"parent_thread_id": parent, "lane_role": "hop", "id": 9},
        {"parent_thread_id": parent, "lane_role": "hop", "seq": 1},
    ):
        resp = bus_client.post(f"/threads/{child}/lane-bind", json=payload)
        assert resp.status_code == 422


def test_invalid_lane_role_quality_envelope(bus_client: TestClient) -> None:
    child = _create_lane(bus_client, "bad-role-child")
    parent = _create_lane(bus_client, "bad-role-parent")
    for role in ("root", "nope"):
        resp = bus_client.post(
            f"/threads/{child}/lane-bind",
            json={"parent_thread_id": parent, "lane_role": role},
        )
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert set(detail) >= {"code", "message", "source", "retryable", "data"}
        assert detail["code"] == "invalid_lane_role"


def test_lane_roles_exclude_root() -> None:
    assert "root" not in LANE_ROLES


def test_delete_child_cascades_association(bus_client: TestClient) -> None:
    child = _create_lane(bus_client, "cascade-child")
    parent = _create_lane(bus_client, "cascade-parent")
    bus_client.post(
        f"/threads/{child}/lane-bind",
        json={"parent_thread_id": parent, "lane_role": "parallel"},
    )
    delete = bus_client.delete(f"/threads/{child}", params={"force": True})
    assert delete.status_code == 200
    with connect() as conn:
        count = conn.execute(
            "SELECT COUNT(*) AS cnt FROM thread_lane_associations WHERE thread_id = ?",
            (child,),
        ).fetchone()["cnt"]
    assert count == 0


def test_delete_parent_restricted(bus_client: TestClient) -> None:
    child = _create_lane(bus_client, "restrict-child")
    parent = _create_lane(bus_client, "restrict-parent")
    bus_client.post(
        f"/threads/{child}/lane-bind",
        json={"parent_thread_id": parent, "lane_role": "dispatch"},
    )
    delete = bus_client.delete(f"/threads/{parent}", params={"force": True})
    assert delete.status_code == 409
    assert delete.json()["detail"]["code"] == "lane_parent_delete_restricted"


def test_thread_get_carries_lane_pair(bus_client: TestClient) -> None:
    child = _create_lane(bus_client, "get-child")
    parent = _create_lane(bus_client, "get-parent")
    bus_client.post(
        f"/threads/{child}/lane-bind",
        json={"parent_thread_id": parent, "lane_role": "sub_mission"},
    )
    detail = bus_client.get(f"/threads/{child}").json()
    assert detail["parent_thread"] == parent
    assert detail["lane_role"] == "sub_mission"

    unbound = bus_client.get(f"/threads/{parent}").json()
    assert unbound["parent_thread"] is None
    assert unbound["lane_role"] is None


def test_send_new_slug_auto_bind(bus_client: TestClient) -> None:
    parent = _create_lane(bus_client, "auto-parent")
    resp = bus_client.post(
        "/threads/send",
        json={
            "new_slug": "auto-child",
            "from": "cursor",
            "to": "web",
            "subject": "auto bind",
            "body": "brief",
            "parent_thread": parent,
            "lane_role": "sub_mission",
        },
    )
    assert resp.status_code == 201, resp.text
    child_id = resp.json()["thread"]["id"]
    current = bus_client.get(f"/threads/{child_id}/lane-current").json()
    assert current["state"] == "associated"
    assert current["parent_thread"] == parent
    assert current["lane_role"] == "sub_mission"


def test_lane_bound_event_emitted_on_bind_and_rebind(bus_client: TestClient) -> None:
    child = _create_lane(bus_client, "emit-child")
    parent_a = _create_lane(bus_client, "emit-parent-a")
    parent_b = _create_lane(bus_client, "emit-parent-b")
    emitted: list[tuple[str | None, int | None]] = []

    def _capture(**kwargs):
        emitted.append((kwargs.get("prior_association_id"), kwargs.get("association_id")))

    with patch("agent_bus_store.db.lane_associations.emit_lane_bound", side_effect=_capture):
        bus_client.post(
            f"/threads/{child}/lane-bind",
            json={"parent_thread_id": parent_a, "lane_role": "hop"},
        )
        bus_client.post(
            f"/threads/{child}/lane-bind",
            json={"parent_thread_id": parent_b, "lane_role": "side"},
        )
    assert emitted[0][0] is None
    assert emitted[0][1] is not None
    assert emitted[1][0] == emitted[0][1]


def test_no_update_token_in_lane_associations_module() -> None:
    from pathlib import Path

    text = Path("libs/agent_bus_store/db/lane_associations.py").read_text()
    assert "UPDATE" not in text
