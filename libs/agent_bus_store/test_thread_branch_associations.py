"""Verification tests for append-only lane↔branch associations (arc 6655)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_bus_store.auth import require_token
from agent_bus_store.db import init_db
from agent_bus_store.db.connection import connect
from agent_bus_store.server import create_app

_SPEC_PATH = Path(
    "/mnt/torus/mcp-data/files/notes/system/specs/lane-tree-association.md"
)


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


def test_associate_appends_and_current_is_max_id(bus_client: TestClient) -> None:
    """S0→S1→S3: append-only history; current follows MAX(id)."""
    thread_id = _create_lane(bus_client, "lane-a")

    s0 = bus_client.get(f"/threads/{thread_id}/branch-current")
    assert s0.status_code == 200
    assert s0.json() == {
        "thread_id": thread_id,
        "current_branch": None,
        "association_id": None,
        "state": "none",
    }

    first = bus_client.post(
        f"/threads/{thread_id}/branch-associate",
        json={"branch_name": "feature/alpha"},
    )
    assert first.status_code == 200
    body1 = first.json()
    assert body1["branch_name"] == "feature/alpha"
    assert body1["current_branch"] == "feature/alpha"
    assert "associated_at" not in body1
    id1 = body1["id"]

    second = bus_client.post(
        f"/threads/{thread_id}/branch-associate",
        json={"branch_name": "feature/beta"},
    )
    assert second.status_code == 200
    body2 = second.json()
    assert body2["id"] > id1
    assert body2["current_branch"] == "feature/beta"

    current = bus_client.get(f"/threads/{thread_id}/branch-current")
    assert current.status_code == 200
    assert current.json()["state"] == "associated"
    assert current.json()["current_branch"] == "feature/beta"
    assert current.json()["association_id"] == body2["id"]


def test_two_lanes_share_branch(bus_client: TestClient) -> None:
    """S4: two lanes may associate the same branch_name without conflict."""
    lane1 = _create_lane(bus_client, "lane-share-1")
    lane2 = _create_lane(bus_client, "lane-share-2")
    shared = "shared/worktree"

    r1 = bus_client.post(
        f"/threads/{lane1}/branch-associate",
        json={"branch_name": shared},
    )
    r2 = bus_client.post(
        f"/threads/{lane2}/branch-associate",
        json={"branch_name": shared},
    )
    assert r1.status_code == 200
    assert r2.status_code == 200

    c1 = bus_client.get(f"/threads/{lane1}/branch-current").json()
    c2 = bus_client.get(f"/threads/{lane2}/branch-current").json()
    assert c1["current_branch"] == shared
    assert c2["current_branch"] == shared
    assert c1["association_id"] != c2["association_id"]


def test_associate_rejects_client_seq_or_id(bus_client: TestClient) -> None:
    """F3: client cannot supply store-owned ordering tokens."""
    thread_id = _create_lane(bus_client, "lane-reject")

    for payload in (
        {"branch_name": "b", "id": 99},
        {"branch_name": "b", "seq": 1},
    ):
        resp = bus_client.post(
            f"/threads/{thread_id}/branch-associate",
            json=payload,
        )
        assert resp.status_code == 422


def test_no_association_distinct_from_history(bus_client: TestClient) -> None:
    """S0 payload is none/null — not an empty-string branch masquerading as history."""
    thread_id = _create_lane(bus_client, "lane-s0")
    resp = bus_client.get(f"/threads/{thread_id}/branch-current")
    assert resp.status_code == 200
    body = resp.json()
    assert body["state"] == "none"
    assert body["current_branch"] is None
    assert body["current_branch"] != ""


def test_no_auto_mint_on_probe() -> None:
    """v1 has no auto-mint resolver module wired into agent_bus_store."""
    import importlib

    import agent_bus_store  # noqa: F401

    pkg_root = Path(agent_bus_store.__file__).resolve().parent
    resolver_names = (
        "auto_mint",
        "dangling_branch",
        "branch_probe_resolver",
    )
    for name in resolver_names:
        assert not (pkg_root / f"{name}.py").exists()
        assert importlib.util.find_spec(f"agent_bus_store.{name}") is None


def test_schema_exactly_three_columns(bus_client: TestClient) -> None:
    """AC-7: PRAGMA table_info observes exactly three columns on migrated DB."""
    thread_id = _create_lane(bus_client, "schema-probe")
    _ = thread_id

    with connect() as conn:
        rows = conn.execute(
            "PRAGMA table_info(thread_branch_associations)"
        ).fetchall()

    column_names = [row["name"] for row in rows]
    assert column_names == ["id", "thread_id", "branch_name"]
    assert "associated_at" not in column_names


def test_flag3_expiry_documented() -> None:
    """Spec carries Flag 3 expiry obligation and conflict-rule text."""
    assert _SPEC_PATH.exists(), f"missing spec at {_SPEC_PATH}"
    text = _SPEC_PATH.read_text(encoding="utf-8")
    assert "a:28524" in text
    assert "Flag 3 expiry" in text or "Flag 3" in text
    assert "conflict rule" in text.lower() or "conflict-rule" in text.lower()
