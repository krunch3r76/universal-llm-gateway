"""AC5–AC6: cursor-sdk heartbeat / progress events."""

from __future__ import annotations

import time

import pytest

from services.git_integration_worker.cursor_dispatch_ledger import (
    CursorDispatchLedger,
    _connect,
)
from services.git_integration_worker.cursor_sdk_events import (
    register_cursor_sdk_event_publisher,
)
from services.git_integration_worker.routes import cursor_sdk as route_mod


@pytest.fixture(autouse=True)
def _isolated_ledger(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    CursorDispatchLedger._instance = None
    yield
    CursorDispatchLedger._instance = None


def test_heartbeat_emits_and_bumps(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC5: heartbeat emits progress and bumps ledger last_heartbeat_at."""
    emitted: list[dict[str, object]] = []

    def _capture(signal: str, payload: dict[str, object]) -> None:
        if signal == "frontier.sdk.worker.progress":
            emitted.append(payload)

    register_cursor_sdk_event_publisher(_capture)
    monkeypatch.setattr(route_mod, "_SDK_HEARTBEAT_S", 0.05)

    from services.git_integration_worker.models.cursor_api import (
        CursorDispatchRequest,
        CursorDispatchResponse,
    )

    ledger = CursorDispatchLedger.instance()
    real_req = CursorDispatchRequest(
        thread_id="900",
        model="cursor/composer-2.5",
        dispatch_id="hb-1",
        execution_id="exec-hb-1",
        message="x",
    )
    ledger.admit(
        req=real_req,
        fingerprint=ledger.fingerprint(real_req),
        execution_id=real_req.execution_id,
        caller_agent=None,
        resolved_model="composer-2.5",
        admission=CursorDispatchResponse(
            admitted=True,
            dispatch_id="hb-1",
            thread_id="900",
            model_id="composer-2.5",
        ),
    )

    hb_thread, hb_stop = route_mod._start_heartbeat(
        dispatch_id="hb-1",
        thread_id="900",
        resolved_model="cursor/composer-2.5",
    )
    time.sleep(0.2)
    hb_stop.set()
    hb_thread.join(timeout=2.0)

    assert len(emitted) >= 2
    for payload in emitted:
        assert payload["resolved_model"] == "cursor/composer-2.5"
        assert "model_entity_id" not in payload

    with _connect() as conn:
        row = conn.execute(
            "SELECT last_heartbeat_at FROM cursor_sdk_dispatches WHERE dispatch_id = ?",
            ("hb-1",),
        ).fetchone()
    assert row["last_heartbeat_at"] is not None


def test_heartbeat_stops(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC6: stop flag halts further emissions and thread exits."""
    emitted: list[str] = []

    def _capture(signal: str, _payload: dict[str, object]) -> None:
        emitted.append(signal)

    register_cursor_sdk_event_publisher(_capture)
    monkeypatch.setattr(route_mod, "_SDK_HEARTBEAT_S", 0.05)

    hb_thread, hb_stop = route_mod._start_heartbeat(
        dispatch_id="hb-2",
        thread_id="901",
        resolved_model="cursor/composer-2.5",
    )
    time.sleep(0.12)
    count_before_stop = len([s for s in emitted if s == "frontier.sdk.worker.progress"])
    hb_stop.set()
    hb_thread.join(timeout=2.0)
    time.sleep(0.15)

    count_after_stop = len([s for s in emitted if s == "frontier.sdk.worker.progress"])
    assert count_after_stop == count_before_stop
    assert not hb_thread.is_alive()
