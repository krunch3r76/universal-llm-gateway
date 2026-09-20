"""Unit tests for CSE session holder CRUD and transitions."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from services.git_integration_worker.cse_session_holders import (
    boot_reconcile,
    ensure_schema,
    get_holder,
    transition_seat_state,
    upsert_holder,
)
from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger

_CSE_URL = "https://claude.ai/cowork/cse_testholder1"


@pytest.fixture()
def ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> CursorDispatchLedger:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    CursorDispatchLedger._instance = None
    return CursorDispatchLedger.instance()


def test_upsert_idempotent_same_chat_url(ledger: CursorDispatchLedger) -> None:
    with ledger._connect() as conn:
        ensure_schema(conn)
        first = upsert_holder(
            conn,
            chat_url=_CSE_URL,
            registration_id="reg-1",
            execution_id="exec-1",
        )
        second = upsert_holder(
            conn,
            chat_url=_CSE_URL,
            registration_id="reg-2",
            execution_id="exec-2",
            lane_thread_id="11667",
        )
        rows = conn.execute("SELECT COUNT(*) AS n FROM cse_session_holders").fetchone()
    assert rows["n"] == 1
    assert first["holder_id"] == second["holder_id"] == "cse_testholder1"
    assert second["registration_id"] == "reg-2"
    assert second["lane_thread_id"] == "11667"


def test_state_transitions_emit_events(ledger: CursorDispatchLedger) -> None:
    emitted: list[tuple[str, dict]] = []

    def _capture(signal: str, payload: dict) -> None:
        emitted.append((signal, payload))

    with patch(
        "services.git_integration_worker.cse_session_holders._emit",
        side_effect=_capture,
    ):
        with ledger._connect() as conn:
            ensure_schema(conn)
            upsert_holder(conn, chat_url=_CSE_URL, registration_id="reg-a")
            transition_seat_state(conn, "cse_testholder1", to_state="dormant")
            transition_seat_state(
                conn,
                "cse_testholder1",
                to_state="driving",
                registration_id="reg-b",
                execution_id="exec-b",
            )
            row = get_holder(conn, "cse_testholder1")
    assert row is not None
    assert row["seat_state"] == "driving"
    signals = [s for s, _ in emitted]
    assert "cse.holder.bound" in signals
    assert "cse.holder.dormant" in signals
    assert "cse.holder.relaunched" in signals


def test_boot_reconcile_releases_absent_registry(ledger: CursorDispatchLedger) -> None:
    with ledger._connect() as conn:
        ensure_schema(conn)
        upsert_holder(conn, chat_url=_CSE_URL, registration_id="reg-x")
    with patch(
        "claude_bundles.cdp_registry.session_address.list_active",
        return_value=[],
    ), patch(
        "claude_bundles.cdp_registry.session_address.chat_url_for_registration",
        return_value=None,
    ):
        with ledger._connect() as conn:
            summary = boot_reconcile(conn)
            row = get_holder(conn, "cse_testholder1")
    assert summary["released"] == 1
    assert row is not None
    assert row["seat_state"] == "released"
