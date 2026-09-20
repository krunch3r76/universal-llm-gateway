"""Unit tests for CSE session holder CRUD and transitions."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from services.git_integration_worker.cse_session_holders import (
    boot_reconcile,
    ensure_schema,
    get_holder,
    occupy_holder_on_hop,
    resolve_nest_parent,
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


def test_boot_reconcile_keeps_driving_on_empty_registry_snapshot(
    ledger: CursorDispatchLedger,
) -> None:
    emitted: list[tuple[str, dict]] = []

    def _capture(signal: str, payload: dict) -> None:
        emitted.append((signal, payload))

    with ledger._connect() as conn:
        ensure_schema(conn)
        upsert_holder(conn, chat_url=_CSE_URL, registration_id="reg-x")
    with patch(
        "claude_bundles.cdp_registry.session_address.list_active",
        return_value=[],
    ), patch(
        "claude_bundles.cdp_registry.dormant.list_dormant",
        return_value=[],
    ), patch(
        "services.git_integration_worker.cse_holder_boot_reconcile._emit",
        side_effect=_capture,
    ):
        with ledger._connect() as conn:
            summary = boot_reconcile(conn)
            row = get_holder(conn, "cse_testholder1")
    assert summary["released"] == 0
    assert summary["skipped_release_empty_snapshot"] == 1
    assert row is not None
    assert row["seat_state"] == "driving"
    skip_signals = [
        p for s, p in emitted if s == "cse.holder.reconcile_skip_release"
    ]
    assert len(skip_signals) == 1
    assert skip_signals[0]["holder_id"] == "cse_testholder1"
    assert skip_signals[0]["reason"] == "empty_registry_snapshot"


def test_boot_reconcile_releases_driving_absent_from_nonempty_registry(
    ledger: CursorDispatchLedger,
) -> None:
    from types import SimpleNamespace

    other_url = "https://claude.ai/cowork/cse_otherholder"
    other_reg = SimpleNamespace(
        registration_id="reg-other",
        execution_id="exec-other",
    )
    with ledger._connect() as conn:
        ensure_schema(conn)
        upsert_holder(conn, chat_url=_CSE_URL, registration_id="reg-x")
    with patch(
        "claude_bundles.cdp_registry.session_address.list_active",
        return_value=[other_reg],
    ), patch(
        "claude_bundles.cdp_registry.session_address.chat_url_for_registration",
        return_value=other_url,
    ), patch(
        "claude_bundles.cdp_registry.dormant.list_dormant",
        return_value=[],
    ):
        with ledger._connect() as conn:
            summary = boot_reconcile(conn)
            row = get_holder(conn, "cse_testholder1")
    assert summary["released"] == 1
    assert summary.get("skipped_release_empty_snapshot", 0) == 0
    assert row is not None
    assert row["seat_state"] == "released"


def test_boot_reconcile_dormant_holder_survives_restart(
    ledger: CursorDispatchLedger,
) -> None:
    with ledger._connect() as conn:
        ensure_schema(conn)
        upsert_holder(conn, chat_url=_CSE_URL, registration_id="reg-d")
        transition_seat_state(conn, "cse_testholder1", to_state="dormant")
        conn.commit()
    with patch(
        "claude_bundles.cdp_registry.session_address.list_active",
        return_value=[],
    ), patch(
        "claude_bundles.cdp_registry.dormant.list_dormant",
        return_value=[],
    ):
        with ledger._connect() as conn:
            summary = boot_reconcile(conn)
            row = get_holder(conn, "cse_testholder1")
            parent = resolve_nest_parent(conn, "cse_testholder1")
    assert summary["released"] == 0
    assert row is not None
    assert row["seat_state"] == "dormant"
    assert parent is not None


def test_ledger_init_survives_boot_reconcile_registry_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from claude_bundles.cdp_registry_store import RegistryStoreError

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    CursorDispatchLedger._instance = None
    with patch(
        "claude_bundles.cdp_registry.session_address.list_active",
        side_effect=RegistryStoreError("corrupt test registry"),
    ):
        ledger = CursorDispatchLedger.instance()
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='cursor_sdk_dispatches'"
        ).fetchone()
    assert row is not None


def test_boot_reconcile_adopts_registry_without_holder_row(
    ledger: CursorDispatchLedger,
) -> None:
    from types import SimpleNamespace

    reg = SimpleNamespace(
        registration_id="reg-adopt",
        execution_id="exec-adopt",
    )
    with ledger._connect() as conn:
        ensure_schema(conn)
    with patch(
        "claude_bundles.cdp_registry.session_address.list_active",
        return_value=[reg],
    ), patch(
        "claude_bundles.cdp_registry.session_address.chat_url_for_registration",
        return_value=_CSE_URL,
    ), patch(
        "claude_bundles.cdp_registry.dormant.list_dormant",
        return_value=[],
    ):
        with ledger._connect() as conn:
            summary = boot_reconcile(conn)
            row = get_holder(conn, "cse_testholder1")
    assert summary["adopted"] == 1
    assert row is not None
    assert row["registration_id"] == "reg-adopt"


def test_occupy_supersedes_same_lane_missed_mint_when_prior_reg_differs(
    ledger: CursorDispatchLedger,
) -> None:
    """AC1b: synthetic driving row (empty reg) must yield to a real occupy_target.

    Hop verb overloads cse_registration_id as superseded_registration_id. If
    that is the seated CSE's current reg, the missed-mint predecessor still
    has to be superseded — it is same-lane, not a foreign peer.
    """
    synthetic = "https://claude.ai/cowork/cse_01scratchoccupy1"
    real = "https://claude.ai/cowork/cse_015realpredecessor"
    with ledger._connect() as conn:
        ensure_schema(conn)
        upsert_holder(
            conn,
            chat_url=synthetic,
            lane_thread_id="11830",
        )
        outcome = occupy_holder_on_hop(
            conn,
            occupy_target=real,
            lane_thread_id="11830",
            superseded_registration_id="d61ec0c2785741ac925e9ea1c57af1ea",
            new_registration_id="d61ec0c2785741ac925e9ea1c57af1ea",
            new_execution_id="exec-ac1b",
        )
        synth = get_holder(conn, "cse_01scratchoccupy1")
        occupied = get_holder(conn, "cse_015realpredecessor")
    assert outcome["ok"] is True
    assert "cse_01scratchoccupy1" in outcome["superseded_holder_ids"]
    assert synth is not None and synth["seat_state"] == "superseded"
    assert occupied is not None and occupied["seat_state"] == "driving"
