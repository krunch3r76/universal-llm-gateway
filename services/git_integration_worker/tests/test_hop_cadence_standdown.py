"""Stand-down ACK inhibit gate for hop-cadence evaluate and scan_and_fire."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

from services.git_integration_worker.cursor_auto.hop_cadence_standdown import (
    STANDDOWN_ACK_OPEN_REASON,
    lane_standdown_ack_open,
)
from services.git_integration_worker.cursor_auto.hop_cadence_watch import (
    evaluate_watch,
    save_watches,
)

pytestmark = pytest.mark.offline

_NOW = 1_700_000_000.0
_THREAD = "7323"


def _due_row() -> dict:
    return {
        "thread_id": _THREAD,
        "seated_at": _NOW - 5000.0,
        "from_agent": "web-anthropic",
    }


def test_evaluate_watch_inhibits_when_standdown_probe_true() -> None:
    decision = evaluate_watch(
        _due_row(),
        now=_NOW,
        threshold=1500.0,
        cool=1800.0,
        standdown_probe=lambda _tid: True,
    )
    assert decision.action == "skip"
    assert decision.reason == STANDDOWN_ACK_OPEN_REASON
    assert decision.signal == STANDDOWN_ACK_OPEN_REASON


def test_evaluate_watch_fires_when_standdown_probe_false() -> None:
    decision = evaluate_watch(
        _due_row(),
        now=_NOW,
        threshold=1500.0,
        cool=1800.0,
        standdown_probe=lambda _tid: False,
    )
    assert decision.action == "fire"
    assert decision.reason == "age_threshold_met"


def test_evaluate_watch_fires_when_standdown_probe_absent() -> None:
    decision = evaluate_watch(
        _due_row(),
        now=_NOW,
        threshold=1500.0,
        cool=1800.0,
    )
    assert decision.action == "fire"
    assert decision.reason == "age_threshold_met"


def test_lane_standdown_ack_open_latest_stand_down() -> None:
    turns = [
        {"turn_number": 1, "body": "hello"},
        {"turn_number": 5, "body": "TYPE: SEAT_STAND_DOWN_ACK\n"},
    ]
    assert lane_standdown_ack_open(_THREAD, fetch_turns_fn=lambda _tid: turns)


def test_lane_standdown_ack_open_successor_consumes() -> None:
    turns = [
        {"turn_number": 1, "body": "TYPE: SEAT_STAND_DOWN_ACK\n"},
        {"turn_number": 2, "body": "TYPE: SUCCESSOR_ATTESTATION\n"},
    ]
    assert not lane_standdown_ack_open(_THREAD, fetch_turns_fn=lambda _tid: turns)


def test_lane_standdown_ack_open_no_marker() -> None:
    assert not lane_standdown_ack_open(_THREAD, fetch_turns_fn=lambda _tid: [])
    assert not lane_standdown_ack_open(
        _THREAD,
        fetch_turns_fn=lambda _tid: [{"turn_number": 1, "body": "ordinary"}],
    )


def test_lane_standdown_ack_open_fetch_none() -> None:
    assert not lane_standdown_ack_open(_THREAD, fetch_turns_fn=lambda _tid: None)


def test_lane_standdown_ack_open_malformed_fetch() -> None:
    assert not lane_standdown_ack_open(_THREAD, fetch_turns_fn=lambda _tid: "bad")
    assert not lane_standdown_ack_open(
        _THREAD,
        fetch_turns_fn=lambda _tid: [{"turn_number": 1}, "not-a-mapping"],
    )


def test_lane_standdown_ack_open_empty_thread_id() -> None:
    assert not lane_standdown_ack_open("")
    assert not lane_standdown_ack_open("   ")


def test_lane_standdown_ack_open_unbounded_history() -> None:
    turns = [{"turn_number": 1, "body": "TYPE: SEAT_STAND_DOWN_ACK\n"}]
    turns.extend(
        {"turn_number": i, "body": f"ordinary turn {i}"} for i in range(2, 203)
    )
    assert lane_standdown_ack_open(_THREAD, fetch_turns_fn=lambda _tid: turns)


def test_lane_standdown_ack_open_superseded_stand_down_not_inhibit() -> None:
    turns = [
        {"turn_number": 1, "body": "TYPE: SUCCESSOR_ATTESTATION\n"},
        {
            "turn_number": 2,
            "body": "TYPE: SEAT_STAND_DOWN_ACK\n",
            "status": "superseded",
        },
    ]
    assert not lane_standdown_ack_open(_THREAD, fetch_turns_fn=lambda _tid: turns)


def test_lane_standdown_ack_open_only_superseded_marker() -> None:
    turns = [
        {
            "turn_number": 1,
            "body": "TYPE: SEAT_STAND_DOWN_ACK\n",
            "status": "superseded",
        },
    ]
    assert not lane_standdown_ack_open(_THREAD, fetch_turns_fn=lambda _tid: turns)


def test_fetch_thread_turns_sync_transport_error_fail_open() -> None:
    from services.git_integration_worker.cursor_auto import hop_cadence_standdown as mod

    mock_client = MagicMock()
    mock_client.get.side_effect = httpx.HTTPError("transport down")
    mock_cm = MagicMock()
    mock_cm.__enter__.return_value = mock_client
    with patch(
        "services.git_integration_worker.cursor_auto.hop_cadence_standdown.make_sync_client",
        return_value=mock_cm,
    ):
        assert mod._fetch_thread_turns_sync(_THREAD) is None
        assert not lane_standdown_ack_open(_THREAD)


def test_fetch_thread_turns_sync_omits_last_param() -> None:
    from services.git_integration_worker.cursor_auto import hop_cadence_standdown as mod

    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"turns": []}
    mock_client.get.return_value = mock_resp
    mock_cm = MagicMock()
    mock_cm.__enter__.return_value = mock_client
    with patch(
        "services.git_integration_worker.cursor_auto.hop_cadence_standdown.make_sync_client",
        return_value=mock_cm,
    ):
        assert mod._fetch_thread_turns_sync(_THREAD) == []
    mock_client.get.assert_called_once()
    _, kwargs = mock_client.get.call_args
    assert kwargs["params"] == {"thread": _THREAD}
    assert "last" not in kwargs["params"]


@pytest.mark.asyncio
async def test_scan_and_fire_skips_open_standdown_ack(tmp_path: Path) -> None:
    from services.git_integration_worker.cursor_auto.hop_cadence import scan_and_fire
    from services.git_integration_worker.cursor_auto import queue as queue_mod

    isolated = tmp_path / "watches.json"
    save_watches({_THREAD: _due_row()}, isolated)
    q = queue_mod.reset_queue_for_tests(durable=False)
    with (
        patch(
            "services.git_integration_worker.cursor_auto.hop_cadence_watch.registry_started_at",
            return_value=None,
        ),
        patch(
            "services.git_integration_worker.cursor_auto.hop_cadence_watch.assess_standing_handoff",
            return_value=None,
        ),
        patch(
            "services.git_integration_worker.cursor_auto.hop_cadence_standdown._fetch_thread_turns_sync",
            return_value=[
                {
                    "turn_number": 10,
                    "body": "TYPE: SEAT_STAND_DOWN_ACK\n",
                }
            ],
        ),
    ):
        outcomes = await scan_and_fire(queue=q, path=isolated, now=_NOW)
    assert outcomes
    assert outcomes[0]["action"] == "skip"
    assert outcomes[0]["reason"] == STANDDOWN_ACK_OPEN_REASON


@pytest.mark.asyncio
async def test_scan_and_fire_fires_when_successor_consumes(tmp_path: Path) -> None:
    from services.git_integration_worker.cursor_auto.hop_cadence import scan_and_fire
    from services.git_integration_worker.cursor_auto import queue as queue_mod

    isolated = tmp_path / "watches.json"
    save_watches({_THREAD: _due_row()}, isolated)
    q = queue_mod.reset_queue_for_tests(durable=False)
    with (
        patch(
            "services.git_integration_worker.cursor_auto.hop_cadence_watch.registry_started_at",
            return_value=None,
        ),
        patch(
            "services.git_integration_worker.cursor_auto.hop_cadence_watch.assess_standing_handoff",
            return_value=None,
        ),
        patch(
            "services.git_integration_worker.cursor_auto.hop_cadence_standdown._fetch_thread_turns_sync",
            return_value=[
                {
                    "turn_number": 10,
                    "body": "TYPE: SUCCESSOR_ATTESTATION\n",
                }
            ],
        ),
        patch(
            "services.git_integration_worker.cursor_auto.hop_cadence.fire_hop_for_decision",
            return_value={"ok": True, "thread_id": _THREAD},
        ),
    ):
        outcomes = await scan_and_fire(queue=q, path=isolated, now=_NOW)
    assert outcomes
    assert outcomes[0].get("ok") is True
    assert outcomes[0].get("reason") != STANDDOWN_ACK_OPEN_REASON
