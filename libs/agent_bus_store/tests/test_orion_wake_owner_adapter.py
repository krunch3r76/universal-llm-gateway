"""Offline tests for shared_wake_delivery_owner (20995 ledger owner API)."""

from __future__ import annotations

import pytest
from agent_bus_store.db import create_thread, init_db, insert_turn
from agent_bus_store.db.connection import connect, write_connect
from agent_bus_store.db.endpoint_wake_ledger import (
    advance_scan,
    claim_due_wakes,
    get_cursor,
    resolve_wake_delivery_binding,
)
from agent_bus_store.db.orion_wake_owner_adapter import (
    WakeClaimContext,
    shared_wake_delivery_owner,
)
from bus_watch.endpoint_wake_dispatcher import _orion_synthetic_route
from web_chat_relay.orion_wake import (
    PINNED_CONVERSATION_ID,
    PINNED_CONVERSATION_URL,
    holder_conversation_url,
)

pytestmark = pytest.mark.offline

_ORION_HOUSE = "21207"


@pytest.fixture()
def db(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    monkeypatch.delenv("AGENT_BUS_ENDPOINT_WAKE_DISPATCHER", raising=False)
    init_db()


def _claim_orion_turn(
    db: None, *, thread: str = _ORION_HOUSE
) -> tuple[str, list[int], int]:
    create_thread(thread_id=thread, slug="orion-wake")
    turn_id, _ts, number, _c = insert_turn(
        thread=thread,
        from_agent="maestro",
        to_agent="orion",
        subject="REQUEST|x",
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
                "agent": "orion",
                "thread_id": thread,
                "turn_id": turn_id,
                "turn_number": number,
            }
        ],
        now="2020-01-01 00:00:00",
    )
    claimed = claim_due_wakes(now="2020-01-01 00:00:30")
    assert claimed["wake_id"]
    return str(claimed["wake_id"]), [turn_id], int(number)


def test_shared_wake_delivery_owner_validates_claim(db: None) -> None:
    """Break: pipeline runs without a claimed wake_id on the ledger."""
    wake_id, turn_ids, number = _claim_orion_turn(db)
    owner = shared_wake_delivery_owner()
    claim = WakeClaimContext(
        wake_id=wake_id,
        agent="orion",
        thread_id=_ORION_HOUSE,
        turn_ids=tuple(turn_ids),
        turn_numbers=(number,),
        endpoint_url=PINNED_CONVERSATION_URL,
    )
    out = owner.validate_claim_for_delivery(claim)
    assert out == {"ok": True, "reason": "claim_valid"}


def test_synthetic_route_holder_url_matches_binding_for_validate(db: None) -> None:
    """Break: dispatcher sub shape → holder flat URL → wake_claim_endpoint_mismatch."""
    wake_id, turn_ids, number = _claim_orion_turn(db)
    bound = _orion_synthetic_route("orion", _ORION_HOUSE)
    assert bound is not None
    _route_kind, sub = bound
    resolved_endpoint = holder_conversation_url(sub)
    binding = resolve_wake_delivery_binding(house_id=_ORION_HOUSE)
    bound_url = str(binding["current"]["actuation"]["conversation_url"]).split("?", 1)[
        0
    ]
    assert resolved_endpoint == bound_url

    owner = shared_wake_delivery_owner()
    claim = WakeClaimContext(
        wake_id=wake_id,
        agent="orion",
        thread_id=_ORION_HOUSE,
        turn_ids=tuple(turn_ids),
        turn_numbers=(number,),
        endpoint_url=resolved_endpoint,
    )
    assert owner.validate_claim_for_delivery(claim) == {
        "ok": True,
        "reason": "claim_valid",
    }

    flat_only = f"https://chatgpt.com/c/{PINNED_CONVERSATION_ID}"
    bad = WakeClaimContext(
        wake_id=wake_id,
        agent="orion",
        thread_id=_ORION_HOUSE,
        turn_ids=tuple(turn_ids),
        turn_numbers=(number,),
        endpoint_url=flat_only,
    )
    assert (
        owner.validate_claim_for_delivery(bad)["reason"]
        == "wake_claim_endpoint_mismatch"
    )


def test_shared_wake_delivery_owner_fail_closed_incomplete(db: None) -> None:
    owner = shared_wake_delivery_owner()
    claim = WakeClaimContext(
        wake_id="",
        agent="orion",
        thread_id=_ORION_HOUSE,
        turn_ids=(1,),
        turn_numbers=(1,),
        endpoint_url=PINNED_CONVERSATION_URL,
    )
    assert owner.validate_claim_for_delivery(claim)["ok"] is False


def test_shared_wake_delivery_owner_rejects_stale_wake_id(db: None) -> None:
    _claim_orion_turn(db)
    owner = shared_wake_delivery_owner()
    claim = WakeClaimContext(
        wake_id="not-a-live-claim",
        agent="orion",
        thread_id=_ORION_HOUSE,
        turn_ids=(1,),
        turn_numbers=(1,),
        endpoint_url=PINNED_CONVERSATION_URL,
    )
    assert owner.validate_claim_for_delivery(claim)["reason"] == "wake_not_claimed"


def test_shared_wake_delivery_owner_endpoint_mismatch(db: None) -> None:
    wake_id, turn_ids, number = _claim_orion_turn(db)
    owner = shared_wake_delivery_owner()
    claim = WakeClaimContext(
        wake_id=wake_id,
        agent="orion",
        thread_id=_ORION_HOUSE,
        turn_ids=tuple(turn_ids),
        turn_numbers=(number,),
        endpoint_url="https://chatgpt.com/c/11111111-2222-3333-4444-555555555555",
    )
    assert (
        owner.validate_claim_for_delivery(claim)["reason"]
        == "wake_claim_endpoint_mismatch"
    )


def test_resolve_orion_house_wake_binding(db: None) -> None:
    binding = resolve_wake_delivery_binding(house_id=_ORION_HOUSE)
    assert binding["state"] == "bound"
    actuation = binding["current"]["actuation"]
    assert actuation["type"] == "orion_chat"
    assert actuation["conversation_id"] == PINNED_CONVERSATION_ID
    assert actuation["conversation_url"] == PINNED_CONVERSATION_URL.split("?", 1)[0]


def test_resolve_unbound_house(db: None) -> None:
    binding = resolve_wake_delivery_binding(house_id="99999")
    assert binding["state"] == "unbound"
    assert binding["current"] is None
    assert binding["reason"] == "unbound"


def test_ledger_identity_unique_per_turn(db: None) -> None:
    """UNIQUE(agent, thread_id, turn_id) — second insert for same turn is ignored."""
    create_thread(thread_id="21220", slug="dup")
    turn_id, _ts, number, _c = insert_turn(
        thread="21220",
        from_agent="cursor",
        to_agent="orion",
        subject="FACT|x",
        body="b",
    )
    item = {
        "agent": "orion",
        "thread_id": "21220",
        "turn_id": turn_id,
        "turn_number": number,
    }
    get_cursor()
    with write_connect() as conn:
        conn.execute("UPDATE endpoint_wake_cursor SET high_water_turn_id=0")
    advance_scan(
        "endpoint-wake-dispatcher", 0, turn_id, [item], now="2020-01-01 00:00:00"
    )
    advance_scan(
        "endpoint-wake-dispatcher", turn_id, turn_id, [item], now="2020-01-01 00:00:01"
    )
    with connect() as conn:
        count = conn.execute(
            "SELECT COUNT(*) AS n FROM endpoint_wake_ledger WHERE turn_id=?",
            (turn_id,),
        ).fetchone()["n"]
    assert count == 1
