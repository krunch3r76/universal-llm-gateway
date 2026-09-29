"""A peer that dies during resume replay must leave the fan-out set."""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocket

from event_store.subscribe import create_subscribe_router


class _ReplayStore:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows
        self._paged = False

    async def query(
        self,
        sql: str,
        params: tuple[Any, ...] = (),
        *,
        limit: int = 1000,
        raise_on_error: bool = False,
    ) -> list[dict[str, Any]]:
        del sql, params, limit, raise_on_error
        if self._paged:
            return []
        self._paged = True
        return list(self._rows)

    def get_realtime_snapshot(self, limit: int = 1000) -> list[dict[str, Any]]:
        del limit
        return []


def _wait_empty(queues: set[Any], timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not queues:
            return True
        time.sleep(0.02)
    return not queues


@pytest.mark.offline
def test_live_subscribe_then_close_drops_the_queue() -> None:
    queues: set[Any] = set()
    app = FastAPI()
    app.include_router(
        create_subscribe_router(_ReplayStore([]), queues)  # type: ignore[arg-type]
    )

    with TestClient(app) as client, client.websocket_connect("/v1/subscribe") as ws:
        ws.send_json({"type": "subscribe", "filter": {"role": "coordination"}})
        ack = ws.receive_json()
        assert ack["type"] == "subscribed"
        assert ack["filter"] == {"role": "coordination"}
        assert len(queues) == 1

    assert _wait_empty(queues)


@pytest.mark.offline
def test_peer_close_during_replay_send_drops_the_queue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pre-push send must not pin the queue after the peer is gone.

    Resume calls ``send_json`` before ``_push_loop`` exists. A send that waits
    forever used to skip ``finally``. Closing the client has to remove the queue.
    """
    entered = threading.Event()
    original = WebSocket.send_json

    async def hang_replay_row(self: WebSocket, data: Any, mode: str = "text") -> None:
        if isinstance(data, dict) and data.get("seq") == 5:
            entered.set()
            await asyncio.Future()
        await original(self, data, mode)

    monkeypatch.setattr(WebSocket, "send_json", hang_replay_row)

    queues: set[Any] = set()
    store = _ReplayStore([{"seq": 5, "signal": "demo", "role": "coordination"}])
    app = FastAPI()
    app.include_router(create_subscribe_router(store, queues))  # type: ignore[arg-type]

    with TestClient(app) as client, client.websocket_connect("/v1/subscribe") as ws:
        ws.send_json(
            {
                "type": "subscribe",
                "filter": {"role": "coordination"},
                "resume_from": {"seq": 1},
            }
        )
        assert entered.wait(5)
        assert len(queues) == 1

    assert _wait_empty(queues)
