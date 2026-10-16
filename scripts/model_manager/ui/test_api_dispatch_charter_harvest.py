"""Tests for sync_restart_charter_harvest outcome vocabulary."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_mcp_deferred_never_reports_ok_at_harvest_path() -> None:
    """AC-1: a deferred manage response must not present as success."""
    ctl = MagicMock()
    deferred = {
        "status": "deferred",
        "state": "busy",
        "reason": "cdp_ask_live",
    }
    mcp_stub = AsyncMock(return_value=deferred)
    docker_check = MagicMock()
    before_stub = AsyncMock(return_value={"old": 1, "identity_source": "test_stub"})
    attach_stub = AsyncMock(
        side_effect=lambda _ctl, _svc, result, _before, **_kw: {
            **result,
            "authority_identity": {"readiness_proven": False},
        }
    )
    with (
        patch(
            "scripts.model_manager.ui.controller.service_ctl.authority_identity.snapshot_before_restart",
            new=before_stub,
        ),
        patch(
            "scripts.model_manager.ui.api_dispatch._attach_harvest_authority_identity",
            new=attach_stub,
        ),
        patch(
            "scripts.model_manager.ui.api_dispatch._mcp_deferred_sync_restart",
            new=mcp_stub,
        ),
        patch(
            "scripts.model_manager.ui.model.service_state.ServiceState.check_mcp",
            docker_check,
        ),
    ):
        from scripts.model_manager.ui.api_dispatch import sync_restart_charter_harvest

        result = await sync_restart_charter_harvest(ctl, "mcp")

    assert before_stub.await_count == 1
    assert attach_stub.await_count == 1
    assert mcp_stub.await_count == 1
    assert docker_check.call_count == 0
    assert result["status"] == "deferred"
    assert result["outcome"] == "declined"
    assert result.get("status") != "ok"


@pytest.mark.asyncio
async def test_mcp_ok_after_restart_reports_proven() -> None:
    ctl = MagicMock()
    mcp_stub = AsyncMock(return_value={"status": "ok", "message": "scheduled"})
    wait_stub = AsyncMock(return_value=1.5)
    docker_inspect = AsyncMock(return_value="2024-01-01T00:00:00+00:00")
    before_stub = AsyncMock(return_value={"old": 1, "identity_source": "test_stub"})
    attach_stub = AsyncMock(
        side_effect=lambda _ctl, _svc, result, _before, **_kw: {
            **result,
            "authority_identity": {"readiness_proven": True},
        }
    )

    with (
        patch(
            "scripts.model_manager.ui.controller.service_ctl.authority_identity.snapshot_before_restart",
            new=before_stub,
        ),
        patch(
            "scripts.model_manager.ui.api_dispatch._attach_harvest_authority_identity",
            new=attach_stub,
        ),
        patch(
            "scripts.model_manager.ui.api_dispatch._mcp_deferred_sync_restart",
            new=mcp_stub,
        ),
        patch(
            "scripts.model_manager.ui.api_dispatch._wait_healthy",
            new=wait_stub,
        ),
        patch(
            "scripts.model_manager.ui.controller.service_ctl.authority_identity._read_container_started_at",
            new=docker_inspect,
        ),
    ):
        from scripts.model_manager.ui.api_dispatch import sync_restart_charter_harvest

        result = await sync_restart_charter_harvest(ctl, "mcp")

    assert before_stub.await_count == 1
    assert attach_stub.await_count == 1
    assert mcp_stub.await_count == 1
    assert wait_stub.await_count == 1
    assert docker_inspect.await_count == 0
    assert result["status"] == "ok"
    assert result["outcome"] == "proven"
    assert result["wait_healthy_s"] == 1.5


@pytest.mark.asyncio
async def test_readiness_proven_false_when_wait_healthy_times_out() -> None:
    """Producer join: restart ok + wait_healthy TimeoutError ⇒ readiness_proven False;
    authority old≠new must not close proof_observed.
    """
    from implement_admission.propagation_row import PropagationRow

    from scripts.model_manager.ui.api_dispatch import sync_restart_charter_harvest
    from services.git_integration_worker.relay.propagation_probe import (
        attest_authority_identity,
        proof_observed,
    )

    row_sha = "abc1230000000000000000000000000000000000"
    ctl = MagicMock()
    before_snap = {"old": 100, "identity_source": "manage_host_pid"}

    async def _finalize(
        service_state: object,
        service: str,
        before: object,
        *,
        readiness_proven: bool,
        intent_id: str | None = None,
    ) -> dict[str, object]:
        assert readiness_proven is False
        return {
            "service": service,
            "old": before["old"],
            "new": 200,
            "identity_source": before["identity_source"],
            "readiness_proven": readiness_proven,
        }

    with (
        patch(
            "scripts.model_manager.ui.controller.service_ctl.authority_identity.snapshot_before_restart",
            new=AsyncMock(return_value=before_snap),
        ),
        patch(
            "scripts.model_manager.ui.api_dispatch.run_gated",
            new=AsyncMock(return_value={"status": "ok"}),
        ),
        patch(
            "scripts.model_manager.ui.api_dispatch._wait_healthy",
            new=AsyncMock(side_effect=TimeoutError("timed out")),
        ),
        patch(
            "scripts.model_manager.ui.controller.service_ctl.authority_identity.finalize_authority_identity",
            new=AsyncMock(side_effect=_finalize),
        ),
    ):
        outcome = await sync_restart_charter_harvest(ctl, "stargate")

    authority = outcome["authority_identity"]
    assert authority["readiness_proven"] is False
    assert attest_authority_identity(authority) == "fall_through"
    before = {"code_version": row_sha}
    after = {"code_version": row_sha}
    authority = {
        **authority,
        "old": 100,
        "new": 200,
        "identity_source": "manage_host_pid",
    }
    row = PropagationRow(
        service="stargate",
        code_ref=row_sha,
        safe_window="standalone_ok",
        proof="test probe",
        proof_class="process_live",
    )
    assert (
        proof_observed(row, after, before=before, authority_identity=authority)
        is False
    )
