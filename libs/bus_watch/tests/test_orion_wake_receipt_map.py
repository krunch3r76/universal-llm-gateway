"""Pinned Orion receipt → ledger outcomes; offline ledger row for owner path."""

from __future__ import annotations

import json

import pytest
from bus_watch.endpoint_wake_transport import classify_orion
from bus_watch.orion_wake_receipt_map import map_orion_receipt_to_outcomes

pytestmark = pytest.mark.offline

_PINNED_EXPECTED: list[tuple[dict, tuple[str, str]]] = [
    (
        {"ok": False, "reason": "composer_not_empty", "pasted": False},
        ("pending", "composer_not_empty"),
    ),
    (
        {"ok": False, "reason": "non_empty_composer", "pasted": False},
        ("pending", "non_empty_composer"),
    ),
    (
        {"ok": False, "reason": "composer_selection_active", "pasted": False},
        ("pending", "composer_selection_active"),
    ),
    (
        {"ok": False, "reason": "composer_not_focused", "pasted": False},
        ("pending", "composer_not_focused"),
    ),
    (
        {"ok": False, "reason": "operator_active", "pasted": False},
        ("pending", "operator_active"),
    ),
    (
        {"ok": False, "reason": "conversation_mismatch", "pasted": False},
        ("pending", "conversation_mismatch"),
    ),
    (
        {"ok": False, "reason": "no_composer", "pasted": False},
        ("pending", "no_composer"),
    ),
    (
        {
            "ok": False,
            "reason": "cdp_transport_error",
            "retryable": True,
            "pasted": False,
            "phase": "pre_insert",
        },
        ("pending", "cdp_transport_error"),
    ),
    (
        {
            "ok": False,
            "reason": "cdp_transport_error",
            "retryable": True,
            "pasted": True,
            "insert_attempted": True,
            "phase": "post_insert",
        },
        ("unknown", "insert_outcome_unknown"),
    ),
    (
        {
            "ok": False,
            "reason": "delivery_unconfirmed",
            "pasted": True,
            "delivered": False,
        },
        ("unknown", "delivery_unconfirmed"),
    ),
    (
        {"ok": False, "clicked_unknown": True, "pasted": True, "delivered": False},
        ("unknown", "send_outcome_unknown"),
    ),
    (
        {
            "ok": True,
            "send_fallback_clicked": True,
            "pasted": True,
            "delivered": False,
        },
        ("unknown", "insert_outcome_unknown"),
    ),
    (
        {
            "ok": False,
            "reason": "send_button_disabled",
            "insert_attempted": True,
            "retryable": True,
            "pasted": False,
        },
        ("pending", "send_button_disabled"),
    ),
    (
        {"ok": True, "delivered": True, "reason": "delivered"},
        ("delivered", "delivered"),
    ),
    (
        {
            "ok": False,
            "reason": "paste_failed",
            "pasted": False,
            "error": "composer_not_empty",
        },
        ("refused", "composer_not_empty"),
    ),
    (
        {
            "ok": False,
            "reason": "paste_failed",
            "pasted": False,
            "error": "tab not created by agent has focus or is visible to the operator",
        },
        (
            "refused",
            "tab not created by agent has focus or is visible to the operator",
        ),
    ),
]


@pytest.mark.parametrize(
    "case, expected",
    _PINNED_EXPECTED,
    ids=[f"{i}:{c[0].get('reason', c[0])}" for i, c in enumerate(_PINNED_EXPECTED)],
)
def test_map_orion_receipt_to_outcomes_pinned(
    case: dict, expected: tuple[str, str]
) -> None:
    st_e, rs_e = expected
    st_m, rs_m, _extra = map_orion_receipt_to_outcomes(case)
    assert (st_m, rs_m) == (st_e, rs_e)
    assert classify_orion(case) == (st_e, rs_e)


@pytest.fixture()
def ledger_db(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    monkeypatch.delenv("AGENT_BUS_ENDPOINT_WAKE_DISPATCHER", raising=False)
    from agent_bus_store.db import init_db

    init_db()


@pytest.mark.no_dom_stub
@pytest.mark.asyncio
async def test_deliver_classify_persists_unknown_row(
    ledger_db: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After committed _dom_deliver, approval exception → unknown paste_receipt on ledger."""
    from agent_bus_store.db import create_thread, insert_turn
    from agent_bus_store.db.connection import connect
    from agent_bus_store.db.endpoint_wake_ledger import (
        advance_scan,
        claim_due_wakes,
        get_cursor,
    )
    from agent_bus_store.db.orion_wake_owner_adapter import LedgerWakeDeliveryOwner
    from web_chat_relay.orion_wake import (
        PINNED_CONVERSATION_URL,
        deliver_orion_chat_wake,
    )
    from web_chat_relay.tests.test_orion_wake import _FakePage

    boom_count = {"n": 0}

    async def _dom_ok(*_a: object, **_k: object) -> dict:
        return {"ok": True, "receipt": "dom_committed"}

    async def _boom(_page: object) -> dict:
        boom_count["n"] += 1
        raise RuntimeError("approval probe")

    monkeypatch.setattr("web_chat_relay.orion_wake._dom_deliver", _dom_ok)
    monkeypatch.setattr("web_chat_relay.orion_wake._handle_approvals", _boom)

    create_thread(thread_id="21207", slug="orion-rmap")
    turn_id, _ts, number, _c = insert_turn(
        thread="21207",
        from_agent="cursor-sdk",
        to_agent="orion",
        subject="FACT|wake",
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
                "thread_id": "21207",
                "turn_id": turn_id,
                "turn_number": number,
            }
        ],
        now="2020-01-01 00:00:00",
    )
    claimed = claim_due_wakes(now="2020-01-01 00:00:30")
    wake_id = str(claimed["wake_id"])
    assert wake_id
    owner = LedgerWakeDeliveryOwner()
    out = await deliver_orion_chat_wake(
        thread="21207",
        turn=number,
        from_agent="maestro",
        page=_FakePage(url=PINNED_CONVERSATION_URL),
        conversation_url=PINNED_CONVERSATION_URL,
        pipeline_options={
            "wake_id": wake_id,
            "thread": "21207",
            "turn_id": turn_id,
            "turn": number,
            "conversation_url": PINNED_CONVERSATION_URL,
            "agent": "orion",
        },
        delivery_owner=owner,
    )
    assert out["reason"] == "insert_outcome_unknown"
    assert boom_count["n"] == 1
    assert classify_orion(out) == ("unknown", "insert_outcome_unknown")

    with connect() as conn:
        row = conn.execute(
            "SELECT state, reason, delivery_failure_detail FROM endpoint_wake_ledger "
            "WHERE wake_id=?",
            (wake_id,),
        ).fetchone()
    assert row is not None
    assert str(row["state"]) == "unknown"
    text = str(row["reason"])
    assert text.startswith("paste_receipt:")
    payload = json.loads(text[len("paste_receipt:") :])
    assert payload.get("outcome") == "unknown"
    assert payload.get("reason") == "insert_outcome_unknown"
    det_raw = row["delivery_failure_detail"]
    detail = json.loads(str(det_raw)) if det_raw else {}
    assert detail.get("reason_code") == "insert_outcome_unknown"
