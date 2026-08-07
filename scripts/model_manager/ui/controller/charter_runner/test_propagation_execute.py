"""Unit tests for charter harvest propagation."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from implement_admission.propagation_row import PropagationRow

from scripts.model_manager.ui.controller.charter_runner.propagation_execute import (
    PROOF_PROBE_REGISTRY,
    ProbeDispatchResult,
    PropagationPlan,
    _build_proof_probe_registry,
    dispatch_proof_probe,
    execute_propagation_plan,
    giw_i2_clear,
    install_propagation_context,
    plan_propagation,
    proof_matches,
    set_probe_client_for_tests,
)

_SHA = "abcd000000000000000000000000000000000000"
_SHA_OTHER = "other0000000000000000000000000000000000"


def _closeout_turn(*, residue: list[str] | None = None, files_modified: list[str] | None = None, propagation: list[dict] | None = None) -> dict:
    body: dict = {"status": "complete", "evidence_uris": {"git_refs": ["land-sha"]}}
    if residue is not None:
        body["propagation_residue"] = residue
    if files_modified is not None:
        body["files_modified"] = files_modified
    if propagation is not None:
        body["propagation"] = propagation
    return {"turn_number": 3, "body": json.dumps(body)}


def _dispatch_result(payload: dict | None, *, requested: str, executed: str | None = None, error: str | None = None) -> ProbeDispatchResult:
    return ProbeDispatchResult(
        payload=payload,
        proof_class_requested=requested,
        proof_class_executed=executed if executed is not None else requested,
        error=error,
    )


def test_unsupported_proof_class_fails_loud_not_echo() -> None:
    """Requested class with no registered probe must fail — not echo a default probe."""
    row = PropagationRow(
        service="mcp",
        code_ref=_SHA,
        proof_class="served_artifact",
        proof_class_requested="served_artifact",
        # Explicit proof: mcp×served_artifact has no compose_proof template.
        proof="unsupported pair for dispatch fail-loud test",
    )
    result = dispatch_proof_probe(row)
    assert result.error is not None
    assert result.error.startswith("proof_class_unsupported:")
    assert "service=mcp" in result.error
    assert "requested=served_artifact" in result.error
    assert "client_visible" in result.error
    assert result.payload is None
    assert result.proof_class_executed is None


def test_process_live_registry_excludes_unprobeable() -> None:
    """M2: unsatisfiable (slug, process_live) pairs are not registered."""
    from services.git_integration_worker.cursor_auto.propagation_probe import (
        process_live_probeable_services,
    )

    probeable = process_live_probeable_services()
    assert "email_bridge" not in probeable
    assert ("email_bridge", "process_live") not in PROOF_PROBE_REGISTRY
    for slug in (
        "git_integration_worker",
        "mcp",
        "cortex_api",
        "gateway",
        "stargate",
        "rag",
        "cloud_proxy",
        "event_service",
        "cdp_ask",
        "agent_bus",
    ):
        assert slug in probeable
        assert (slug, "process_live") in PROOF_PROBE_REGISTRY

    row = PropagationRow(
        service="email_bridge",
        code_ref=_SHA,
        proof_class="process_live",
        proof_class_requested="process_live",
        proof="unprobeable process_live must fail loud",
    )
    result = dispatch_proof_probe(row)
    assert result.error is not None
    assert result.error.startswith("proof_class_unsupported:")
    assert "service=email_bridge" in result.error
    assert result.payload is None


def test_process_live_registry_unlocks_when_fetcher_added(monkeypatch) -> None:
    """M2: oracle is fetcher-map derived — adding a fetcher unlocks advertisement."""
    from services.git_integration_worker.cursor_auto import propagation_probe

    monkeypatch.setitem(
        propagation_probe.PROCESS_LIVE_FETCHERS,
        "email_bridge",
        lambda: {"code_version": "deadbeef", "pid": 9},
    )
    rebuilt = _build_proof_probe_registry()
    assert ("email_bridge", "process_live") in rebuilt
    assert "email_bridge" in propagation_probe.process_live_probeable_services()


def test_served_artifact_dispatch_populates_fingerprint(monkeypatch) -> None:
    """served_artifact execution must invoke fetch and populate sha256 + semantic fields."""
    captured: dict[str, object] = {}

    def _fake_probe(service: str, *, code_ref: str, expected_x_mcp_count: int | None = None):
        captured["service"] = service
        captured["code_ref"] = code_ref
        return {
            "proof_class": "served_artifact",
            "surfaces": {
                "uds": {
                    "bytes_sha256": "abc123",
                    "x_mcp_count": 46,
                }
            },
            "byte_identical": True,
            "x_mcp_count": 46,
            "expected_x_mcp_count": 46,
        }

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.propagation_served_artifact.probe_served_artifact",
        _fake_probe,
    )
    row = PropagationRow(
        service="cortex_api",
        code_ref=_SHA,
        proof_class="served_artifact",
        proof_class_requested="served_artifact",
    )
    result = dispatch_proof_probe(row)
    assert result.error is None
    assert captured["service"] == "cortex_api"
    assert result.payload is not None
    assert result.payload["surfaces"]["uds"]["bytes_sha256"] == "abc123"
    assert result.payload["x_mcp_count"] == 46
    assert result.proof_class_executed == "served_artifact"


@pytest.mark.asyncio
async def test_execute_fails_unsupported_proof_class_without_restart(tmp_path, monkeypatch):
    """Execute path must terminate unsupported rows — no restart, no echo."""
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))

    from charter_runner_store.propagation_ledger import upsert_open_rows

    upsert_open_rows(
        [
            PropagationRow(
                service="mcp",
                code_ref=_SHA,
                proof_class="served_artifact",
                proof_class_requested="served_artifact",
                proof="unsupported pair for execute fail-loud test",
            )
        ]
    )

    plan = PropagationPlan(rows=[], sync_restart_services=[])
    ctl = MagicMock()
    install_propagation_context(ctl, event_bus=None)

    with patch(
        "scripts.model_manager.ui.api_dispatch.sync_restart_charter_harvest",
        new=AsyncMock(),
    ) as restart:
        results = await execute_propagation_plan(plan, root_id="root", window_index=1)

    restart.assert_not_called()
    assert results["remaining"]
    defer = results["remaining"][0]["defer_reason"]
    assert defer.startswith("proof_class_unsupported:")
    assert results["remaining"][0]["proof_class_executed"] is None
    assert results["remaining"][0]["disposition"] == "failed_proof_class_unsupported"


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
        row_id=f"mcp:{_SHA}:sync_restart",
        service="mcp",
        code_ref=_SHA,
        safe_window="standalone_ok",
        age_in_harvests=0,
        mint_thread=None,
        mint_turn=None,
        defer_reason=None,
        proof_class="client_visible",
        hazard=None,
        reason=None,
        settle_boundary_monotonic=None,
    )
    both_match = {
        "mcp_health": {"code_version": _SHA},
        "cortex_api": {"code_version": _SHA},
    }
    assert proof_matches(row, both_match)
    assert not proof_matches(
        row,
        {"mcp_health": {"code_version": _SHA}, "cortex_api": {"code_version": _SHA_OTHER}},
    )


def test_proof_matches_process_live_requires_identity_delta() -> None:
    from charter_runner_store.propagation_ledger import OpenPropagationProjection

    row = OpenPropagationProjection(
        row_id=f"cortex_api:{_SHA}:sync_restart",
        service="cortex_api",
        code_ref=_SHA,
        safe_window="harvest",
        age_in_harvests=0,
        mint_thread=None,
        mint_turn=None,
        defer_reason=None,
        proof_class="process_live",
        hazard=None,
        reason=None,
        settle_boundary_monotonic=None,
    )
    before = {"code_version": _SHA_OTHER, "pid": 10}
    after = {"code_version": _SHA, "pid": 10}
    assert not proof_matches(row, after, before=before)
    after_new = {"code_version": _SHA, "pid": 11}
    assert proof_matches(row, after_new, before=before)


@pytest.mark.asyncio
async def test_execute_closes_on_proof_not_on_restart_status(tmp_path, monkeypatch):
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))

    before = {"code_version": _SHA, "pid": 100}
    after = {"code_version": _SHA, "pid": 200}

    plan = PropagationPlan(
        rows=[
            PropagationRow(
                service="cortex_api",
                code_ref=_SHA,
                proof_class="process_live",
            )
        ],
        sync_restart_services=["cortex_api"],
    )

    ctl = MagicMock()
    install_propagation_context(ctl, event_bus=None)

    with (
        patch(
            "scripts.model_manager.ui.controller.charter_runner.propagation_execute.dispatch_for_projection",
            side_effect=[
                # D2 ancestry pre-pass + harvest before + harvest after
                _dispatch_result(before, requested="process_live"),
                _dispatch_result(before, requested="process_live"),
                _dispatch_result(after, requested="process_live"),
            ],
        ),
        patch(
            "scripts.model_manager.ui.api_dispatch.sync_restart_charter_harvest",
            new=AsyncMock(return_value={"status": "ok"}),
        ),
    ):
        results = await execute_propagation_plan(plan, root_id="root", window_index=1)

    assert results["closed"]
    assert results["closed"][0]["proof_class_executed"] == "process_live"
    assert not results["remaining"]


@pytest.mark.asyncio
async def test_execute_does_not_early_close_without_identity_change(tmp_path, monkeypatch):
    """Harvest must not close when code_version matches but process identity is unchanged."""
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))

    payload = {"code_version": _SHA, "pid": 100}

    plan = PropagationPlan(
        rows=[
            PropagationRow(
                service="cortex_api",
                code_ref=_SHA,
                proof_class="process_live",
            )
        ],
        sync_restart_services=["cortex_api"],
    )

    ctl = MagicMock()
    install_propagation_context(ctl, event_bus=None)

    with (
        patch(
            "scripts.model_manager.ui.controller.charter_runner.propagation_execute.dispatch_for_projection",
            side_effect=[
                # D2 ancestry pre-pass + harvest before + harvest after
                _dispatch_result(payload, requested="process_live"),
                _dispatch_result(payload, requested="process_live"),
                _dispatch_result(payload, requested="process_live"),
            ],
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
            json={"code_version": _SHA_OTHER, "claimed": 1, "pending": 0},
        )
    )
    set_probe_client_for_tests(httpx.Client(transport=transport))

    plan = PropagationPlan(
        rows=[
            PropagationRow(
                service="git_integration_worker",
                code_ref=_SHA,
                proof_class="process_live",
                hazard="closeout_relay",
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
            json={"code_version": _SHA_OTHER, "claimed": 0, "pending": 0},
        )
    )
    set_probe_client_for_tests(httpx.Client(transport=transport))

    plan = PropagationPlan(
        rows=[
            PropagationRow(
                service="git_integration_worker",
                code_ref=_SHA,
                proof_class="process_live",
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
