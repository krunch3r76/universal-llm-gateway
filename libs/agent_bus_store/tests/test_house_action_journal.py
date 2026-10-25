"""House action journal store, routes, and migration."""

from __future__ import annotations

import importlib
import inspect
from unittest.mock import patch

import pytest
from agent_bus_store.auth import require_token
from agent_bus_store.db import create_thread, init_db, insert_turn, update_thread
from agent_bus_store.db.connection import connect
from agent_bus_store.db.house_action_journal import (
    _MINT_ROOT_LITERAL,
    append_house_action,
    list_joined_houses,
    parse_house_birth_mission,
)
from agent_bus_store.db.migrations import migration_021
from agent_bus_store.db.threads import set_thread_tags
from agent_bus_store.server import create_app
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    monkeypatch.delenv("AGENT_BUS_ENDPOINT_WAKE_DISPATCHER", raising=False)
    init_db()
    app = create_app()
    app.dependency_overrides[require_token] = lambda: None
    return TestClient(app)


def _liaison_root(
    client: TestClient,
    *,
    slug: str,
    turn1_body: str,
    extra_tags: list[str] | None = None,
    status: str = "active",
) -> str:
    tags = ["role:root", "lane:liaison", *(extra_tags or [])]
    detail = create_thread(thread_id=None, slug=slug, tags=tags)
    assert detail is not None
    root = str(detail["id"])
    insert_turn(
        thread=root,
        from_agent="cursor",
        to_agent="cursor",
        subject=f"HOUSE {slug}",
        body=turn1_body,
        idempotent_first_turn=True,
    )
    if status != "active":
        update_thread(thread_id=root, status=status)
    return root


def test_migration_021_idempotent(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    init_db()
    with connect() as conn:
        migration_021.run(conn)
        migration_021.run(conn)
        n = conn.execute(
            "SELECT count(*) FROM sqlite_master WHERE name='house_action_journal'"
        ).fetchone()[0]
    assert n == 1


def test_journal_module_has_no_update_or_delete() -> None:
    mod = importlib.import_module("agent_bus_store.db.house_action_journal")
    for name, obj in inspect.getmembers(mod):
        if not inspect.isfunction(obj):
            continue
        lowered = name.lower()
        assert "update" not in lowered
        assert "delete" not in lowered


def test_house_birth_parse_matches_mint_root_thread_literal() -> None:
    body = _MINT_ROOT_LITERAL.format(slug="grok-sdk-fast-off", work_key="ruling:x")
    assert parse_house_birth_mission(body) == "ruling:x"


def test_get_houses_returns_joined_rows_shape_root_id_slug_mission_action_why(
    client: TestClient,
) -> None:
    root = _liaison_root(
        client,
        slug="house-a",
        turn1_body="HOUSE-BIRTH house-a work_key=todo:alpha — role:root continuity house on Glass.",
    )
    client.post(
        "/house-actions",
        json={
            "root_thread_id": root,
            "kind": "now_row_set",
            "actor": "operator",
            "action_why": "todo:alpha",
        },
    )
    resp = client.get("/house-actions")
    assert resp.status_code == 200
    row = resp.json()["houses"][0]
    assert set(row.keys()) >= {"root_id", "slug", "mission", "action_why"}
    assert row["root_id"] == root
    assert row["slug"] == "house-a"
    assert row["mission"] == "todo:alpha"
    assert row["action_why"] == "todo:alpha"


def test_get_houses_lists_house_without_journal_row_with_empty_action_why(
    client: TestClient,
) -> None:
    _liaison_root(
        client,
        slug="no-journal",
        turn1_body="HOUSE-BIRTH no-journal work_key=todo:n — role:root continuity house on Glass.",
    )
    rows = client.get("/house-actions").json()["houses"]
    assert len(rows) == 1
    assert rows[0]["action_why"] == ""


def test_get_houses_action_why_is_newest_row_per_root(client: TestClient) -> None:
    root = _liaison_root(
        client,
        slug="fold",
        turn1_body="HOUSE-BIRTH fold work_key=todo:f — role:root continuity house on Glass.",
    )
    client.post(
        "/house-actions",
        json={
            "root_thread_id": root,
            "kind": "now_row_set",
            "actor": "operator",
            "action_why": "first",
        },
    )
    client.post(
        "/house-actions",
        json={
            "root_thread_id": root,
            "kind": "gate_swap",
            "actor": "ticker",
            "action_why": "newest",
        },
    )
    row = client.get("/house-actions").json()["houses"][0]
    assert row["action_why"] == "newest"


def test_get_houses_excludes_closed_root(client: TestClient) -> None:
    _liaison_root(
        client,
        slug="closed",
        turn1_body="HOUSE-BIRTH closed work_key=todo:c — role:root continuity house on Glass.",
        status="closed",
    )
    assert client.get("/house-actions").json()["houses"] == []


def test_get_houses_excludes_role_root_without_lane_liaison(
    client: TestClient,
) -> None:
    detail = create_thread(thread_id=None, slug="root-only")
    root = str(detail["id"])
    with connect() as conn:
        set_thread_tags(conn, root, ["role:root"])
    insert_turn(
        thread=root,
        from_agent="cursor",
        to_agent="cursor",
        subject="x",
        body="HOUSE-BIRTH x work_key=todo:x — role:root continuity house on Glass.",
        idempotent_first_turn=True,
    )
    assert client.get("/house-actions").json()["houses"] == []


def test_get_houses_excludes_lane_liaison_without_role_root(
    client: TestClient,
) -> None:
    detail = create_thread(thread_id=None, slug="lane-only")
    root = str(detail["id"])
    with connect() as conn:
        set_thread_tags(conn, root, ["lane:liaison"])
    insert_turn(
        thread=root,
        from_agent="cursor",
        to_agent="cursor",
        subject="x",
        body="HOUSE-BIRTH x work_key=todo:x — role:root continuity house on Glass.",
        idempotent_first_turn=True,
    )
    assert client.get("/house-actions").json()["houses"] == []


def test_get_houses_mission_is_house_birth_work_key_not_subject(
    client: TestClient,
) -> None:
    root = _liaison_root(
        client,
        slug="house-y",
        turn1_body="HOUSE-BIRTH inner work_key=ruling:x — role:root continuity house on Glass.",
    )
    with connect() as conn:
        conn.execute(
            "UPDATE turns SET subject=? WHERE thread=? AND turn_number=1",
            ("HOUSE y", root),
        )
        conn.commit()
    row = client.get("/house-actions").json()["houses"][0]
    assert row["mission"] == "ruling:x"


def test_get_houses_mission_empty_when_turn_one_not_house_birth(
    client: TestClient,
) -> None:
    _liaison_root(client, slug="legacy", turn1_body="Continuity bootstrap")
    row = client.get("/house-actions").json()["houses"][0]
    assert row["mission"] == ""


def test_post_append_emits_one_advisory_event(client: TestClient) -> None:
    root = _liaison_root(
        client,
        slug="emit",
        turn1_body="HOUSE-BIRTH emit work_key=todo:e — role:root continuity house on Glass.",
    )
    with patch(
        "agent_bus_store.routes.threads.house_actions.emit_house_action_appended"
    ) as emit:
        resp = client.post(
            "/house-actions",
            json={
                "root_thread_id": root,
                "kind": "harvest",
                "actor": "seat",
                "action_why": "harvested",
            },
        )
    assert resp.status_code == 200
    emit.assert_called_once()


def test_post_append_rejected_emits_no_event(client: TestClient) -> None:
    with patch(
        "agent_bus_store.routes.threads.house_actions.emit_house_action_appended"
    ) as emit:
        resp = client.post(
            "/house-actions",
            json={
                "root_thread_id": "99999",
                "kind": "harvest",
                "actor": "seat",
                "action_why": "x",
            },
        )
    assert resp.status_code == 422
    emit.assert_not_called()


def test_append_house_action_direct(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    init_db()
    detail = create_thread(
        thread_id=None,
        slug="direct",
        tags=["role:root", "lane:liaison"],
    )
    root = str(detail["id"])
    row = append_house_action(
        root_thread_id=root,
        kind="now_row_set",
        actor="operator",
        action_why="todo:direct",
    )
    assert row["root_thread_id"] == root
    assert list_joined_houses()[0]["action_why"] == "todo:direct"
