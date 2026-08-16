"""HTTP tests for GET /threads/{thread_id}/lineage (Stage 2 provenance G2/G6)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agent_bus_store import create_app
from agent_bus_store.auth import require_token
from agent_bus_store.db import admit_dispatch, create_thread, init_db


@pytest.fixture()
def bus_db(tmp_path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "bus.db"
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(db_path))
    init_db()
    return db_path


def _app(bus_db):
    app = create_app(db_path=str(bus_db))
    app.dependency_overrides[require_token] = lambda: None
    return app


def test_thread_lineage_includes_dispatch_links_and_children(bus_db) -> None:
    with TestClient(_app(bus_db)) as client:
        parent = create_thread(thread_id=None, slug="lineage-parent")
        parent_id = parent["id"]
        child = create_thread(thread_id=None, slug="lineage-child")
        child_id = child["id"]

        bind = client.post(
            f"/threads/{child_id}/lane-bind",
            json={"parent_thread_id": parent_id, "lane_role": "sub_mission"},
        )
        assert bind.status_code == 200, bind.text

        admit_dispatch(
            thread_id=parent_id,
            execution_id="exec-lineage-1",
            pipeline_id="cursor-sdk-generate",
        )

        resp = client.get(f"/threads/{parent_id}/lineage")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["thread_id"] == parent_id

        assert len(body["children"]) == 1
        row = body["children"][0]
        assert row["thread_id"] == child_id
        assert row["lane_role"] == "sub_mission"
        assert row["parent_thread_id"] == parent_id
        assert row["turn_count"] == 0
        assert row["status"] == "active"

        assert len(body["dispatch_links"]) == 1
        link = body["dispatch_links"][0]
        assert link["execution_id"] == "exec-lineage-1"
        assert link["pipeline_id"] == "cursor-sdk-generate"
        assert link["terminal_status"] is None


def test_thread_lineage_empty_when_no_children_or_links(bus_db) -> None:
    with TestClient(_app(bus_db)) as client:
        bare = create_thread(thread_id=None, slug="lineage-bare")
        resp = client.get(f"/threads/{bare['id']}/lineage")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["children"] == []
        assert body["dispatch_links"] == []


def test_thread_lineage_404_unknown_thread(bus_db) -> None:
    with TestClient(_app(bus_db)) as client:
        resp = client.get("/threads/999999/lineage")
        assert resp.status_code == 404


def test_plain_thread_detail_has_no_dispatch_links_field(bus_db) -> None:
    """G3: the plain `GET /threads/{id}` (`get_thread()`) response no longer
    carries `dispatch_links` at all — it was silently always-`[]` on this path
    (only the three dispatch-lifecycle routes via `get_thread_with_links()`
    populated it); dropped rather than left inconsistent. Lineage for this
    thread lives on `GET /threads/{id}/lineage` (G2), asserted separately.
    """
    with TestClient(_app(bus_db)) as client:
        created = create_thread(thread_id=None, slug="g3-plain-detail")
        thread_id = created["id"]
        admit_dispatch(
            thread_id=thread_id,
            execution_id="exec-g3-plain-1",
            pipeline_id="cursor-sdk-generate",
        )

        resp = client.get(f"/threads/{thread_id}")
        assert resp.status_code == 200, resp.text
        assert "dispatch_links" not in resp.json()
