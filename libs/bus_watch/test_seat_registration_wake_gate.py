"""K63 seat-registration wake gate — parse, scan, holder match, alert ordering."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from agent_bus_store.db import create_thread, init_db, insert_turn
from agent_bus_store.db.connection import connect
from agent_bus_store.db.endpoint_wake_ledger import (
    advance_scan,
    claim_due_wakes,
    get_cursor,
    record_wake_result,
)

from bus_watch.endpoint_wake_dispatcher import (
    fire_due_wakes,
    holder_matches_seat_registration,
    load_latest_seat_registration_stamp,
    parse_seat_registration_stamp,
    post_wake_registration_refused_alerts,
)

# Verbatim bodies from agent-bus 12286#4482 and 12286#4490 (2026-10-22).
_FIXTURE_4482_BODY = """\
TYPE: SEAT_REGISTRATION
lane: 12286 · LEG 475 (successor to seat 77e9e044, reg 55cc1a21ef104a06b98c92b1bb94918d)
birth: successor_birth_id 580897db041a4f7c99cf7f56f36a5b8d · execution_id 43a0e794-c7ce-46cd-af56-0401ec7c8214 · desired_model cdp/opus-5.5-extra · registration_id/chat_url: from this send's response, not resolve_attended
skills: cdp-operator-proxy, lean-dispatch, reasoning-posture, hypothesize-simulate loaded
"""

_FIXTURE_4490_BODY = """\
TYPE: SEAT_REGISTRATION
registration_id: 48e59d388f634e9f9dd991f02040d4d0
chat_url: https://claude.ai/cowork/cse_01AZzTx44N9V7d413GmnyBME
successor_birth_id: 580897db041a4f7c99cf7f56f36a5b8d
execution_id: 43a0e794-c7ce-46cd-af56-0401ec7c8214
purpose: re-stamp of 12286#4482 for the K63 wake gate
"""


@pytest.fixture()
def db(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    monkeypatch.delenv("AGENT_BUS_ENDPOINT_WAKE_DISPATCHER", raising=False)
    init_db()


def test_parse_seat_registration_fixture_4482_unparseable() -> None:
    """12286#4482 buried registration in prose — gate must not treat it as a stamp."""
    assert parse_seat_registration_stamp(_FIXTURE_4482_BODY) is None


def test_parse_seat_registration_fixture_4490_parseable() -> None:
    """12286#4490 stopgap stamp — line-start registration_id and chat_url."""
    parsed = parse_seat_registration_stamp(_FIXTURE_4490_BODY)
    assert parsed == (
        "48e59d388f634e9f9dd991f02040d4d0",
        "https://claude.ai/cowork/cse_01AZzTx44N9V7d413GmnyBME",
    )


def test_holder_matches_chat_url_via_holder_id_not_exact_string() -> None:
    stamp = (
        "48e59d388f634e9f9dd991f02040d4d0",
        "https://claude.ai/cowork/cse_01AZzTx44N9V7d413GmnyBME",
    )
    holder = {
        "registration_id": "48e59d388f634e9f9dd991f02040d4d0",
        "chat_url": "https://claude.ai/cowork/cse_01AZzTx44N9V7d413GmnyBME/",
    }
    assert holder_matches_seat_registration(holder, stamp)


def test_load_latest_seat_registration_scans_beyond_last_100_window() -> None:
    """Stamp older than the newest 100 turns must still win by turn_number."""
    stamp_turn = 4490
    stamp_body = _FIXTURE_4490_BODY
    filler = [
        {"turn_number": n, "body": f"TYPE: WAKE\nnoise turn {n}\n"}
        for n in range(stamp_turn + 1, stamp_turn + 151)
    ]
    pages: list[dict[str, object]] = [
        {"turn_number": stamp_turn, "body": stamp_body},
        *filler,
    ]

    class Store:
        def turns(self, thread: str, **params: object) -> dict[str, object]:
            assert thread == "12286"
            if params.get("after_turn") is not None:
                after = int(params["after_turn"])  # type: ignore[arg-type]
                last = int(params.get("last") or 500)
                window = [r for r in pages if int(r["turn_number"]) > after]
                window.sort(key=lambda r: int(r["turn_number"]))
                truncated = len(window) > last
                return {
                    "turns": window[:last],
                    "truncated": truncated,
                }
            last = int(params.get("last") or 100)
            tip = sorted(pages, key=lambda r: int(r["turn_number"]), reverse=True)
            return {"turns": tip[:last]}

    store = Store()
    loaded = load_latest_seat_registration_stamp(store)  # type: ignore[arg-type]
    assert loaded == parse_seat_registration_stamp(stamp_body)
    tip_rows = store.turns("12286", last=100)["turns"]
    assert not any(
        parse_seat_registration_stamp(str(r["body"])) is not None for r in tip_rows
    )


def test_post_wake_registration_refused_alerts_logs_when_create_turn_missing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class Store:
        pass

    with caplog.at_level("WARNING"):
        post_wake_registration_refused_alerts(
            Store(),  # type: ignore[arg-type]
            rejected_registration_id="r1",
            rejected_chat_url="https://example.invalid",
            stamp_registration_id="r2",
            stamp_chat_url="https://example.invalid/2",
        )
    assert any("create_turn unavailable" in rec.message for rec in caplog.records)


def _maestro_wake_claimed(db: None) -> tuple[dict[str, Any], str, int]:
    create_thread(thread_id=12286, slug="maestro-lane-order")
    detail = create_thread(thread_id=None, slug="maestro-cse-order")
    assert detail is not None
    bus_thread = str(detail["id"])
    turn_id, _ts, number, _c = insert_turn(
        thread=bus_thread,
        from_agent="cursor",
        to_agent="maestro",
        subject="CONSULT|x",
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
                "agent": "maestro",
                "thread_id": bus_thread,
                "turn_id": turn_id,
                "turn_number": number,
            }
        ],
        now="2020-01-01 00:00:00",
    )
    claimed = claim_due_wakes(now="2020-01-01 00:00:01")
    return claimed, bus_thread, number


def test_fire_maestro_mismatch_records_result_before_alert_failure(db: None) -> None:
    """Alert failure after store.result must not leave the wake undelivered for retry."""
    claimed, _bus_thread, _number = _maestro_wake_claimed(db)
    wake_id = str(claimed["wake_id"])
    result_calls: list[dict[str, object]] = []
    alert_calls: list[object] = []

    class Store:
        def claim(self) -> dict:
            return claimed

        def holder(self, lane_thread_id: str) -> dict:
            return {
                "chat_url": "https://claude.ai/chat/stale",
                "registration_id": "reg-stale",
            }

        def subscription(self, agent: str, thread_id: str):
            return (
                "wildcard",
                {
                    "id": 9,
                    "surface": "cse",
                    "display": "cse",
                    "agent_id": "12286",
                    "title": None,
                },
            )

        def turns(self, thread: str, **params: object) -> dict:
            return {
                "turns": [
                    {
                        "turn_number": 4490,
                        "body": _FIXTURE_4490_BODY,
                    }
                ]
            }

        def create_turn(self, **kwargs: object) -> dict:
            alert_calls.append(kwargs)
            raise RuntimeError("store down")

        def result(self, **payload: object) -> int:
            result_calls.append(payload)
            return record_wake_result(
                str(payload["wake_id"]),
                str(payload["outcome"]),
                str(payload["reason"]) if payload["reason"] is not None else None,
                str(payload["route"]) if payload["route"] is not None else None,
                payload["subscription_id"],  # type: ignore[arg-type]
                payload["cse_registration_id"],  # type: ignore[arg-type]
            )

    store = Store()
    fire_due_wakes(store)  # type: ignore[arg-type]
    assert len(result_calls) == 1
    assert result_calls[0]["outcome"] == "skipped"
    assert result_calls[0]["reason"] == "seat_registration_mismatch"
    assert len(alert_calls) == 2


def test_fire_maestro_delivery_records_result_before_alert_failure(db: None) -> None:
    """Successful delivery with a failing alert must not skip store.result."""
    claimed, bus_thread, number = _maestro_wake_claimed(db)
    result_calls: list[dict[str, object]] = []
    alert_calls: list[object] = []
    holder = {
        "chat_url": "https://claude.ai/cowork/cse_01AZzTx44N9V7d413GmnyBME",
        "registration_id": "48e59d388f634e9f9dd991f02040d4d0",
    }

    class Store:
        def claim(self) -> dict:
            return claimed

        def holder(self, lane_thread_id: str) -> dict:
            return holder

        def subscription(self, agent: str, thread_id: str):
            return (
                "wildcard",
                {
                    "id": 9,
                    "surface": "cse",
                    "display": "cse",
                    "agent_id": "12286",
                    "title": None,
                },
            )

        def turns(self, thread: str, **params: object) -> dict:
            return {
                "turns": [
                    {
                        "turn_number": 4490,
                        "body": _FIXTURE_4490_BODY,
                    }
                ]
            }

        def create_turn(self, **kwargs: object) -> dict:
            alert_calls.append(kwargs)
            raise RuntimeError("store down")

        def result(self, **payload: object) -> int:
            result_calls.append(payload)
            return record_wake_result(
                str(payload["wake_id"]),
                str(payload["outcome"]),
                str(payload["reason"]) if payload["reason"] is not None else None,
                str(payload["route"]) if payload["route"] is not None else None,
                payload["subscription_id"],  # type: ignore[arg-type]
                payload["cse_registration_id"],  # type: ignore[arg-type]
            )

    def _split_authority(*args: object, **kwargs: object) -> dict:
        dry = kwargs.get("dry_run")
        if dry:
            return {
                "ok": True,
                "dry_run": True,
                "registration_id": holder["registration_id"],
                "url": holder["chat_url"],
            }
        return {
            "ok": True,
            "dry_run": False,
            "registration_id": "other-reg",
            "url": holder["chat_url"],
        }

    store = Store()
    with patch(
        "bus_watch.cse_followup.fire_cse_followup", side_effect=_split_authority
    ):
        fire_due_wakes(store)  # type: ignore[arg-type]
    assert len(result_calls) == 1
    assert result_calls[0]["outcome"] == "delivered"
    assert len(alert_calls) == 2
