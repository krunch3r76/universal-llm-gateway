"""Bus-thread disposition — ephemeral auto-close defaults."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agent_bus_store import create_app
from agent_bus_store.auth import require_token
from agent_bus_store.db import (
    admit_dispatch,
    create_thread_with_turn,
    get_thread,
    init_db,
)
from agent_bus_store.db.connection import connect
from agent_bus_store.db.threads import set_thread_tags
from agent_bus_store.disposition import (
    append_bus_lifecycle_tags,
    land_owed_for_summary,
    maybe_auto_close_after_dispatch_terminate,
    resolve_bus_lifecycle,
    summary_for_auto_close,
)
from agent_bus_store.turns_models import ThreadStatus


@pytest.fixture()
def bus_db(tmp_path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "bus.db"
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(db_path))
    init_db()
    app = create_app()
    app.dependency_overrides[require_token] = lambda: None
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_summary_for_auto_close_preserves_so_what() -> None:
    assert (
        summary_for_auto_close("ULG: reliable closeout SMS")
        == "DONE — ULG: reliable closeout SMS"
    )
    assert summary_for_auto_close("DONE — ULG: already composed") == (
        "DONE — ULG: already composed"
    )


def test_summary_for_auto_close_land_owed_from_tag() -> None:
    so_what = "ULG: branch still on lane-B"
    assert (
        summary_for_auto_close(so_what, tags=["land_required"])
        == f"LAND OWED — {so_what}"
    )
    assert (
        summary_for_auto_close(f"DONE — {so_what}", tags=["land_required"])
        == f"LAND OWED — {so_what}"
    )


def test_summary_for_auto_close_land_owed_from_closeout_meter() -> None:
    so_what = "ULG: unlanded lane-B capture"
    assert (
        summary_for_auto_close(so_what, landed=False, commits_ahead=2)
        == f"LAND OWED — {so_what}"
    )


def test_summary_for_auto_close_stays_done_without_debt() -> None:
    so_what = "ULG: landed cleanly"
    assert (
        summary_for_auto_close(so_what, landed=True, commits_ahead=1)
        == f"DONE — {so_what}"
    )
    assert (
        summary_for_auto_close(so_what, landed=False, commits_ahead=0)
        == f"DONE — {so_what}"
    )


def test_land_owed_for_summary() -> None:
    assert land_owed_for_summary(tags=["land_required"]) is True
    assert land_owed_for_summary(landed=False, commits_ahead=1) is True
    assert land_owed_for_summary(landed=True, commits_ahead=1) is False
    assert land_owed_for_summary(landed=False, commits_ahead=0) is False


def test_add_land_required_tag_on_closed_thread(bus_db) -> None:
    """F2: additive tags must succeed after the thread is closed."""
    thread_row, *_ = create_thread_with_turn(
        slug="closed-tag",
        from_agent="dispatch",
        to_agent="cursor-sdk",
        subject="implement",
        body="pointer",
    )
    thread_id = thread_row["id"]
    bus_db.patch(f"/threads/{thread_id}", json={"status": "closed"})
    resp = bus_db.patch(
        f"/threads/{thread_id}",
        json={"add_tags": ["land_required"]},
    )
    assert resp.status_code == 200
    assert "land_required" in (resp.json().get("tags") or [])


def test_dispatch_terminate_land_owed_summary(bus_db) -> None:
    so_what = "ULG: lane-B residue visible in pager"
    thread_row, *_ = create_thread_with_turn(
        slug="sdk-land-owed",
        from_agent="dispatch",
        to_agent="cursor-sdk",
        subject="implement",
        body="packet pointer",
        summary=so_what,
        lifecycle_state="pending",
    )
    thread_id = thread_row["id"]
    with connect() as conn:
        set_thread_tags(
            conn,
            thread_id,
            append_bus_lifecycle_tags(["land_required"]),
        )
    admit_dispatch(
        thread_id=thread_id,
        execution_id="exec-land-owed",
        pipeline_id="cursor-sdk-generate",
    )
    bus_db.post(
        "/turns",
        json={
            "thread": thread_id,
            "from": "cursor-sdk",
            "to": "dispatch",
            "subject": "cursor-sdk dispatch closeout",
            "body": '{"landed": false, "commits_ahead": 1, "status": "partial"}',
            "after_turn": 1,
        },
    )
    resp = bus_db.post(
        f"/threads/{thread_id}/dispatch-terminate",
        json={"terminal_status": "completed", "execution_id": "exec-land-owed"},
    )
    assert resp.status_code == 200
    detail = resp.json()
    assert detail["status"] == ThreadStatus.CLOSED
    assert detail["summary"] == f"LAND OWED — {so_what}"


def test_summary_for_auto_close_rejects_machine_junk() -> None:
    assert summary_for_auto_close(None) is None
    assert summary_for_auto_close("") is None
    assert (
        summary_for_auto_close("Dispatch completed — auto-closed (ephemeral default).")
        is None
    )
    assert (
        summary_for_auto_close(
            "DONE — Dispatch completed — auto-closed (ephemeral default)."
        )
        is None
    )
    assert (
        summary_for_auto_close(
            "Generate result read (turn 2) — auto-closed (close-on-read)."
        )
        is None
    )


def test_resolve_bus_lifecycle_defaults_ephemeral() -> None:
    assert resolve_bus_lifecycle(None) == "ephemeral"
    assert resolve_bus_lifecycle(["contract:implement"]) == "ephemeral"


def test_resolve_bus_lifecycle_persistent_tag() -> None:
    assert resolve_bus_lifecycle(["bus_lifecycle:persistent"]) == "persistent"


def test_append_bus_lifecycle_tags_replaces_conflicting() -> None:
    tags = append_bus_lifecycle_tags(
        ["bus_lifecycle:persistent", "contract:implement"],
        bus_lifecycle="ephemeral",
    )
    assert "bus_lifecycle:ephemeral" in tags
    assert "bus_lifecycle:persistent" not in tags


def test_dispatch_terminate_auto_closes_completed(bus_db) -> None:
    thread_row, *_ = create_thread_with_turn(
        slug="sdk-done",
        from_agent="dispatch",
        to_agent="cursor-sdk",
        subject="implement",
        body="packet pointer",
        lifecycle_state="pending",
    )
    thread_id = thread_row["id"]
    with connect() as conn:
        set_thread_tags(conn, thread_id, append_bus_lifecycle_tags([]))
    admit_dispatch(
        thread_id=thread_id,
        execution_id="exec-close",
        pipeline_id="cursor-sdk-generate",
    )
    bus_db.post(
        "/turns",
        json={
            "thread": thread_id,
            "from": "cursor-sdk",
            "to": "dispatch",
            "subject": "cursor-sdk dispatch closeout",
            "body": "done",
            "after_turn": 1,
        },
    )

    resp = bus_db.post(
        f"/threads/{thread_id}/dispatch-terminate",
        json={"terminal_status": "completed", "execution_id": "exec-close"},
    )
    assert resp.status_code == 200
    detail = resp.json()
    assert detail["status"] == ThreadStatus.CLOSED
    # Empty prior ⇒ leave summary unset (¬ machine one-liner).
    assert not (detail.get("summary") or "").strip()


def test_dispatch_terminate_preserves_so_what_summary(bus_db) -> None:
    so_what = "ULG: pager titles carry outcome, not telemetry"
    thread_row, *_ = create_thread_with_turn(
        slug="sdk-so-what",
        from_agent="dispatch",
        to_agent="cursor-sdk",
        subject="implement",
        body="packet pointer",
        summary=so_what,
        lifecycle_state="pending",
    )
    thread_id = thread_row["id"]
    with connect() as conn:
        set_thread_tags(conn, thread_id, append_bus_lifecycle_tags([]))
    admit_dispatch(
        thread_id=thread_id,
        execution_id="exec-so-what",
        pipeline_id="cursor-sdk-generate",
    )
    bus_db.post(
        "/turns",
        json={
            "thread": thread_id,
            "from": "cursor-sdk",
            "to": "dispatch",
            "subject": "cursor-sdk dispatch closeout",
            "body": "done",
            "after_turn": 1,
        },
    )

    resp = bus_db.post(
        f"/threads/{thread_id}/dispatch-terminate",
        json={"terminal_status": "completed", "execution_id": "exec-so-what"},
    )
    assert resp.status_code == 200
    detail = resp.json()
    assert detail["status"] == ThreadStatus.CLOSED
    assert detail["summary"] == f"DONE — {so_what}"
    assert "auto-closed" not in (detail["summary"] or "")


def test_dispatch_terminate_keeps_failed_open(bus_db) -> None:
    thread_row, *_ = create_thread_with_turn(
        slug="sdk-fail",
        from_agent="dispatch",
        to_agent="cursor-sdk",
        subject="implement",
        body="packet pointer",
        lifecycle_state="pending",
    )
    thread_id = thread_row["id"]
    with connect() as conn:
        set_thread_tags(conn, thread_id, append_bus_lifecycle_tags([]))
    admit_dispatch(
        thread_id=thread_id,
        execution_id="exec-fail",
        pipeline_id="cursor-sdk-generate",
    )

    resp = bus_db.post(
        f"/threads/{thread_id}/dispatch-terminate",
        json={"terminal_status": "failed", "execution_id": "exec-fail"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == ThreadStatus.ACTIVE


def test_dispatch_terminate_skips_close_without_result_turn(bus_db) -> None:
    thread_row, *_ = create_thread_with_turn(
        slug="sdk-no-closeout",
        from_agent="dispatch",
        to_agent="cursor-sdk",
        subject="implement",
        body="packet pointer",
        lifecycle_state="pending",
    )
    thread_id = thread_row["id"]
    with connect() as conn:
        set_thread_tags(conn, thread_id, append_bus_lifecycle_tags([]))
    admit_dispatch(
        thread_id=thread_id,
        execution_id="exec-no-closeout",
        pipeline_id="cursor-sdk-generate",
    )

    resp = bus_db.post(
        f"/threads/{thread_id}/dispatch-terminate",
        json={"terminal_status": "completed", "execution_id": "exec-no-closeout"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == ThreadStatus.ACTIVE


def test_dispatch_terminate_persistent_opt_out(bus_db) -> None:
    thread_row, *_ = create_thread_with_turn(
        slug="sdk-persist",
        from_agent="dispatch",
        to_agent="cursor-sdk",
        subject="implement",
        body="packet pointer",
        lifecycle_state="pending",
    )
    thread_id = thread_row["id"]
    with connect() as conn:
        set_thread_tags(
            conn, thread_id, append_bus_lifecycle_tags([], bus_lifecycle="persistent")
        )
    admit_dispatch(
        thread_id=thread_id,
        execution_id="exec-persist",
        pipeline_id="cursor-sdk-generate",
    )
    bus_db.post(
        "/turns",
        json={
            "thread": thread_id,
            "from": "cursor-sdk",
            "to": "dispatch",
            "subject": "cursor-sdk dispatch closeout",
            "body": "done",
            "after_turn": 1,
        },
    )

    resp = bus_db.post(
        f"/threads/{thread_id}/dispatch-terminate",
        json={"terminal_status": "completed", "execution_id": "exec-persist"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == ThreadStatus.ACTIVE


def test_implement_handoff_reply_auto_closes(bus_db) -> None:
    thread_row, *_ = create_thread_with_turn(
        slug="handoff-impl",
        from_agent="dispatch",
        to_agent="claude-cursor",
        subject="implement packet",
        body="pointer",
    )
    thread_id = thread_row["id"]
    with connect() as conn:
        set_thread_tags(
            conn,
            thread_id,
            append_bus_lifecycle_tags(["contract:implement", "type:handoff"]),
        )

    bus_db.post(
        "/turns",
        json={
            "thread": thread_id,
            "from": "claude-cursor",
            "to": "dispatch",
            "subject": "implement done",
            "body": "closeout summary",
            "status": "open",
            "after_turn": 1,
        },
    )

    row = get_thread(thread_id)
    assert row is not None
    assert row["status"] == ThreadStatus.CLOSED


def test_consult_handoff_reply_stays_open(bus_db) -> None:
    thread_row, *_ = create_thread_with_turn(
        slug="handoff-consult",
        from_agent="dispatch",
        to_agent="claude-web",
        subject="consult packet",
        body="pointer",
    )
    thread_id = thread_row["id"]
    with connect() as conn:
        set_thread_tags(
            conn,
            thread_id,
            append_bus_lifecycle_tags(["contract:consult", "type:handoff"]),
        )

    bus_db.post(
        "/turns",
        json={
            "thread": thread_id,
            "from": "claude-web",
            "to": "dispatch",
            "subject": "findings",
            "body": "review notes",
            "status": "open",
            "after_turn": 1,
        },
    )

    row = get_thread(thread_id)
    assert row is not None
    assert row["status"] == ThreadStatus.ACTIVE


def test_maybe_auto_close_skips_failed(bus_db) -> None:
    thread_row, *_ = create_thread_with_turn(
        slug="unit",
        from_agent="dispatch",
        to_agent="cursor",
        subject="s",
        body="b",
    )
    thread_id = thread_row["id"]
    assert (
        maybe_auto_close_after_dispatch_terminate(thread_id, terminal_status="failed")
        is None
    )


def test_historical_cursor_note_not_dispatch_result(bus_db) -> None:
    """AC-1: cursor→web notes must not alone trigger auto-close on terminate."""
    thread_row, *_ = create_thread_with_turn(
        slug="proxy-false-pos",
        from_agent="web-anthropic",
        to_agent="cursor",
        subject="DIRECTIVE",
        body="operator proxy pointer",
        lifecycle_state="pending",
    )
    thread_id = thread_row["id"]
    with connect() as conn:
        set_thread_tags(conn, thread_id, append_bus_lifecycle_tags([]))
    bus_db.post(
        "/turns",
        json={
            "thread": thread_id,
            "from": "cursor",
            "to": "web-anthropic",
            "subject": "monitor note",
            "body": "status update",
            "after_turn": 1,
        },
    )
    admit_dispatch(
        thread_id=thread_id,
        execution_id="exec-proxy-fp",
        pipeline_id="cursor-sdk-generate",
    )
    bus_db.post(
        "/turns",
        json={
            "thread": thread_id,
            "from": "cursor-sdk",
            "to": "web-anthropic",
            "subject": "cursor-sdk dispatch closeout",
            "body": "nested sdk done",
            "after_turn": 2,
        },
    )

    resp = bus_db.post(
        f"/threads/{thread_id}/dispatch-terminate",
        json={"terminal_status": "completed", "execution_id": "exec-proxy-fp"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == ThreadStatus.ACTIVE


def test_lane_cursor_auto_exempt_from_auto_close(bus_db) -> None:
    """AC-2: lane:cursor-auto threads never auto-close on nested SDK terminate."""
    thread_row, *_ = create_thread_with_turn(
        slug="cursor-auto-lane",
        from_agent="dispatch",
        to_agent="cursor-sdk",
        subject="implement",
        body="generate-shaped pointer on auto lane",
        lifecycle_state="pending",
    )
    thread_id = thread_row["id"]
    with connect() as conn:
        set_thread_tags(conn, thread_id, ["lane:cursor-auto"])
    admit_dispatch(
        thread_id=thread_id,
        execution_id="exec-auto-lane",
        pipeline_id="cursor-sdk-generate",
    )
    bus_db.post(
        "/turns",
        json={
            "thread": thread_id,
            "from": "cursor-sdk",
            "to": "dispatch",
            "subject": "cursor-sdk dispatch closeout",
            "body": "done",
            "after_turn": 1,
        },
    )

    resp = bus_db.post(
        f"/threads/{thread_id}/dispatch-terminate",
        json={"terminal_status": "completed", "execution_id": "exec-auto-lane"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == ThreadStatus.ACTIVE
