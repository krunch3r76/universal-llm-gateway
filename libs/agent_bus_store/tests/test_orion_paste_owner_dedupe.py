"""Shared owner paste dedupe, concurrency, restart — replaces in-memory OrionWakeDedupeLedger."""

from __future__ import annotations

import threading
from pathlib import Path
from unittest.mock import patch

import pytest
from agent_bus_store.db import create_thread, init_db, insert_turn
from agent_bus_store.db.connection import connect
from agent_bus_store.db.orion_wake_owner_adapter import (
    WakeClaimContext,
    shared_wake_delivery_owner,
)
from web_chat_relay.orion_wake import PINNED_CONVERSATION_URL

pytestmark = pytest.mark.offline

_ORION_HOUSE = "21207"


@pytest.fixture()
def db(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    monkeypatch.delenv("AGENT_BUS_ENDPOINT_WAKE_DISPATCHER", raising=False)
    init_db()


def _claim(*, slug: str = "paste-dedupe") -> WakeClaimContext:
    create_thread(thread_id=_ORION_HOUSE, slug=slug)
    turn_id, _ts, number, _c = insert_turn(
        thread=_ORION_HOUSE,
        from_agent="maestro",
        to_agent="orion",
        subject="REQUEST|x",
        body="b",
    )
    endpoint = PINNED_CONVERSATION_URL.split("?", 1)[0]
    return WakeClaimContext(
        wake_id="wake-dedupe-a",
        agent="orion",
        thread_id=_ORION_HOUSE,
        turn_ids=(int(turn_id),),
        turn_numbers=(int(number),),
        endpoint_url=endpoint,
    )


def test_shared_owner_duplicate_suppressed_returns_first_receipt(db: None) -> None:
    """Break: second paste on same (agent, thread, turn_id) without owner dedupe."""
    owner = shared_wake_delivery_owner()
    claim = _claim()
    assert owner.prepare_paste_delivery(claim)["ok"] is True
    owner.record_paste_receipt(
        claim, {"ok": True, "delivered": True, "pasted": True, "pointer_len": 42}
    )
    again = owner.prepare_paste_delivery(
        WakeClaimContext(
            wake_id="wake-dedupe-b",
            agent=claim.agent,
            thread_id=claim.thread_id,
            turn_ids=claim.turn_ids,
            turn_numbers=claim.turn_numbers,
            endpoint_url=claim.endpoint_url,
        )
    )
    assert again["reason"] == "duplicate_suppressed"
    assert again["first_receipt"]["pointer_len"] == 42


def test_shared_owner_restart_still_duplicate_suppressed(db: None) -> None:
    claim = _claim(slug="paste-restart")
    owner1 = shared_wake_delivery_owner()
    assert owner1.prepare_paste_delivery(claim)["ok"] is True
    owner1.record_paste_receipt(
        claim, {"ok": True, "delivered": True, "pasted": True, "pointer_len": 7}
    )
    owner2 = shared_wake_delivery_owner()
    retry = owner2.prepare_paste_delivery(
        WakeClaimContext(
            wake_id="wake-after-restart",
            agent=claim.agent,
            thread_id=claim.thread_id,
            turn_ids=claim.turn_ids,
            turn_numbers=claim.turn_numbers,
            endpoint_url=claim.endpoint_url,
        )
    )
    assert retry["reason"] == "duplicate_suppressed"


def test_concurrent_paste_claim_exactly_one_acquires(db: None) -> None:
    """Break: two callers both acquire the same ledger key and paste."""
    claim_a = _claim(slug="paste-race")
    claim_b = WakeClaimContext(
        wake_id="wake-race-b",
        agent=claim_a.agent,
        thread_id=claim_a.thread_id,
        turn_ids=claim_a.turn_ids,
        turn_numbers=claim_a.turn_numbers,
        endpoint_url=claim_a.endpoint_url,
    )
    barrier = threading.Barrier(2)
    results: list[dict] = []

    def _try(claim: WakeClaimContext) -> None:
        barrier.wait()
        owner = shared_wake_delivery_owner()
        results.append(owner.prepare_paste_delivery(claim))

    threads = [
        threading.Thread(target=_try, args=(claim_a,)),
        threading.Thread(target=_try, args=(claim_b,)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    ok_count = sum(1 for r in results if r.get("ok") is True)
    assert ok_count == 1
    other = next(r for r in results if not r.get("ok"))
    assert other["reason"] in {"paste_in_flight", "wake_claim_invalid"}


def test_production_modules_do_not_reference_orion_wake_dedupe_ledger() -> None:
    repo = Path(__file__).resolve().parents[3]
    scan_roots = (repo / "libs", repo / "pipelines", repo / "services")
    offenders: list[str] = []
    for root in scan_roots:
        if not root.is_dir():
            continue
        for path in root.rglob("*.py"):
            rel = path.relative_to(repo)
            parts = rel.parts
            if "tests" in parts or path.name.startswith("test_"):
                continue
            if "OrionWakeDedupeLedger" in path.read_text(encoding="utf-8"):
                offenders.append(str(rel))
    assert offenders == []


@pytest.mark.asyncio
async def test_deliver_orion_chat_wake_uses_owner_dedupe(db: None) -> None:
    from web_chat_relay.orion_wake import deliver_orion_chat_wake

    create_thread(thread_id=_ORION_HOUSE, slug="chat-wake")
    turn_id, _ts, number, _c = insert_turn(
        thread=_ORION_HOUSE,
        from_agent="maestro",
        to_agent="orion",
        subject="REQUEST|y",
        body="b",
    )
    calls = 0

    async def _fake_deliver(**_kwargs: object) -> dict:
        nonlocal calls
        calls += 1
        return {"ok": True, "delivered": True, "pasted": True, "pointer_len": 99}

    opts = {
        "thread": _ORION_HOUSE,
        "turn": int(number),
        "turn_ids": [int(turn_id)],
        "conversation_url": PINNED_CONVERSATION_URL,
        "wake_id": "wake-pipeline-1",
    }
    with patch("web_chat_relay.orion_wake.deliver_orion_wake", side_effect=_fake_deliver):
        first = await deliver_orion_chat_wake(
            thread=_ORION_HOUSE,
            turn=int(number),
            from_agent="maestro",
            pipeline_options=opts,
        )
        opts["wake_id"] = "wake-pipeline-2"
        second = await deliver_orion_chat_wake(
            thread=_ORION_HOUSE,
            turn=int(number),
            from_agent="maestro",
            pipeline_options=opts,
        )
    assert first.get("delivered") is True
    assert second["reason"] == "duplicate_suppressed"
    assert calls == 1
    with connect() as conn:
        row = conn.execute(
            "SELECT state FROM endpoint_wake_ledger WHERE turn_id=?",
            (int(turn_id),),
        ).fetchone()
    assert row["state"] == "delivered"
