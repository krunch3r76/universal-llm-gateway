"""Row-9 flood probe: buried closeout must not double-post; oracle is store SQL."""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from agent_bus_store import create_app
from agent_bus_store.auth import require_token
from agent_bus_store.db import create_thread, init_db, insert_turn
from fastapi.testclient import TestClient
from transport_utils import DEFAULT_AGENT_BUS_URL

from services.git_integration_worker.cursor_auto.closeout_outbox import (
    CloseoutOutboxStore,
    get_outbox_store,
)
from services.git_integration_worker.cursor_auto.closeout_replay import (
    startup_closeout_outbox_replay,
)
from services.git_integration_worker.cursor_auto.job_ledger import (
    AutoJobLedger,
    get_ledger,
)
from services.git_integration_worker.cursor_auto.queue import (
    get_queue,
    reset_queue_for_tests,
)
from services.git_integration_worker.cursor_bus import BusReplyResult

# Tip window that previously hid buried closeouts.
_FLOOD_DECOYS = 250
_DISPATCH_ID = "auto-row9flood001"
_ORACLE_SQL = (
    "SELECT COUNT(*) AS n FROM turns "
    "WHERE thread = ? AND body LIKE ? AND body LIKE ?"
)


@pytest.fixture()
def isolated(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "giw"))
    bus_db = tmp_path / "bus.db"
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(bus_db))
    init_db()
    AutoJobLedger.reset_for_tests()
    CloseoutOutboxStore.reset_for_tests()
    reset_queue_for_tests(durable=True)
    app = create_app()
    app.dependency_overrides[require_token] = lambda: None
    client = TestClient(app)
    yield client, bus_db
    client.close()
    app.dependency_overrides.clear()
    AutoJobLedger.reset_for_tests()
    CloseoutOutboxStore.reset_for_tests()


class _App:
    def __init__(self, worker_id: str) -> None:
        self.state = type("S", (), {"worker_id": worker_id, "worker_boot_ts": "t"})()


def _oracle_count(bus_db: Path, thread_id: str, dispatch_id: str) -> tuple[str, list, int]:
    """Direct-store duplicate count — never GET /turns."""
    params = (
        thread_id,
        "%TYPE: CLOSEOUT%",
        f"%dispatch_id: {dispatch_id}%",
    )
    with sqlite3.connect(bus_db) as conn:
        row = conn.execute(_ORACLE_SQL, params).fetchone()
    n = int(row[0]) if row else -1
    return _ORACLE_SQL, list(params), n


def test_flood_probe_buried_closeout_no_double_post_direct_store(isolated) -> None:
    """PASS: exactly one closeout turn for dispatch_id after replay under flood."""
    bus_client, bus_db = isolated
    thread_row = create_thread(thread_id=None, slug="row9-flood", tags=[])
    assert thread_row is not None
    thread_id = thread_row["id"]

    insert_turn(
        thread=thread_id,
        from_agent="web-anthropic",
        to_agent="cursor",
        subject="TYPE: DIRECTIVE — row9 flood",
        body="TYPE: DIRECTIVE\n",
        status="open",
    )
    request_turn = 1
    closeout_body = (
        "TYPE: CLOSEOUT\n"
        "status: complete\n"
        f"dispatch_id: {_DISPATCH_ID}\n"
        "model: auto\n"
        "request_turn: 1\n"
        "checkpoint: nothing_authored\n"
    )
    insert_turn(
        thread=thread_id,
        from_agent="cursor-auto",
        to_agent="web-anthropic",
        subject="status:done — row9 flood closeout",
        body=closeout_body,
        status="open",
    )
    for i in range(_FLOOD_DECOYS):
        insert_turn(
            thread=thread_id,
            from_agent="cursor",
            to_agent="web-anthropic",
            subject=f"decoy-{i}",
            body=f"decoy body {i}",
            status="open",
        )

    query, params, before = _oracle_count(bus_db, thread_id, _DISPATCH_ID)
    assert before == 1, (
        f"pre-replay oracle expected 1; query={query!r} params={params!r} n={before}"
    )

    job = get_queue().enqueue(
        thread_id=thread_id,
        turn_number=request_turn,
        subject="relay flood",
        body="TYPE: DIRECTIVE\n",
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="auto",
        desired_effort="medium",
        contract="implement",
    )
    get_ledger().bind_dispatch(job.job_id, dispatch_id=_DISPATCH_ID)
    store = get_outbox_store()
    store.persist_pending(
        dispatch_id=_DISPATCH_ID,
        job_id=job.job_id,
        thread_id=thread_id,
        to_agent="web-anthropic",
        from_agent="cursor-auto",
        subject="status:done — row9 flood closeout",
        envelope_body=closeout_body,
        closeout_status="complete",
        request_turn=request_turn,
        worker_id="old-worker",
        worker_started_at="2026-08-06T00:00:00+00:00",
        checkpoint_value="nothing_authored",
        tree_residue=0,
    )
    # Post-success → marker-death shape: outbox still pending/replayable while
    # the closeout already exists on the bus (and is buried past tip=200).
    assert store.get(_DISPATCH_ID) is not None
    assert store.get(_DISPATCH_ID).state == "pending"

    mock_reply = AsyncMock(
        return_value=BusReplyResult(status_code=201, body={"turn_number": 999})
    )

    class _AsgiClient:
        """Route scan GETs through the in-process bus app (honors after_turn)."""

        def __init__(self, client: TestClient) -> None:
            self._client = client

        async def get(self, path: str, *, params=None, headers=None):
            resp = self._client.get(path, params=params or {})
            return resp

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    with (
        patch(
            "services.git_integration_worker.cursor_auto.closeout_bus_scan.make_async_client",
            return_value=_AsgiClient(bus_client),
        ),
        patch(
            "services.git_integration_worker.cursor_auto.closeout_bus_scan.DEFAULT_AGENT_BUS_URL",
            DEFAULT_AGENT_BUS_URL,
        ),
        patch(
            "services.git_integration_worker.cursor_auto.closeout_replay.CursorBusClient",
        ) as bus_cls,
    ):
        bus_cls.return_value.reply = mock_reply
        asyncio.run(startup_closeout_outbox_replay(_App("new-worker")))

    mock_reply.assert_not_called()
    row = store.get(_DISPATCH_ID)
    assert row is not None
    assert row.state == "posted_confirmed"

    query, params, after = _oracle_count(bus_db, thread_id, _DISPATCH_ID)
    # Quantifier on the record — empty included.
    print("FLOOD_ORACLE_QUERY:", query)
    print("FLOOD_ORACLE_PARAMS:", params)
    print("FLOOD_ORACLE_RAW_N:", after)
    assert after == 1, (
        f"flood probe FAIL: duplicate closeout; query={query!r} "
        f"params={params!r} n={after}"
    )
