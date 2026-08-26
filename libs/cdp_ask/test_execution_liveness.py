"""Tests for poll-plane Cowork window liveness projection."""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from cdp_ask.app import create_app
from cdp_ask.execution_store import ExecutionStore
from cdp_ask.page_liveness import (
    SUSTAINED_IDLE_SAMPLES,
    LadderAdvanceState,
    LadderCallbacks,
    advance_ladder_from_harvest,
)

pytestmark = pytest.mark.offline


@pytest.fixture
def store() -> ExecutionStore:
    return ExecutionStore()


@pytest.mark.asyncio
async def test_update_liveness_stores_harvest_sample(store: ExecutionStore) -> None:
    record = await store.create(holder="test", purpose="ask")
    await store.update_liveness(
        record.execution_id,
        streaming=True,
        stop=False,
        tool_pause=True,
        liveness_observed_at=1234.5,
    )
    refreshed = await store.get(record.execution_id)
    assert refreshed is not None
    assert refreshed.streaming is True
    assert refreshed.stop is False
    assert refreshed.tool_pause is True
    assert refreshed.liveness_observed_at == 1234.5


@pytest.mark.asyncio
async def test_mark_terminal_clears_liveness(store: ExecutionStore) -> None:
    record = await store.create(holder="test", purpose="ask")
    await store.update_liveness(
        record.execution_id,
        streaming=True,
        stop=False,
        tool_pause=False,
        liveness_observed_at=999.0,
    )
    await store.mark_terminal(record.execution_id, status="failed", error="boom")
    refreshed = await store.get(record.execution_id)
    assert refreshed is not None
    assert refreshed.streaming is None
    assert refreshed.stop is None
    assert refreshed.tool_pause is None
    assert refreshed.liveness_observed_at is None


@pytest.mark.asyncio
async def test_poll_execution_retains_liveness_past_turn_idle(
    store: ExecutionStore,
) -> None:
    """Post-idle liveness stays visible — it is the only post-idle progress signal.

    Nulling it at ``turn_idle`` froze the poller's progress fingerprint for the
    whole harvest-resolution window, so a healthy long harvest was
    indistinguishable from a hang (friction a:26175).
    """
    record = await store.create(holder="test", purpose="ask")
    sleeper = asyncio.create_task(asyncio.sleep(3600))
    await store.attach_task(record.execution_id, sleeper)

    async def _on_liveness(
        streaming: bool,
        stop: bool,
        tool_pause: bool,
        observed_at: float,
    ) -> None:
        await store.update_liveness(
            record.execution_id,
            streaming=streaming,
            stop=stop,
            tool_pause=tool_pause,
            liveness_observed_at=observed_at,
        )

    async def _on_turn_idle() -> None:
        await store.update_ladder(
            record.execution_id,
            completion_phase="turn_idle",
            turn_idle_at=time.time(),
        )

    progress = LadderAdvanceState()
    callbacks = LadderCallbacks(on_liveness=_on_liveness, on_turn_idle=_on_turn_idle)

    client = TestClient(create_app(store=store))
    live = client.get(f"/v1/project-ask/executions/{record.execution_id}").json()
    assert live["status"] == "running"
    assert live["completion_phase"] == "running"

    await advance_ladder_from_harvest(
        {
            "streaming": True,
            "stop": False,
            "tool_pause": False,
            "body_len": 10,
        },
        callbacks=callbacks,
        progress=progress,
    )
    seeded = client.get(f"/v1/project-ask/executions/{record.execution_id}").json()
    assert seeded["streaming"] is True
    assert seeded["liveness_observed_at"] is not None

    for _ in range(SUSTAINED_IDLE_SAMPLES):
        await advance_ladder_from_harvest(
            {
                "streaming": False,
                "stop": False,
                "tool_pause": False,
                "body_len": 20,
            },
            callbacks=callbacks,
            progress=progress,
        )

    assert progress.turn_idle_sent
    idle = client.get(f"/v1/project-ask/executions/{record.execution_id}").json()
    assert idle["completion_phase"] == "turn_idle"
    assert idle["liveness_observed_at"] is not None
    assert idle["streaming"] is False
    assert idle["stop"] is False
    assert idle["tool_pause"] is False

    await store.mark_terminal(record.execution_id, status="completed")
    done = client.get(f"/v1/project-ask/executions/{record.execution_id}").json()
    assert done["streaming"] is None
    assert done["liveness_observed_at"] is None

    sleeper.cancel()
    with pytest.raises(asyncio.CancelledError):
        await sleeper


@pytest.mark.asyncio
async def test_abort_retain_idle_running_execution_terminalizes(
    store: ExecutionStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def _fake_abort_cleanup(
        reg: object, *, purpose: str | None, retain_idle: bool = False
    ) -> str:
        captured["retain_idle"] = retain_idle
        captured["purpose"] = purpose
        return "attested_stopped_and_deregistered"

    monkeypatch.setattr(
        "claude_bundles.project_ask_abort.abort_cleanup",
        _fake_abort_cleanup,
    )
    monkeypatch.setattr(
        "claude_bundles.project_ask_abort.lookup_active_registration",
        lambda rid: SimpleNamespace(
            registration_id=rid,
            port=9234,
            purpose="operator-proxy",
            cdp_url="http://127.0.0.1:9234",
        ),
    )

    record = await store.create(holder="test", purpose="operator-proxy")
    await store.set_registration_id(record.execution_id, "reg-test")
    sleeper = asyncio.create_task(asyncio.sleep(3600))
    await store.attach_task(record.execution_id, sleeper)
    await store.update_liveness(
        record.execution_id,
        streaming=False,
        stop=True,
        tool_pause=False,
        liveness_observed_at=time.time(),
    )

    client = TestClient(create_app(store=store))
    resp = client.post(f"/v1/project-ask/executions/{record.execution_id}/abort")
    assert resp.status_code == 200
    body = resp.json()
    assert body["aborted"] is True
    assert body["status"] == "aborted"
    assert body["abort_outcome"] == "attested_stopped_and_deregistered"
    assert captured["retain_idle"] is True
    assert captured["purpose"] == "operator-proxy"

    refreshed = await store.get(record.execution_id)
    assert refreshed is not None
    assert refreshed.status == "aborted"

    sleeper.cancel()
    with pytest.raises(asyncio.CancelledError):
        await sleeper


@pytest.mark.asyncio
async def test_poll_execution_url_from_registry_when_unbound(
    store: ExecutionStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC6 — running poll resolves chat_url from registry when payload lacks url."""
    cse = "https://claude.ai/cowork/cse_poll"
    monkeypatch.setattr(
        "claude_bundles.cdp_registry.chat_url_for_registration",
        lambda rid: cse if rid == "reg-cse" else None,
    )
    record = await store.create(holder="test", purpose="ask")
    await store.set_registration_id(record.execution_id, "reg-cse")
    client = TestClient(create_app(store=store))
    live = client.get(f"/v1/project-ask/executions/{record.execution_id}").json()
    assert live["url"] == cse


@pytest.mark.asyncio
async def test_poll_execution_terminal_payload_url_wins(
    store: ExecutionStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC6 — terminal payload url wins over registry fallback."""
    terminal_url = "https://claude.ai/cowork/cse_terminal"
    monkeypatch.setattr(
        "claude_bundles.cdp_registry.chat_url_for_registration",
        lambda _rid: "https://claude.ai/cowork/cse_registry",
    )
    record = await store.create(holder="test", purpose="ask")
    await store.set_registration_id(record.execution_id, "reg-cse")
    await store.mark_terminal(
        record.execution_id,
        status="failed",
        error="boom",
        result={"url": terminal_url},
    )
    client = TestClient(create_app(store=store))
    done = client.get(f"/v1/project-ask/executions/{record.execution_id}").json()
    assert done["url"] == terminal_url


@pytest.mark.asyncio
async def test_poll_execution_url_none_without_registry(
    store: ExecutionStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = await store.create(holder="test", purpose="ask")
    monkeypatch.setattr(
        "claude_bundles.cdp_registry.chat_url_for_registration",
        lambda _rid: None,
    )
    client = TestClient(create_app(store=store))
    live = client.get(f"/v1/project-ask/executions/{record.execution_id}").json()
    assert live.get("url") is None
