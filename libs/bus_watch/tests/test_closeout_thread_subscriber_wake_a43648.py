"""Regression a:43648 — worker-label closeouts wake the thread-scoped subscriber."""

from __future__ import annotations

from typing import Any

import pytest
from agent_bus_store.db import create_thread, init_db, insert_turn
from agent_bus_store.db.connection import connect, write_connect
from agent_bus_store.db.endpoint_subscriptions import subscribe_endpoint
from agent_bus_store.db.endpoint_wake_ledger import (
    CONSUMER,
    advance_scan,
    get_cursor,
    newest_subscribed_thread_scoped_subscription,
)
from bus_watch.endpoint_wake_dispatcher import qualify, scan_once


def _rewind_wake_cursor(conn, *, to: int = 0) -> None:
    conn.execute(
        "UPDATE endpoint_wake_cursor SET high_water_turn_id=? WHERE consumer=?",
        (to, CONSUMER),
    )


_THREAD = "22796"
_HOUSELESS = "monterey-application-houseless"

# 22796 turn id 200926 / #68
_SPECIMEN_TURN_68 = {
    "from_agent": "cursor-sdk",
    "to_agent": "dispatch",
    "subject": (
        "CLOSEOUT|cursor-sdk CLOSEOUT 2db7ac2a-f032-4056-8ee6-ded8547508a0 "
        "exec=38e3a03a-8152-4716-9b0c-ae4079384ef7 contract=freeform"
    ),
}
# 22796 turn #78 id 201072
_SPECIMEN_TURN_78 = {
    "from_agent": "cursor-sdk",
    "to_agent": "dispatch",
    "subject": (
        "CLOSEOUT|cursor-sdk CLOSEOUT ff15a20f-eb7f-4a14-b448-8b3474c6a99b "
        "exec=16cabe16-54e7-428b-b3ea-e1130bdef63b contract=freeform"
    ),
}
# 22796 turn #74 id 201000 — self-addressed; must not wake
_SPECIMEN_TURN_74 = {
    "from_agent": "cursor-sdk",
    "to_agent": "cursor-sdk",
    "subject": (
        "CLOSEOUT|cursor-sdk CLOSEOUT a1909dcc-3907-4cbd-84b7-de374649a5fd-r1 "
        "exec=a2bb470c-ac24-4f35-b0d0-0550bfb19108 contract=freeform "
        "admitted_via=giw_park_resume caller=cursor-sdk"
    ),
}
# 22796 turn #72 PARKED
_SPECIMEN_TURN_72 = {
    "from_agent": "cursor-sdk",
    "to_agent": "dispatch",
    "subject": (
        "cursor-sdk dispatch a1909dcc-3907-4cbd-84b7-de374649a5fd PARKED "
        "(for GIW restart 7294ea41-658b-4280-8b04-e001e3a76cea)"
    ),
}
# 22796 turn #77 cdp reply
_SPECIMEN_TURN_77 = {
    "from_agent": "web-anthropic",
    "to_agent": "dispatch",
    "subject": "cdp reply — 474a36a9",
}
_SPECIMEN_TURNS = (_SPECIMEN_TURN_68, _SPECIMEN_TURN_78)


@pytest.fixture()
def db(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    monkeypatch.delenv("AGENT_BUS_ENDPOINT_WAKE_DISPATCHER", raising=False)
    init_db()


def _ensure_thread(thread: str) -> None:
    create_thread(thread_id=thread, slug=f"a43648-{thread}")


def _seed_houseless_subscription(thread: str) -> None:
    _ensure_thread(thread)
    subscribe_endpoint(
        agent=_HOUSELESS,
        thread_scope=thread,
        surface="cse",
        display="8",
        title="houseless",
        agent_id="reg-22796",
        bound_by="test-a43648",
    )


class _ScanStore:
    def __init__(self) -> None:
        self.pending: list[dict[str, Any]] = []

    def cursor(self) -> dict[str, Any]:
        return get_cursor()

    def since(self, after_id: int, limit: int = 50) -> list[dict[str, Any]]:
        with connect() as c:
            rows = c.execute(
                "SELECT id, thread, turn_number, from_agent, to_agent, subject, "
                "body, read_at FROM turns WHERE id>? ORDER BY id LIMIT ?",
                (after_id, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def scan(self, expected: int, new_hw: int, rows: list[dict]) -> dict:
        self.pending = rows
        return advance_scan(
            "endpoint-wake-dispatcher",
            expected,
            new_hw,
            rows,
            now="2020-01-01 20:00:00",
        )

    def subscription(self, agent: str, thread_id: str, raw_to: str | None = None):
        from agent_bus_store.db.endpoint_subscriptions import current_subscription

        row = current_subscription(agent, thread_id)
        if row and row.get("state") == "subscribed":
            return ("exact", row)
        return None

    def holder(self, lane: str) -> dict | None:
        lane = str(lane or "").strip()
        if lane:
            return {
                "registration_id": lane,
                "chat_url": f"https://claude.ai/cowork/{lane}",
                "purpose": "houseless",
                "seat_state": "houseless",
            }
        return None

    def holder_by_registration(self, registration_id: str) -> dict | None:
        reg = str(registration_id or "").strip()
        if not reg:
            return None
        return {
            "registration_id": reg,
            "chat_url": f"https://claude.ai/cowork/{reg}",
            "purpose": "houseless",
            "seat_state": "houseless",
        }

    def lane_parent(self, lane: str) -> str | None:
        return None


def _ledger_pending_rows() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM endpoint_wake_ledger WHERE state='pending'"
        ).fetchall()
    return [dict(r) for r in rows]


def _run_scan_for_turn(
    *,
    from_agent: str,
    to_agent: str,
    subject: str,
    thread: str,
) -> tuple[list[dict[str, Any]], int]:
    _ensure_thread(thread)
    turn_id, _ts, t_num, _c = insert_turn(
        thread=thread,
        from_agent=from_agent,
        to_agent=to_agent,
        subject=subject,
        body="closeout",
    )
    get_cursor()
    with connect() as conn:
        _rewind_wake_cursor(conn, to=0)
    store = _ScanStore()
    scan_once(store)  # type: ignore[arg-type]
    return store.pending, turn_id


@pytest.mark.parametrize("case", _SPECIMEN_TURNS[:2])
def test_worker_closeout_specimens_enqueue_pending_wake(db: None, case: dict) -> None:
    _seed_houseless_subscription(_THREAD)
    pending, turn_id = _run_scan_for_turn(thread=_THREAD, **case)
    wakes = _ledger_pending_rows()
    assert len(wakes) == 1
    row = wakes[0]
    assert row["agent"] == _HOUSELESS
    assert row["thread_id"] == _THREAD
    assert row["turn_id"] == turn_id


def test_scan_once_idempotent_one_ledger_row_per_turn(db: None) -> None:
    """Round-1 reroute: second scan_once pass must not duplicate ledger rows."""
    _seed_houseless_subscription(_THREAD)
    _ensure_thread(_THREAD)
    turn_id, *_ = insert_turn(
        thread=_THREAD,
        **_SPECIMEN_TURNS[0],
        body="closeout",
    )
    get_cursor()
    with connect() as conn:
        _rewind_wake_cursor(conn, to=0)
    store = _ScanStore()
    scan_once(store)  # type: ignore[arg-type]
    scan_once(store)  # type: ignore[arg-type]
    with connect() as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM endpoint_wake_ledger WHERE turn_id=?",
            (turn_id,),
        ).fetchone()[0]
    assert int(n) == 1


def test_self_addressed_closeout_specimen_does_not_wake(db: None) -> None:
    _seed_houseless_subscription(_THREAD)
    _run_scan_for_turn(thread=_THREAD, **_SPECIMEN_TURN_74)
    assert _ledger_pending_rows() == []


def test_admit_echo_to_cursor_sdk_does_not_wake(db: None) -> None:
    _seed_houseless_subscription(_THREAD)
    _run_scan_for_turn(
        thread=_THREAD,
        from_agent="cursor-sdk",
        to_agent="cursor-sdk",
        subject="cursor-sdk generate admitted",
    )
    assert _ledger_pending_rows() == []
    verdict = qualify(
        {
            "to_agent": "cursor-sdk",
            "from_agent": "dispatch",
            "subject": "cursor-sdk generate admitted",
            "thread": _THREAD,
        },
        cse=True,
        store=_ScanStore(),  # type: ignore[arg-type]
    )
    assert verdict.wake is False
    assert verdict.rule_id == "drop_dispatch_intermediate"


def test_two_thread_subscribers_wake_newest_only(db: None) -> None:
    _ensure_thread(_THREAD)
    subscribe_endpoint(
        agent="older-seat",
        thread_scope=_THREAD,
        surface="cse",
        display="7",
        title="older",
        agent_id="old-reg",
        bound_by="test-a43648",
    )
    _seed_houseless_subscription(_THREAD)
    picked = newest_subscribed_thread_scoped_subscription(_THREAD)
    assert picked is not None
    assert picked[0] == _HOUSELESS
    pending, _ = _run_scan_for_turn(
        thread=_THREAD,
        **_SPECIMEN_TURNS[0],
    )
    wakes = _ledger_pending_rows()
    assert len(wakes) == 1
    assert wakes[0]["agent"] == _HOUSELESS


def test_no_thread_subscriber_behaves_as_today(db: None) -> None:
    _run_scan_for_turn(thread=_THREAD, **_SPECIMEN_TURNS[0])
    assert _ledger_pending_rows() == []


def test_wildcard_only_subscription_does_not_wake(db: None) -> None:
    _ensure_thread(_THREAD)
    with write_connect() as conn:
        conn.execute(
            """
            INSERT INTO agent_endpoint_subscriptions (
                agent, thread_scope, state, surface, display, title, agent_id,
                bound_by, created_at
            ) VALUES (?, '*', 'subscribed', 'cse', '9', 'wildcard', ?, ?, ?)
            """,
            (
                "wildcard-only-seat",
                "wild-reg",
                "test-a43648",
                "2020-01-01 00:00:00",
            ),
        )
    _run_scan_for_turn(thread=_THREAD, **_SPECIMEN_TURNS[0])
    assert _ledger_pending_rows() == []


def test_subscription_lookup_failure_does_not_crash_scan(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(_thread: str):
        raise RuntimeError("lookup failed")

    monkeypatch.setattr(
        "agent_bus_store.db.endpoint_wake_ledger.newest_subscribed_thread_scoped_subscription",
        _boom,
    )
    _run_scan_for_turn(thread=_THREAD, **_SPECIMEN_TURNS[0])
    assert _ledger_pending_rows() == []


def test_parked_to_dispatch_wakes_thread_subscriber(db: None) -> None:
    _seed_houseless_subscription(_THREAD)
    _run_scan_for_turn(
        thread=_THREAD,
        from_agent="cursor-sdk",
        to_agent="dispatch",
        subject="PARKED | lane 22796",
    )
    assert len(_ledger_pending_rows()) == 1


def test_wildcard_cursor_preempted_by_thread_subscriber(db: None) -> None:
    _ensure_thread(_THREAD)
    with write_connect() as conn:
        conn.execute(
            """
            INSERT INTO agent_endpoint_subscriptions (
                agent, thread_scope, state, surface, display, title, agent_id,
                bound_by, created_at
            ) VALUES ('grok-bot-a43648', '*', 'subscribed', 'grok', '8', 'wc', 'tab-wc',
                      'test', '2020-01-01 00:00:00')
            """
        )
    _seed_houseless_subscription(_THREAD)
    _run_scan_for_turn(thread=_THREAD, **_SPECIMEN_TURNS[0])
    wakes = _ledger_pending_rows()
    assert len(wakes) == 1
    assert wakes[0]["agent"] == _HOUSELESS


def test_newest_thread_subscriber_lookup_hermetic_without_db_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bus_watch.endpoint_wake_dispatcher import _newest_thread_subscriber_for_reroute

    monkeypatch.delenv("AGENT_BUS_DB_PATH", raising=False)
    assert _newest_thread_subscriber_for_reroute("22796") is None


def test_cursor_surface_thread_subscriber_pending(db: None) -> None:
    _ensure_thread(_THREAD)
    subscribe_endpoint(
        agent="lane-house",
        thread_scope=_THREAD,
        surface="cursor",
        display="8",
        title="cursor-tab",
        agent_id="composer-8",
        bound_by="test-a43648",
    )
    _run_scan_for_turn(
        thread=_THREAD,
        from_agent="cursor-sdk",
        to_agent="dispatch",
        subject="cursor-sdk CLOSEOUT tab-wake contract=freeform",
    )
    assert len(_ledger_pending_rows()) == 1
    assert _ledger_pending_rows()[0]["agent"] == "lane-house"


@pytest.mark.parametrize("case", (_SPECIMEN_TURN_72, _SPECIMEN_TURN_77))
def test_22796_parked_and_cdp_reply_worker_label_wake(db: None, case: dict) -> None:
    _seed_houseless_subscription(_THREAD)
    _run_scan_for_turn(thread=_THREAD, **case)
    assert len(_ledger_pending_rows()) == 1
    assert _ledger_pending_rows()[0]["agent"] == _HOUSELESS
