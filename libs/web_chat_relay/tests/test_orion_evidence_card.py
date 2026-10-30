"""Orion wake evidence card — offline acceptance tests."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, patch

import pytest
from agent_bus_store.db import create_thread, init_db, insert_turn
from agent_bus_store.db.orion_wake_owner_adapter import shared_wake_delivery_owner
from web_chat_relay.orion_evidence_card import (
    CARD_BEGIN,
    CARD_END,
    MAX_CARD_BYTES,
    REF_LINE,
    build_evidence_card,
    compose_orion_chat_wake_paste,
    extract_reference_ids,
    maybe_append_evidence_card,
)
from web_chat_relay.orion_wake import (
    PINNED_CONVERSATION_URL,
    build_wake_pointer,
    deliver_orion_chat_wake,
    deliver_orion_wake,
)

pytestmark = pytest.mark.offline

_ORION_HOUSE = "21207"
_GOLDEN_POINTER = build_wake_pointer(thread=_ORION_HOUSE, turn=74, from_agent="maestro")


@pytest.fixture()
def db(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    monkeypatch.delenv("ORION_EVIDENCE_CARD", raising=False)
    init_db()


def test_compose_wake_paste_joins_distinct_senders_in_order() -> None:
    turns = [
        {
            "thread": _ORION_HOUSE,
            "turn_number": 302,
            "subject": "A",
            "body": "b",
            "from_agent": "maestro",
        },
        {
            "thread": _ORION_HOUSE,
            "turn_number": 303,
            "subject": "B",
            "body": "b",
            "from_agent": "cursor",
        },
    ]
    paste = compose_orion_chat_wake_paste(
        thread=_ORION_HOUSE,
        turn="302–303",
        from_agent="maestro",
        turns=turns,
    )
    assert "(from maestro, cursor)" in paste


def test_ac1_flag_off_paste_byte_identical_to_pointer(db: None) -> None:
    """Break: accidental card append when flag unset."""
    turns = [
        {
            "thread": _ORION_HOUSE,
            "turn_number": 74,
            "subject": "REQUEST|x todo:wire-gate",
            "body": "see a:99",
        }
    ]
    paste = compose_orion_chat_wake_paste(
        thread=_ORION_HOUSE,
        turn=74,
        from_agent="maestro",
        turns=turns,
    )
    assert paste == _GOLDEN_POINTER


def test_ac2_flag_on_appends_card_with_markers_and_byte_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ORION_EVIDENCE_CARD", "1")
    turns = [
        {
            "thread": "12286",
            "turn_number": 1,
            "subject": "MEMO",
            "body": "todo:wire-gate and a:101",
        }
    ]

    def _entity_get(**kwargs: object) -> dict:
        assert kwargs.get("intent") == "card"
        return {
            "items": [
                {
                    "id": "todo:wire-gate",
                    "summary_row": "Wire the gate",
                    "name": "wire-gate",
                    "top_k_assertions": [],
                    "assertion_counts": {"active": 1, "superseded": 0},
                }
            ],
            "count": 1,
        }

    def _assertion_get(**kwargs: object) -> dict:
        return {
            "id": 101,
            "claim": "Gate must fail closed",
            "confidence": "confirmed",
            "valid_from": "2026-01-01",
            "observed_at": None,
            "superseded_by": None,
            "review_status": None,
        }

    card = build_evidence_card(
        turns,
        entity_get_fn=_entity_get,
        assertion_get_fn=_assertion_get,
        blocked_ids=set(),
    )
    assert card is not None
    assert CARD_BEGIN in card
    assert CARD_END in card
    assert REF_LINE in card
    assert "todo:wire-gate" in card
    assert "a:101" in card
    assert len(card.encode("utf-8")) <= MAX_CARD_BYTES

    with patch(
        "web_chat_relay.orion_evidence_card.build_evidence_card",
        return_value=card,
    ):
        paste = maybe_append_evidence_card(_GOLDEN_POINTER, turns)
    assert paste.startswith(_GOLDEN_POINTER)
    assert CARD_BEGIN in paste


@pytest.mark.asyncio
async def test_ac2_batch_wake_one_paste_one_card(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Break: per-turn pastes → two cards; expect one paste with one card."""
    monkeypatch.setenv("ORION_EVIDENCE_CARD", "1")
    create_thread(thread_id=_ORION_HOUSE, slug="orion-evidence-batch")
    turn_ids: list[int] = []
    for n in (10, 11):
        turn_id, _ts, number, _c = insert_turn(
            thread=_ORION_HOUSE,
            from_agent="maestro",
            to_agent="orion",
            subject=f"REQUEST|{n} todo:batch-a",
            body=f"12286#{n}",
        )
        turn_ids.append(int(turn_id))
    endpoint = PINNED_CONVERSATION_URL.split("?", 1)[0]
    paste_calls: list[str] = []

    async def _fake_deliver(*, paste_text: str | None = None, **kwargs: object) -> dict:
        paste_calls.append(paste_text or "")
        return {"ok": True, "delivered": True, "pasted": True, "reason": "delivered"}

    opts = {
        "thread": _ORION_HOUSE,
        "turn": "10,11",
        "from_agent": "maestro",
        "conversation_url": endpoint,
        "wake_id": "wake-evidence-batch",
        "turn_ids": turn_ids,
        "turn_numbers": [10, 11],
        "agent": "orion",
    }
    owner = shared_wake_delivery_owner()
    fake_card = "\n".join(
        [CARD_BEGIN, REF_LINE, "• todo:batch-a | Batch entity", CARD_END]
    )

    with (
        patch(
            "web_chat_relay.orion_wake.deliver_orion_wake",
            new=AsyncMock(side_effect=_fake_deliver),
        ),
        patch(
            "web_chat_relay.orion_evidence_card.build_evidence_card",
            side_effect=lambda turns, **kw: fake_card,
        ),
    ):
        out = await deliver_orion_chat_wake(
            thread=_ORION_HOUSE,
            turn="10,11",
            from_agent="maestro",
            conversation_url=endpoint,
            pipeline_options=opts,
            delivery_owner=owner,
        )
    assert out.get("delivered") is True, out
    assert len(paste_calls) == 1
    assert paste_calls[0].count(CARD_BEGIN) == 1
    assert "todo:batch-a" in paste_calls[0]
    assert "turns 10–11" in paste_calls[0]


def test_ac3_lookup_failure_returns_pointer_and_logs(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("ORION_EVIDENCE_CARD", "1")
    turns = [{"thread": "1", "turn_number": 1, "subject": "todo:x", "body": ""}]

    with (
        patch(
            "web_chat_relay.orion_evidence_card.build_evidence_card",
            side_effect=RuntimeError("cortex down"),
        ),
        caplog.at_level(logging.WARNING),
    ):
        out = maybe_append_evidence_card(_GOLDEN_POINTER, turns)
    assert out == _GOLDEN_POINTER
    assert any("orion_evidence_card lookup failed" in r.message for r in caplog.records)


def test_ac3_timeout_returns_pointer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORION_EVIDENCE_CARD", "1")
    turns = [{"thread": "1", "turn_number": 1, "subject": "todo:slow", "body": ""}]

    def _slow(**kwargs: object) -> dict:
        raise TimeoutError("budget")

    with patch(
        "web_chat_relay.orion_evidence_card.build_evidence_card",
        side_effect=TimeoutError("budget"),
    ):
        assert maybe_append_evidence_card(_GOLDEN_POINTER, turns) == _GOLDEN_POINTER


def test_ac4_blocked_superseded_flagged_and_conflict() -> None:
    turns = [
        {
            "thread": "1",
            "turn_number": 1,
            "subject": "todo:blocked todo:ok",
            "body": "a:1 a:2 open a:3 closed",
        }
    ]
    ids = extract_reference_ids(turns)
    assert ("entity", "todo:blocked") in ids
    assert ("assertion", "a:1") in ids

    def _entity_get(**kwargs: object) -> dict:
        return {
            "items": [
                {
                    "id": "todo:ok",
                    "summary_row": "Ok entity",
                    "name": "ok",
                    "top_k_assertions": [
                        {
                            "id": 9,
                            "claim": "x",
                            "confidence": "confirmed",
                            "epistemic_state": "flagged",
                        }
                    ],
                    "assertion_counts": {"active": 1, "superseded": 2},
                }
            ],
            "count": 1,
        }

    def _assertion_get(**kwargs: object) -> dict:
        aid = int(kwargs["assertion_id"])
        if aid == 1:
            return {
                "id": 1,
                "entity_id": "todo:ok",
                "claim": "Feature is open",
                "confidence": "confirmed",
                "valid_from": "2026-01-01",
                "superseded_by": 99,
                "review_status": None,
            }
        if aid == 2:
            return {
                "id": 2,
                "entity_id": "todo:ok",
                "claim": "Feature is closed",
                "confidence": "confirmed",
                "valid_from": "2026-01-02",
                "superseded_by": None,
                "review_status": "flagged",
            }
        return {
            "id": 3,
            "entity_id": "todo:ok",
            "claim": "Unrelated",
            "confidence": "provisional",
            "valid_from": "2026-01-03",
            "superseded_by": None,
            "review_status": "staged",
        }

    card = build_evidence_card(
        turns,
        entity_get_fn=_entity_get,
        assertion_get_fn=_assertion_get,
        blocked_ids={"todo:blocked", "a:3"},
    )
    assert card is not None
    assert "todo:blocked" not in card
    assert "superseded" in card
    assert "flagged" in card
    assert "conflict:" in card
    assert "a:1" in card and "a:2" in card


def test_ac5_secret_path_never_in_card() -> None:
    turns = [
        {
            "thread": "1",
            "turn_number": 1,
            "subject": "todo:safe",
            "body": "path /home/io/.ssh/id_rsa and password=sekrit",
        }
    ]

    def _entity_get(**kwargs: object) -> dict:
        return {
            "items": [
                {
                    "id": "todo:safe",
                    "summary_row": "password=embedded api_key=ABCDEF",
                    "name": "safe",
                    "top_k_assertions": [],
                    "assertion_counts": {"active": 0, "superseded": 0},
                }
            ],
            "count": 1,
        }

    card = build_evidence_card(
        turns,
        entity_get_fn=_entity_get,
        assertion_get_fn=lambda **_: {"error": "unused"},
        blocked_ids=set(),
    )
    assert card is None or "password=" not in card
    assert card is None or "/home/io/.ssh" not in card


@pytest.mark.asyncio
async def test_deliver_orion_wake_unchanged_without_paste_text(db: None) -> None:
    """Direct deliver_orion_wake path ignores evidence flag (orion_chat only hook)."""
    pasted: list[str] = []

    class _Page:
        url = PINNED_CONVERSATION_URL

        async def evaluate(self, expression: str) -> object:
            if "stop" in expression:
                return False
            if "dialogs" in expression:
                return {"dialogs": []}
            return {"present": False, "buttons": []}

        async def keyboard(self) -> object:
            return type("K", (), {"press": AsyncMock(), "insert_text": AsyncMock()})()

        def locator(self, selector: str) -> object:
            return type(
                "L",
                (),
                {
                    "last": property(lambda _s: _s),
                    "first": property(lambda _s: _s),
                    "count": AsyncMock(return_value=1),
                    "is_visible": AsyncMock(return_value=True),
                    "click": AsyncMock(),
                    "nth": lambda _s, _i: _s,
                },
            )()

        async def wait_for_timeout(self, ms: int) -> None:
            return None

    page = _Page()

    async def _paste(_page: object, text: str, **kwargs: object) -> None:
        pasted.append(text)

    with patch(
        "web_chat_relay.orion_wake._paste_and_send", new=AsyncMock(side_effect=_paste)
    ):
        out = await deliver_orion_wake(
            thread=_ORION_HOUSE,
            turn=74,
            from_agent="maestro",
            page=page,
        )
    assert out.get("delivered") is True
    assert pasted == [_GOLDEN_POINTER]
