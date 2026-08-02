"""Tests for harvest-wanted propagation marker and between-window consumption."""

from __future__ import annotations

import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from implement_admission.propagation_row import PropagationRow

from charter_runner_store.propagation_ledger import (
    DEFER_HARVEST_WANTED,
    list_harvest_wanted_rows,
    list_open_rows,
    mark_harvest_wanted,
    reclaim_stale_consumption_claims,
    release_consumption_claim,
    try_claim_for_consumption,
    upsert_open_rows,
)
from charter_runner_store.propagation_outcomes import propagation_outcomes_path
from services.git_integration_worker.cursor_auto.handler_propagation import (
    execution_for_manage_deferred,
)
from scripts.model_manager.ui.controller.charter_runner.propagation_execute import (
    ProbeDispatchResult,
    install_propagation_context,
)
from scripts.model_manager.ui.controller.charter_runner.propagation_harvest_wanted import (
    consume_harvest_wanted_at_tick,
)

_SHA = "abcd000000000000000000000000000000000000"


def _mcp_row() -> PropagationRow:
    return PropagationRow(
        service="mcp",
        code_ref=_SHA,
        action="sync_restart",
        proof_class="client_visible",
        proof_class_requested="client_visible",
    )


def test_mark_harvest_wanted_persists_marker(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))
    row_id = upsert_open_rows([_mcp_row()])[0]
    assert mark_harvest_wanted(row_id)
    row = list_open_rows()[0]
    assert row.defer_reason == DEFER_HARVEST_WANTED


def test_execution_for_manage_deferred_without_intent_is_harvest_wanted(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))
    row = _mcp_row()
    row_id = upsert_open_rows([row])[0]
    result = execution_for_manage_deferred(
        row,
        row_id=row_id,
        manage_result={"status": "deferred", "state": "busy", "reason": "cdp_ask_live"},
    )
    assert result["status"] == "harvest_wanted"
    assert "charter tick will consume" in result["next"].lower()
    assert list_open_rows()[0].defer_reason == DEFER_HARVEST_WANTED


def test_try_claim_for_consumption_exactly_once(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))
    row_id = upsert_open_rows([_mcp_row()])[0]
    mark_harvest_wanted(row_id)
    assert try_claim_for_consumption(row_id, "token-a")
    assert not try_claim_for_consumption(row_id, "token-b")
    row = list_open_rows()[0]
    assert row.consumption_token == "token-a"


def test_reclaim_stale_consumption_claims_keeps_row_open(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))
    row_id = upsert_open_rows([_mcp_row()])[0]
    mark_harvest_wanted(row_id)
    assert try_claim_for_consumption(row_id, "stale-token")
    from charter_runner_store.propagation_ledger import open_ledger_db

    db = open_ledger_db()
    db.execute(
        "UPDATE propagation_ledger SET consumption_claimed_at=? WHERE row_id=?",
        (time.time() - 900.0, row_id),
    )
    db.commit()
    db.close()
    reclaimed = reclaim_stale_consumption_claims(stale_after_s=600.0)
    assert reclaimed == 1
    row = list_open_rows()[0]
    assert row.consumption_token is None
    assert row.defer_reason == DEFER_HARVEST_WANTED


def test_release_consumption_claim_returns_to_pool(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))
    row_id = upsert_open_rows([_mcp_row()])[0]
    mark_harvest_wanted(row_id)
    assert try_claim_for_consumption(row_id, "tok")
    assert release_consumption_claim(row_id, "tok")
    assert list_harvest_wanted_rows()


@pytest.mark.asyncio
async def test_consume_closes_on_proof_observed(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))
    row_id = upsert_open_rows([_mcp_row()])[0]
    mark_harvest_wanted(row_id)

    before = {"code_version": "oldsha", "proof_class_executed": "client_visible"}
    after = {"code_version": _SHA, "proof_class_executed": "client_visible"}
    ctl = MagicMock()
    install_propagation_context(ctl, event_bus=None)

    dispatch_results = [
        ProbeDispatchResult(
            payload=before,
            proof_class_requested="client_visible",
            proof_class_executed="client_visible",
            error=None,
        ),
        ProbeDispatchResult(
            payload=after,
            proof_class_requested="client_visible",
            proof_class_executed="client_visible",
            error=None,
        ),
    ]

    with (
        patch(
            "scripts.model_manager.ui.controller.charter_runner.propagation_harvest_wanted.dispatch_for_projection",
            side_effect=dispatch_results,
        ),
        patch(
            "scripts.model_manager.ui.controller.charter_runner.propagation_harvest_wanted.proof_matches",
            return_value=True,
        ),
        patch(
            "scripts.model_manager.ui.api_dispatch.sync_restart_charter_harvest",
            new=AsyncMock(return_value={"status": "ok", "service": "mcp"}),
        ),
    ):
        results = await consume_harvest_wanted_at_tick(
            tick_index=1,
            service_controller=ctl,
        )

    assert results["closed"]
    assert list_open_rows() == []
    outcome_path = propagation_outcomes_path()
    assert outcome_path.is_file()
    lines = outcome_path.read_text(encoding="utf-8").strip().splitlines()
    record = json.loads(lines[-1])
    assert record["outcome"] == "closed"
    assert record["service"] == "mcp"


@pytest.mark.asyncio
async def test_consume_failed_outcome_distinct(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))
    row_id = upsert_open_rows([_mcp_row()])[0]
    mark_harvest_wanted(row_id)
    ctl = MagicMock()
    install_propagation_context(ctl, event_bus=None)

    with (
        patch(
            "scripts.model_manager.ui.controller.charter_runner.propagation_harvest_wanted.dispatch_for_projection",
            return_value=ProbeDispatchResult(
                payload={"code_version": _SHA},
                proof_class_requested="client_visible",
                proof_class_executed="client_visible",
                error=None,
            ),
        ),
        patch(
            "scripts.model_manager.ui.api_dispatch.sync_restart_charter_harvest",
            new=AsyncMock(
                return_value={"status": "error", "reason": "mcp_busy", "service": "mcp"}
            ),
        ),
    ):
        results = await consume_harvest_wanted_at_tick(
            tick_index=2,
            service_controller=ctl,
        )

    assert results["failed"]
    assert list_open_rows() == []
    from charter_runner_store.propagation_ledger import open_ledger_db

    db = open_ledger_db()
    row = db.execute(
        "SELECT status, defer_reason FROM propagation_ledger WHERE row_id=?",
        (row_id,),
    ).fetchone()
    db.close()
    assert row["status"] == "failed"
    record = json.loads(propagation_outcomes_path().read_text().strip().splitlines()[-1])
    assert record["outcome"] == "failed"
