"""in_sync, drift, pending, and transport backoff."""

from __future__ import annotations

import pytest

from .await_verify import run_await_verify

pytestmark = pytest.mark.offline


def _clock():
    state = {"t": 0.0}

    def now() -> float:
        return state["t"]

    async def sleep(seconds: float) -> None:
        state["t"] += seconds

    return now, sleep


@pytest.mark.asyncio
async def test_outcomes() -> None:
    now, sleep = _clock()

    async def silent(path, params=None):
        return {"turns": []}, 200

    pending = await run_await_verify(
        {"n": 1, "house": "house", "verify_lane": "v", "verifier": "verifier", "wait_verify_seconds": 3},
        {"turn": 1},
        get=silent,
        sleep=sleep,
        now=now,
    )
    assert pending["state"] == "pending"

    now, sleep = _clock()

    async def insync(path, params=None):
        return {
            "turns": [
                {"from": "verifier", "body": "IN-SYNC|house|1", "turn_number": 8}
            ]
        }, 200

    synced = await run_await_verify(
        {"n": 1, "house": "house", "verify_lane": "v", "verifier": "verifier", "wait_verify_seconds": 30},
        {"turn": 1},
        get=insync,
        sleep=sleep,
        now=now,
    )
    assert synced["state"] == "in_sync"

    async def drift(path, params=None):
        return {
            "turns": [{"from": "verifier", "body": "DRIFT|house|1", "turn_number": 9}]
        }, 200

    drifted = await run_await_verify(
        {"n": 1, "house": "house", "verify_lane": "v", "verifier": "verifier", "wait_verify_seconds": 30},
        {"turn": 1},
        get=drift,
        sleep=sleep,
        now=now,
    )
    assert drifted["state"] == "drift"


@pytest.mark.asyncio
async def test_transport_backoff() -> None:
    now, sleep = _clock()
    calls = {"n": 0}

    async def flaky(path, params=None):
        calls["n"] += 1
        if calls["n"] < 3:
            raise ConnectionError("restart")
        return {
            "turns": [
                {"from": "verifier", "body": "IN-SYNC|house|2", "turn_number": 3}
            ]
        }, 200

    out = await run_await_verify(
        {"n": 2, "house": "house", "verify_lane": "v", "verifier": "verifier", "wait_verify_seconds": 60},
        {"turn": 1},
        get=flaky,
        sleep=sleep,
        now=now,
    )
    assert out["state"] == "in_sync"
    assert calls["n"] == 3
