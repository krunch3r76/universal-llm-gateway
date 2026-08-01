"""Unit tests for charter harvest propagation."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from implement_admission.propagation_row import PropagationRow

from scripts.model_manager.ui.controller.charter_runner.propagation_execute import (
    PropagationPlan,
    execute_propagation_plan,
    giw_i2_clear,
    install_propagation_context,
    plan_propagation,
    proof_matches,
    set_probe_client_for_tests,
)


def _closeout_turn(*, residue: list[str] | None = None, files_modified: list[str] | None = None, propagation: list[dict] | None = None) -> dict:
    body: dict = {"status": "complete", "evidence_uris": {"git_refs": ["land-sha"]}}
    if residue is not None:
        body["propagation_residue"] = residue
    if files_modified is not None:
        body["files_modified"] = files_modified
    if propagation is not None:
        body["propagation"] = propagation
    return {"turn_number": 3, "body": json.dumps(body)}


def test_plan_propagation_from_residue_lines() -> None:
    plan = plan_propagation(
        [
            _closeout_turn(
                residue=[
                    'sync_restart: mcp — manage(action="sync_restart", service="mcp")',
                    'sync_restart: git_integration_worker — manage(action="sync_restart", service="git_integration_worker")',
                ]
            )
        ]
    )
    assert plan is not None
    assert plan.sync_restart_services == ["mcp", "git_integration_worker"]
    assert len(plan.rows) == 2
    assert plan.charter_reload is False


def test_plan_propagation_structured_rows_win() -> None:
    plan = plan_propagation(
        [
            _closeout_turn(
                propagation=[
                    {
                        "service": "mcp",
                        "action": "sync_restart",
                        "code_ref": "structured-sha",
                        "safe_window": "standalone_ok",
                        "proof": "GET /health",
                        "proof_class": "client_visible",
                    }
                ],
                residue=['sync_restart: git_integration_worker — x'],
            )
        ]
    )
    assert plan is not None
    assert len(plan.rows) == 1
    assert plan.rows[0].service == "mcp"


def test_plan_propagation_charter_reload_from_paths() -> None:
    plan = plan_propagation(
        [
            _closeout_turn(
                files_modified=[
                    "scripts/model_manager/ui/controller/charter_runner/harvest.py",
                ]
            )
        ]
    )
    assert plan is not None
    assert plan.charter_reload is True
    assert plan.sync_restart_services == []


def test_plan_propagation_derives_service_from_path_when_residue_empty() -> None:
    plan = plan_propagation(
        [
            _closeout_turn(
                files_modified=["services/mcp-server/server.py"],
            )
        ]
    )
    assert plan is not None
    assert plan.sync_restart_services == ["mcp"]


def test_plan_propagation_none_without_actions() -> None:
    assert plan_propagation([_closeout_turn()]) is None


def test_giw_i2_clear_when_queue_has_claimed_jobs() -> None:
    ok, reason = giw_i2_clear(queue_snapshot={"claimed": 1, "pending": 0})
    assert ok is False
    assert reason == "i2_inflight_closeout"


def test_proof_matches_code_version() -> None:
    from charter_runner_store.propagation_ledger import OpenPropagationProjection

    row = OpenPropagationProjection(
        row_id="mcp:sha:sync_restart",
        service="mcp",
        code_ref="sha",
        safe_window="standalone_ok",
        age_in_harvests=0,
        mint_thread=None,
        mint_turn=None,
        defer_reason=None,
        proof_class="client_visible",
        hazard=None,
        reason=None,
    )
    both_match = {
        "mcp_health": {"code_version": "sha"},
        "cortex_api": {"code_version": "sha"},
    }
    assert proof_matches(row, both_match)
    assert not proof_matches(
        row,
        {"mcp_health": {"code_version": "sha"}, "cortex_api": {"code_version": "other"}},
    )


def test_proof_matches_process_live_requires_identity_delta() -> None:
    from charter_runner_store.propagation_ledger import OpenPropagationProjection

    row = OpenPropagationProjection(
        row_id="cortex_api:sha:sync_restart",
        service="cortex_api",
        code_ref="sha",
        safe_window="harvest",
        age_in_harvests=0,
        mint_thread=None,
        mint_turn=None,
        defer_reason=None,
        proof_class="process_live",
        hazard=None,
        reason=None,
    )
    before = {"code_version": "old", "pid": 10}
    after = {"code_version": "sha", "pid": 10}
    assert not proof_matches(row, after, before=before)
    after_new = {"code_version": "sha", "pid": 11}
    assert proof_matches(row, after_new, before=before)


@pytest.mark.asyncio
async def test_execute_closes_on_proof_not_on_restart_status(tmp_path, monkeypatch):
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))

    before = {"code_version": "land-sha", "pid": 100}
    after = {"code_version": "land-sha", "pid": 200}

    plan = PropagationPlan(
        rows=[
            PropagationRow(
                service="cortex_api",
                code_ref="land-sha",
            )
        ],
        sync_restart_services=["cortex_api"],
    )

    ctl = MagicMock()
    install_propagation_context(ctl, event_bus=None)

    with (
        patch(
            "scripts.model_manager.ui.controller.charter_runner.propagation_execute.probe_for_projection",
            side_effect=[before, after],
        ),
        patch(
            "scripts.model_manager.ui.api_dispatch.sync_restart_charter_harvest",
            new=AsyncMock(return_value={"status": "ok"}),
        ),
    ):
        results = await execute_propagation_plan(plan, root_id="root", window_index=1)

    assert results["closed"]
    assert not results["remaining"]


@pytest.mark.asyncio
async def test_execute_does_not_early_close_without_identity_change(tmp_path, monkeypatch):
    """Harvest must not close when code_version matches but process identity is unchanged."""
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))

    payload = {"code_version": "land-sha", "pid": 100}

    plan = PropagationPlan(
        rows=[
            PropagationRow(
                service="cortex_api",
                code_ref="land-sha",
            )
        ],
        sync_restart_services=["cortex_api"],
    )

    ctl = MagicMock()
    install_propagation_context(ctl, event_bus=None)

    with (
        patch(
            "scripts.model_manager.ui.controller.charter_runner.propagation_execute.probe_for_projection",
            side_effect=[payload, payload],
        ),
        patch(
            "scripts.model_manager.ui.api_dispatch.sync_restart_charter_harvest",
            new=AsyncMock(return_value={"status": "ok"}),
        ) as restart,
    ):
        results = await execute_propagation_plan(plan, root_id="root", window_index=1)

    restart.assert_called_once()
    assert not results["closed"]
    assert results["remaining"]
    assert results["remaining"][0]["defer_reason"] == "proof_not_observed_after_restart"


@pytest.mark.asyncio
async def test_execute_defers_when_i2_blocks_giw(tmp_path, monkeypatch):
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))

    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json={"code_version": "old-sha", "claimed": 1, "pending": 0},
        )
    )
    set_probe_client_for_tests(httpx.Client(transport=transport))

    plan = PropagationPlan(
        rows=[
            PropagationRow(
                service="git_integration_worker",
                code_ref="land-sha",
            )
        ],
        sync_restart_services=["git_integration_worker"],
    )
    ctl = MagicMock()
    install_propagation_context(ctl, event_bus=None)

    with patch(
        "scripts.model_manager.ui.api_dispatch.sync_restart_charter_harvest",
        new=AsyncMock(),
    ) as restart:
        results = await execute_propagation_plan(plan, root_id="root", window_index=1)

    restart.assert_not_called()
    assert results["remaining"]
    assert results["remaining"][0]["defer_reason"] == "i2_inflight_closeout"
    set_probe_client_for_tests(None)


@pytest.mark.asyncio
async def test_execute_defers_giw_without_relay_loss_hazard(tmp_path, monkeypatch):
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))

    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json={"code_version": "old-sha", "claimed": 0, "pending": 0},
        )
    )
    set_probe_client_for_tests(httpx.Client(transport=transport))

    plan = PropagationPlan(
        rows=[
            PropagationRow(
                service="git_integration_worker",
                code_ref="land-sha",
            )
        ],
        sync_restart_services=["git_integration_worker"],
    )
    ctl = MagicMock()
    install_propagation_context(ctl, event_bus=None)

    with patch(
        "scripts.model_manager.ui.api_dispatch.sync_restart_charter_harvest",
        new=AsyncMock(),
    ) as restart:
        results = await execute_propagation_plan(plan, root_id="root", window_index=1)

    restart.assert_not_called()
    assert results["remaining"]
    assert results["remaining"][0]["defer_reason"] == "giw_requires_relay_loss_hazard"
    set_probe_client_for_tests(None)


@pytest.mark.asyncio
async def test_age_two_escalates(tmp_path, monkeypatch):
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))

    from charter_runner_store.propagation_ledger import (
        bump_age_for_open_rows,
        upsert_open_rows,
    )

    upsert_open_rows([PropagationRow(service="mcp", code_ref="land-sha")])
    bump_age_for_open_rows()
    bump_age_for_open_rows()

    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json={"code_version": "stale", "claimed": 0, "pending": 0},
        )
    )
    set_probe_client_for_tests(httpx.Client(transport=transport))

    plan = PropagationPlan(rows=[], sync_restart_services=[])
    ctl = MagicMock()
    install_propagation_context(ctl, event_bus=None)

    with patch(
        "scripts.model_manager.ui.api_dispatch.sync_restart_charter_harvest",
        new=AsyncMock(return_value={"status": "ok"}),
    ):
        results = await execute_propagation_plan(plan, root_id="root", window_index=1)

    assert results["escalated"]
    set_probe_client_for_tests(None)
