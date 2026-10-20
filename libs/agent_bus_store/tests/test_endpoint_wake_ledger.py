"""Wake cursor, CAS scan, claim TTL, and /turns/since shape."""

from __future__ import annotations

import threading

import pytest
from agent_bus_store.auth import require_token
from agent_bus_store.db import create_thread, init_db, insert_turn
from agent_bus_store.db.connection import connect, write_connect
from agent_bus_store.db.endpoint_wake_ledger import (
    WakeConflictError,
    advance_scan,
    claim_due_wakes,
    fold_expired_claims,
    get_cursor,
    record_wake_result,
)
from agent_bus_store.db.turns import get_turns
from agent_bus_store.server import create_app
from fastapi.testclient import TestClient


@pytest.fixture()
def db(tmp_path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    monkeypatch.delenv("AGENT_BUS_ENDPOINT_WAKE_DISPATCHER", raising=False)
    init_db()
    app = create_app()
    app.dependency_overrides[require_token] = lambda: None
    return TestClient(app)


def _turn(thread: str, n_from: str = "cursor", to: str = "grok-bot-gc1") -> dict:
    turn_id, _ts, number, _claimed = insert_turn(
        thread=thread, from_agent=n_from, to_agent=to, subject="FACT|x", body="b"
    )
    return {"agent": to, "thread_id": thread, "turn_id": turn_id, "turn_number": number}


def test_compact_fetch_includes_attestation(db: TestClient) -> None:
    detail = create_thread(thread_id=None, slug="compact-attest")
    assert detail is not None
    thread = str(detail["id"])
    insert_turn(
        thread=thread,
        from_agent="cursor",
        to_agent="web-anthropic",
        subject="FACT|x",
        body="hidden",
        attested_by="static:cursor@code via=io(127.0.0.1) client=cursor-ide",
        user_agent="Mozilla/5.0",
    )
    rows = get_turns(thread=thread, compact=True)
    assert len(rows) == 1
    assert rows[0]["body"] is None
    assert "via=io(127.0.0.1)" in rows[0]["attested_by"]
    assert rows[0]["user_agent"] == "Mozilla/5.0"


def test_cold_start_cursor_is_max_id(db: TestClient) -> None:
    detail = create_thread(thread_id=None, slug="cold")
    assert detail is not None
    made = _turn(str(detail["id"]))
    cursor = get_cursor()
    assert cursor["high_water_turn_id"] == made["turn_id"]
    assert (
        db.get("/turns/since", params={"after_id": made["turn_id"], "limit": 2}).json()[
            "turns"
        ]
        == []
    )


def test_turns_since_shape_and_stale_scan(db: TestClient) -> None:
    detail = create_thread(thread_id=None, slug="since")
    assert detail is not None
    thread = str(detail["id"])
    first = _turn(thread)
    second = _turn(thread)
    with connect() as conn:
        conn.execute("DELETE FROM endpoint_wake_cursor")
    get_cursor(now="2020-01-01 00:00:00")
    with connect() as conn:
        conn.execute("UPDATE endpoint_wake_cursor SET high_water_turn_id=0")
    resp = db.get("/turns/since", params={"after_id": first["turn_id"] - 1, "limit": 2})
    rows = resp.json()["turns"]
    assert [r["id"] for r in rows] == [first["turn_id"], second["turn_id"]]
    assert "body" not in rows[0]
    spec = db.app.openapi()["paths"]["/turns/since"]["get"]
    assert "x-mcp" not in spec
    advance_scan("endpoint-wake-dispatcher", 0, first["turn_id"], [first])
    with pytest.raises(WakeConflictError):
        advance_scan("endpoint-wake-dispatcher", 0, second["turn_id"], [second])
    with connect() as conn:
        count = conn.execute(
            "SELECT COUNT(*) AS n FROM endpoint_wake_ledger"
        ).fetchone()["n"]
    assert count == 1


def test_claim_once_and_ttl_folds_unknown(db: TestClient) -> None:
    detail = create_thread(thread_id=None, slug="claim")
    assert detail is not None
    item = _turn(str(detail["id"]))
    get_cursor()
    with connect() as conn:
        conn.execute("UPDATE endpoint_wake_cursor SET high_water_turn_id=0")
    advance_scan(
        "endpoint-wake-dispatcher",
        0,
        item["turn_id"],
        [item],
        now="2020-01-01 00:00:00",
    )
    barrier = threading.Barrier(2)
    results: list[str | None] = []

    def _claim() -> None:
        barrier.wait()
        claimed = claim_due_wakes(now="2020-01-01 00:00:01")
        results.append(claimed["wake_id"])

    threads = [threading.Thread(target=_claim) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(1 for w in results if w) == 1
    wake = next(w for w in results if w)
    assert fold_expired_claims(claim_ttl_s=300, now="2020-01-01 00:06:00") == 1
    again = claim_due_wakes(now="2020-01-01 00:06:01")
    assert again["wake_id"] is None
    with connect() as conn:
        state = conn.execute(
            "SELECT state FROM endpoint_wake_ledger WHERE wake_id=?", (wake,)
        ).fetchone()["state"]
    assert state == "unknown"


def test_stage_manager_equivalence_already_answered_and_debounce(
    db: TestClient,
) -> None:
    detail = create_thread(thread_id=None, slug="sm-equiv")
    assert detail is not None
    thread = str(detail["id"])
    t1_id, _ts, t1_n, _c = insert_turn(
        thread=thread,
        from_agent="cursor",
        to_agent="stage-manager",
        subject="REQUEST|x",
        body="b",
    )
    get_cursor()
    with connect() as conn:
        conn.execute("UPDATE endpoint_wake_cursor SET high_water_turn_id=0")
    advance_scan(
        "endpoint-wake-dispatcher",
        0,
        t1_id,
        [
            {
                "agent": "stage-manager",
                "thread_id": thread,
                "turn_id": t1_id,
                "turn_number": t1_n,
            }
        ],
        now="2020-01-01 10:00:00",
    )
    # Reply from grok-bot-gc1 before claim marks the stage-manager wake already_answered
    reply_id, _, _, _ = insert_turn(
        thread=thread,
        from_agent="grok-bot-gc1",
        to_agent="cursor",
        subject=f"ACK|x (re #{t1_n})",
        body="ack",
    )
    with write_connect() as conn:
        conn.execute(
            "UPDATE turns SET created_at=? WHERE id=?",
            ("2020-01-01T10:00:00Z", reply_id),
        )
    claimed = claim_due_wakes(now="2020-01-01 10:00:01")
    assert claimed["wake_id"] is None
    assert claimed["rows"][0]["state"] == "skipped"
    assert claimed["rows"][0]["reason"] == "already_answered"

    # Now test delivered wake for stage-manager has debounce cleared when grok-bot-gc1 replies
    t2_id, _ts2, t2_n, _c2 = insert_turn(
        thread=thread,
        from_agent="cursor",
        to_agent="stage-manager",
        subject="REQUEST|y",
        body="b2",
    )
    advance_scan(
        "endpoint-wake-dispatcher",
        t1_id,
        t2_id,
        [
            {
                "agent": "stage-manager",
                "thread_id": thread,
                "turn_id": t2_id,
                "turn_number": t2_n,
            }
        ],
        now="2020-01-01 10:01:00",
    )
    w2 = claim_due_wakes(now="2020-01-01 10:01:01")
    assert w2["wake_id"] is not None
    record_wake_result(
        w2["wake_id"],
        "delivered",
        "ok",
        "wildcard",
        1,
        None,
        now="2020-01-01 10:01:02",
    )
    t3_id, _ts3, t3_n, _c3 = insert_turn(
        thread=thread,
        from_agent="cursor",
        to_agent="stage-manager",
        subject="REQUEST|z",
        body="b3",
    )
    advance_scan(
        "endpoint-wake-dispatcher",
        t2_id,
        t3_id,
        [
            {
                "agent": "stage-manager",
                "thread_id": thread,
                "turn_id": t3_id,
                "turn_number": t3_n,
            }
        ],
        now="2020-01-01 10:01:10",
    )
    # Still within 600s debounce before reply
    assert claim_due_wakes(now="2020-01-01 10:01:20")["wake_id"] is None
    # Reply from grok-bot-gc1 after finished_at clears debounce for stage-manager
    r2_id, _, _, _ = insert_turn(
        thread=thread,
        from_agent="grok-bot-gc1",
        to_agent="cursor",
        subject="FACT|done",
        body="done",
    )
    with write_connect() as conn:
        conn.execute(
            "UPDATE turns SET created_at=? WHERE id=?",
            ("2020-01-01T10:01:30Z", r2_id),
        )
    # Need a new turn after r2_id so it is not skipped by already_answered
    t4_id, _ts4, t4_n, _c4 = insert_turn(
        thread=thread,
        from_agent="cursor",
        to_agent="stage-manager",
        subject="REQUEST|w",
        body="b4",
    )
    advance_scan(
        "endpoint-wake-dispatcher",
        t3_id,
        t4_id,
        [
            {
                "agent": "stage-manager",
                "thread_id": thread,
                "turn_id": t4_id,
                "turn_number": t4_n,
            }
        ],
        now="2020-01-01 10:01:35",
    )
    w4 = claim_due_wakes(now="2020-01-01 10:01:40")
    assert w4["wake_id"] is not None


def test_unrelated_same_seat_post_on_shared_root_does_not_skip_pending_wake(
    db: TestClient,
) -> None:
    detail = create_thread(thread_id=None, slug="unrelated-seat")
    assert detail is not None
    thread = str(detail["id"])
    t1_id, _ts, t1_n, _c = insert_turn(
        thread=thread,
        from_agent="maestro",
        to_agent="grok-bot-cc1",
        subject="ACK|gwdir-class",
        body="class",
    )
    get_cursor()
    with connect() as conn:
        conn.execute("UPDATE endpoint_wake_cursor SET high_water_turn_id=0")
    advance_scan(
        "endpoint-wake-dispatcher",
        0,
        t1_id,
        [
            {
                "agent": "grok-bot-cc1",
                "thread_id": thread,
                "turn_id": t1_id,
                "turn_number": t1_n,
            }
        ],
        now="2020-01-01 16:00:00",
    )
    insert_turn(
        thread=thread,
        from_agent="grok-bot-cc1",
        to_agent="maestro",
        subject="PLAN|other",
        body=f"re #1934\nsee 99999#{t1_n} for context",
    )
    claimed = claim_due_wakes(now="2020-01-01 16:00:01")
    assert claimed["wake_id"] is not None
    assert [r["turn_id"] for r in claimed["rows"] if r.get("state") == "claimed"] == [
        t1_id,
    ]
    with write_connect() as conn:
        conn.execute(
            "UPDATE endpoint_wake_ledger SET state='pending', wake_id=NULL, "
            "claimed_at=NULL, reason=NULL, finished_at=NULL WHERE turn_id=?",
            (t1_id,),
        )
    insert_turn(
        thread=thread,
        from_agent="grok-bot-cc1",
        to_agent="maestro",
        subject=f"ACK|re #{t1_n}",
        body="done",
    )
    skipped = claim_due_wakes(now="2020-01-01 16:00:02")
    assert skipped["wake_id"] is None
    assert skipped["rows"][0]["state"] == "skipped"
    assert skipped["rows"][0]["reason"] == "already_answered"


def test_stage_manager_alias_rows_coalesce_in_one_claim(db: TestClient) -> None:
    detail = create_thread(thread_id=None, slug="sm-coalesce")
    assert detail is not None
    thread = str(detail["id"])
    t1_id, _ts, t1_n, _c = insert_turn(
        thread=thread,
        from_agent="cursor",
        to_agent="stage-manager",
        subject="REQUEST|a",
        body="b",
    )
    t2_id, _ts2, t2_n, _c2 = insert_turn(
        thread=thread,
        from_agent="cursor",
        to_agent="grok-bot-gc1",
        subject="REQUEST|b",
        body="b2",
    )
    get_cursor()
    with connect() as conn:
        conn.execute("UPDATE endpoint_wake_cursor SET high_water_turn_id=0")
    advance_scan(
        "endpoint-wake-dispatcher",
        0,
        t2_id,
        [
            {
                "agent": "stage-manager",
                "thread_id": thread,
                "turn_id": t1_id,
                "turn_number": t1_n,
            },
            {
                "agent": "grok-bot-gc1",
                "thread_id": thread,
                "turn_id": t2_id,
                "turn_number": t2_n,
            },
        ],
        now="2020-01-01 11:00:00",
    )
    claimed = claim_due_wakes(now="2020-01-01 11:00:01")
    assert claimed["wake_id"] is not None
    turn_ids = sorted(
        r["turn_id"] for r in claimed["rows"] if r.get("state") == "claimed"
    )
    assert turn_ids == [t1_id, t2_id]


def test_debounce_clears_on_cross_thread_reply(db: TestClient) -> None:
    bus = create_thread(thread_id=None, slug="debounce-xthread")
    house = create_thread(thread_id=None, slug="debounce-house")
    assert bus is not None and house is not None
    bus_thread = str(bus["id"])
    house_thread = str(house["id"])
    a_id, _, a_n, _ = insert_turn(
        thread=bus_thread,
        from_agent="cursor",
        to_agent="stage-manager",
        subject="REQUEST|x",
        body="b",
    )
    get_cursor()
    with connect() as conn:
        conn.execute("UPDATE endpoint_wake_cursor SET high_water_turn_id=0")
    advance_scan(
        "endpoint-wake-dispatcher",
        0,
        a_id,
        [
            {
                "agent": "stage-manager",
                "thread_id": bus_thread,
                "turn_id": a_id,
                "turn_number": a_n,
            }
        ],
        now="2020-01-01 12:00:00",
    )
    w1 = claim_due_wakes(now="2020-01-01 12:00:01")
    record_wake_result(
        w1["wake_id"],
        "delivered",
        "ok",
        "wildcard",
        1,
        None,
        now="2020-01-01 12:00:02",
    )
    b_id, _, b_n, _ = insert_turn(
        thread=bus_thread,
        from_agent="cursor",
        to_agent="stage-manager",
        subject="REQUEST|y",
        body="b2",
    )
    advance_scan(
        "endpoint-wake-dispatcher",
        a_id,
        b_id,
        [
            {
                "agent": "stage-manager",
                "thread_id": bus_thread,
                "turn_id": b_id,
                "turn_number": b_n,
            }
        ],
        now="2020-01-01 12:00:10",
    )
    assert claim_due_wakes(now="2020-01-01 12:00:20")["wake_id"] is None
    reply_id, _, _, _ = insert_turn(
        thread=house_thread,
        from_agent="grok-bot-gc1",
        to_agent="cursor",
        subject="FACT|house",
        body="house",
    )
    with write_connect() as conn:
        conn.execute(
            "UPDATE turns SET created_at=? WHERE id=?",
            ("2020-01-01T12:00:30Z", reply_id),
        )
    w2 = claim_due_wakes(now="2020-01-01 12:00:35")
    assert w2["wake_id"] is not None
    assert [r["turn_id"] for r in w2["rows"] if r.get("state") == "claimed"] == [b_id]


def test_debounce_clears_when_delivered_turn_marked_read(db: TestClient) -> None:
    detail = create_thread(thread_id=None, slug="debounce-read")
    assert detail is not None
    thread = str(detail["id"])
    a_id, _, a_n, _ = insert_turn(
        thread=thread,
        from_agent="cursor",
        to_agent="grok-bot-gc1",
        subject="REQUEST|x",
        body="b",
    )
    get_cursor()
    with connect() as conn:
        conn.execute("UPDATE endpoint_wake_cursor SET high_water_turn_id=0")
    advance_scan(
        "endpoint-wake-dispatcher",
        0,
        a_id,
        [
            {
                "agent": "grok-bot-gc1",
                "thread_id": thread,
                "turn_id": a_id,
                "turn_number": a_n,
            }
        ],
        now="2020-01-01 13:00:00",
    )
    w1 = claim_due_wakes(now="2020-01-01 13:00:01")
    record_wake_result(
        w1["wake_id"],
        "delivered",
        "ok",
        "wildcard",
        1,
        None,
        now="2020-01-01 13:00:02",
    )
    b_id, _, b_n, _ = insert_turn(
        thread=thread,
        from_agent="cursor",
        to_agent="grok-bot-gc1",
        subject="REQUEST|y",
        body="b2",
    )
    advance_scan(
        "endpoint-wake-dispatcher",
        a_id,
        b_id,
        [
            {
                "agent": "grok-bot-gc1",
                "thread_id": thread,
                "turn_id": b_id,
                "turn_number": b_n,
            }
        ],
        now="2020-01-01 13:00:10",
    )
    assert claim_due_wakes(now="2020-01-01 13:00:20")["wake_id"] is None
    with write_connect() as conn:
        conn.execute(
            "UPDATE turns SET read_at=? WHERE id=?",
            ("2020-01-01 13:00:25", a_id),
        )
    w2 = claim_due_wakes(now="2020-01-01 13:00:30")
    assert w2["wake_id"] is not None
    assert [r["turn_id"] for r in w2["rows"] if r.get("state") == "claimed"] == [b_id]


def test_record_wake_result_parent_and_window_binding_routes(db: TestClient) -> None:
    """route=parent and window-binding must not violate endpoint_wake_ledger CHECK."""
    for slug, route in (("route-parent", "parent"), ("route-wb", "window-binding")):
        detail = create_thread(thread_id=None, slug=slug)
        assert detail is not None
        thread = str(detail["id"])
        turn_id, _, turn_number, _ = insert_turn(
            thread=thread,
            from_agent="cursor",
            to_agent="grok-bot-gc1",
            subject=f"REQUEST|{slug}",
            body="b",
        )
        get_cursor()
        with connect() as conn:
            conn.execute("UPDATE endpoint_wake_cursor SET high_water_turn_id=0")
        advance_scan(
            "endpoint-wake-dispatcher",
            0,
            turn_id,
            [
                {
                    "agent": "grok-bot-gc1",
                    "thread_id": thread,
                    "turn_id": turn_id,
                    "turn_number": turn_number,
                }
            ],
            now="2020-01-01 15:00:00",
        )
        claimed = claim_due_wakes(now="2020-01-01 15:00:01")
        assert claimed["wake_id"] is not None
        record_wake_result(
            claimed["wake_id"],
            "delivered",
            "ok",
            route,
            1,
            None,
            now="2020-01-01 15:00:02",
        )


def test_child_lane_closeout_matches_parent_thread_subscription(db: TestClient) -> None:
    """Parent-scoped Impresario subscription must cover child-lane CLOSEOUT turns."""
    from agent_bus_store.db.endpoint_subscriptions import subscribe_endpoint
    from agent_bus_store.db.endpoint_wake_ledger import resolve_subscription_for_thread
    from agent_bus_store.db.lane_associations import associate_lane

    parent = create_thread(thread_id=None, slug="root-xvfb")
    child = create_thread(thread_id=None, slug="lane-sdk")
    assert parent is not None and child is not None
    parent_id, child_id = str(parent["id"]), str(child["id"])
    associate_lane(
        thread_id=child_id,
        parent_thread_id=parent_id,
        lane_role="sub_mission",
        bound_by="test",
    )
    subscribe_endpoint(
        agent="impresario",
        thread_scope=parent_id,
        surface="cursor",
        display="8",
        title=f"{parent_id} continuity",
        agent_id="glass-agent-1",
    )
    resolved = resolve_subscription_for_thread("impresario", child_id)
    assert resolved is not None
    route, row = resolved
    assert route == "parent"
    assert row["thread_scope"] == parent_id

    turn_id, _, turn_number, _ = insert_turn(
        thread=child_id,
        from_agent="cursor-sdk",
        to_agent="impresario",
        subject="CLOSEOUT — lane complete",
        body="TYPE: CLOSEOUT\nstatus: complete\n",
    )
    get_cursor()
    with connect() as conn:
        conn.execute("UPDATE endpoint_wake_cursor SET high_water_turn_id=0")
    advance_scan(
        "endpoint-wake-dispatcher",
        0,
        turn_id,
        [
            {
                "agent": "impresario",
                "thread_id": child_id,
                "turn_id": turn_id,
                "turn_number": turn_number,
            }
        ],
        now="2020-01-01 14:00:00",
    )
    claimed = claim_due_wakes(now="2020-01-01 14:00:01")
    assert claimed["wake_id"] is not None
    assert claimed["rows"][0]["thread_id"] == child_id


def test_grok_bot_wake_skipped_already_read_when_turn_stamped(
    db: TestClient,
) -> None:
    detail = create_thread(thread_id=None, slug="l20-grok-read")
    assert detail is not None
    thread = str(detail["id"])
    t_id, _ts, t_n, _c = insert_turn(
        thread=thread,
        from_agent="cursor",
        to_agent="grok-bot-gc1",
        subject="VERDICT|g",
        body="b",
    )
    get_cursor()
    with connect() as conn:
        conn.execute("UPDATE endpoint_wake_cursor SET high_water_turn_id=0")
    advance_scan(
        "endpoint-wake-dispatcher",
        0,
        t_id,
        [
            {
                "agent": "grok-bot-gc1",
                "thread_id": thread,
                "turn_id": t_id,
                "turn_number": t_n,
            }
        ],
        now="2020-01-01 17:05:00",
    )
    with write_connect() as conn:
        conn.execute(
            "UPDATE turns SET read_at=? WHERE id=?",
            ("2020-01-01 17:05:00", t_id),
        )
    skipped = claim_due_wakes(now="2020-01-01 17:05:01")
    assert skipped["wake_id"] is None
    assert skipped["rows"][0]["state"] == "skipped"
    assert skipped["rows"][0]["reason"] == "already_read"


def test_maestro_wake_not_skipped_already_read_when_turn_stamped(
    db: TestClient,
) -> None:
    detail = create_thread(thread_id=None, slug="l20-maestro-read")
    assert detail is not None
    thread = str(detail["id"])
    t_id, _ts, t_n, _c = insert_turn(
        thread=thread,
        from_agent="cursor",
        to_agent="maestro",
        subject="VERDICT|x",
        body="b",
    )
    get_cursor()
    with connect() as conn:
        conn.execute("UPDATE endpoint_wake_cursor SET high_water_turn_id=0")
    advance_scan(
        "endpoint-wake-dispatcher",
        0,
        t_id,
        [
            {
                "agent": "maestro",
                "thread_id": thread,
                "turn_id": t_id,
                "turn_number": t_n,
            }
        ],
        now="2020-01-01 17:00:00",
    )
    with write_connect() as conn:
        conn.execute(
            "UPDATE turns SET read_at=? WHERE id=?",
            ("2020-01-01 17:00:00", t_id),
        )
    claimed = claim_due_wakes(now="2020-01-01 17:00:01")
    assert claimed["wake_id"] is not None
    assert [r["turn_id"] for r in claimed["rows"] if r.get("state") == "claimed"] == [
        t_id,
    ]


def test_web_anthropic_wake_not_skipped_already_read_when_turn_stamped(
    db: TestClient,
) -> None:
    detail = create_thread(thread_id=None, slug="l20-web-read")
    assert detail is not None
    thread = str(detail["id"])
    t_id, _ts, t_n, _c = insert_turn(
        thread=thread,
        from_agent="cursor",
        to_agent="web-anthropic",
        subject="VERDICT|y",
        body="b",
    )
    get_cursor()
    with connect() as conn:
        conn.execute("UPDATE endpoint_wake_cursor SET high_water_turn_id=0")
    advance_scan(
        "endpoint-wake-dispatcher",
        0,
        t_id,
        [
            {
                "agent": "web-anthropic",
                "thread_id": thread,
                "turn_id": t_id,
                "turn_number": t_n,
            }
        ],
        now="2020-01-01 17:10:00",
    )
    with write_connect() as conn:
        conn.execute(
            "UPDATE turns SET read_at=? WHERE id=?",
            ("2020-01-01 17:10:00", t_id),
        )
    claimed = claim_due_wakes(now="2020-01-01 17:10:01")
    assert claimed["wake_id"] is not None


def test_pair_mark_read_skips_peer_pending_wake_ledger(db: TestClient) -> None:
    from agent_bus_store.db.turns import bulk_mark_read_state

    detail = create_thread(thread_id=None, slug="l20-pair-mark-read-skip")
    assert detail is not None
    thread = str(detail["id"])
    t_id, _ts, t_n, _c = insert_turn(
        thread=thread,
        from_agent="cursor",
        to_agent="web-anthropic",
        subject="VERDICT|pair-skip",
        body="b",
    )
    get_cursor()
    with connect() as conn:
        conn.execute("UPDATE endpoint_wake_cursor SET high_water_turn_id=0")
    advance_scan(
        "endpoint-wake-dispatcher",
        0,
        t_id,
        [
            {
                "agent": "web-anthropic",
                "thread_id": thread,
                "turn_id": t_id,
                "turn_number": t_n,
            }
        ],
        now="2020-01-01 17:30:00",
    )
    marked = bulk_mark_read_state(
        thread=thread, through_turn=t_n, agent="maestro"
    )
    assert marked == 1
    with connect() as conn:
        row = conn.execute(
            "SELECT state, reason FROM endpoint_wake_ledger WHERE turn_id=?",
            (t_id,),
        ).fetchone()
    assert row["state"] == "skipped"
    assert row["reason"] == "already_read"
    claimed = claim_due_wakes(now="2020-01-01 17:30:01")
    assert claimed["wake_id"] is None


def test_web_anthropic_reply_skips_maestro_wake_already_answered(
    db: TestClient,
) -> None:
    detail = create_thread(thread_id=None, slug="l20-web-answers-maestro")
    assert detail is not None
    thread = str(detail["id"])
    t_id, _ts, t_n, _c = insert_turn(
        thread=thread,
        from_agent="cursor",
        to_agent="maestro",
        subject="VERDICT|cross-seat",
        body="b",
    )
    get_cursor()
    with connect() as conn:
        conn.execute("UPDATE endpoint_wake_cursor SET high_water_turn_id=0")
    advance_scan(
        "endpoint-wake-dispatcher",
        0,
        t_id,
        [
            {
                "agent": "maestro",
                "thread_id": thread,
                "turn_id": t_id,
                "turn_number": t_n,
            }
        ],
        now="2020-01-01 17:35:00",
    )
    insert_turn(
        thread=thread,
        from_agent="web-anthropic",
        to_agent="cursor",
        subject=f"ACK|re #{t_n}",
        body="done",
    )
    skipped = claim_due_wakes(now="2020-01-01 17:35:01")
    assert skipped["wake_id"] is None
    assert skipped["rows"][0]["state"] == "skipped"
    assert skipped["rows"][0]["reason"] == "already_answered"


def test_maestro_wake_still_skipped_already_answered(db: TestClient) -> None:
    detail = create_thread(thread_id=None, slug="l20-maestro-answered")
    assert detail is not None
    thread = str(detail["id"])
    t_id, _ts, t_n, _c = insert_turn(
        thread=thread,
        from_agent="cursor",
        to_agent="maestro",
        subject="VERDICT|z",
        body="b",
    )
    get_cursor()
    with connect() as conn:
        conn.execute("UPDATE endpoint_wake_cursor SET high_water_turn_id=0")
    advance_scan(
        "endpoint-wake-dispatcher",
        0,
        t_id,
        [
            {
                "agent": "maestro",
                "thread_id": thread,
                "turn_id": t_id,
                "turn_number": t_n,
            }
        ],
        now="2020-01-01 17:20:00",
    )
    insert_turn(
        thread=thread,
        from_agent="maestro",
        to_agent="cursor",
        subject=f"ACK|re #{t_n}",
        body="done",
    )
    skipped = claim_due_wakes(now="2020-01-01 17:20:01")
    assert skipped["wake_id"] is None
    assert skipped["rows"][0]["state"] == "skipped"
    assert skipped["rows"][0]["reason"] == "already_answered"


def test_to_all_turn_does_not_qualify_for_wake(db: TestClient) -> None:
    from bus_watch.endpoint_wake_dispatcher import qualifies

    detail = create_thread(thread_id=None, slug="l20-all-no-wake")
    assert detail is not None
    thread = str(detail["id"])
    t_id, _ts, t_n, _c = insert_turn(
        thread=thread,
        from_agent="cursor",
        to_agent="all",
        subject="VERDICT|broadcast",
        body="b",
    )
    turn = {
        "id": t_id,
        "thread": thread,
        "turn_number": t_n,
        "from_agent": "cursor",
        "to_agent": "all",
        "subject": "VERDICT|broadcast",
        "body": "b",
    }
    assert qualifies(turn, cse=True) is False


def test_parent_scope_unsubscribe_tombstone_honored(db: TestClient) -> None:
    """Unsubscribing parent scope must not match a stale subscribed journal row."""
    from agent_bus_store.db.endpoint_subscriptions import (
        subscribe_endpoint,
        unsubscribe_endpoint,
    )
    from agent_bus_store.db.endpoint_wake_ledger import resolve_subscription_for_thread
    from agent_bus_store.db.lane_associations import associate_lane

    parent = create_thread(thread_id=None, slug="root-unsub")
    child = create_thread(thread_id=None, slug="lane-unsub")
    assert parent is not None and child is not None
    parent_id, child_id = str(parent["id"]), str(child["id"])
    associate_lane(
        thread_id=child_id,
        parent_thread_id=parent_id,
        lane_role="sub_mission",
        bound_by="test",
    )
    subscribe_endpoint(
        agent="impresario",
        thread_scope=parent_id,
        surface="cursor",
        display="8",
        title=f"{parent_id} continuity",
        agent_id="glass-agent-1",
    )
    assert resolve_subscription_for_thread("impresario", child_id) is not None
    unsubscribe_endpoint(agent="impresario", thread_scope=parent_id)
    assert resolve_subscription_for_thread("impresario", child_id) is None
