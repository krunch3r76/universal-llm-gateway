"""Tests for manage restart/rebuild post-state observation (6584 leg 1)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from scripts.model_manager.ui.controller.service_ctl.post_state import (
    ServicePostStateProbe,
    finalize_restart_rebuild_result,
    probe_service_post_state,
)
from scripts.model_manager.ui.model.service_state import ServiceInfo, ServiceStatus


@pytest.mark.offline
def test_old_ok_shape_implies_live_without_probe() -> None:
    """Old behaviour: status ok with no post-state lets callers assume liveness."""
    legacy = {"status": "ok", "message": "restarted cortex_api"}
    assert legacy.get("service_live") is None
    assert legacy.get("service_post_state") is None
    # New bar: same inputs after finalize must not allow that inference.
    probe = ServicePostStateProbe(
        probe_status="error",
        service="cortex_api",
        error_class="TimeoutError",
    )
    updated = finalize_restart_rebuild_result(legacy, probe)
    assert updated["status"] != "ok"
    assert updated["service_live"] is None
    assert updated["service_post_state"]["post_action_liveness"] == "unobservable"


@pytest.mark.offline
def test_observed_running_yields_ok_and_service_live_true() -> None:
    legacy = {"status": "ok", "message": "done"}
    probe = ServicePostStateProbe(
        probe_status="ok",
        service="agent_bus",
        service_status=ServiceStatus.RUNNING.value,
        pid=4242,
    )
    updated = finalize_restart_rebuild_result(legacy, probe)
    assert updated["status"] == "ok"
    assert updated["service_live"] is True
    assert updated["service_post_state"]["post_action_liveness"] == "live"
    assert updated["service_post_state"]["pid"] == 4242


@pytest.mark.offline
def test_observed_stopped_is_not_ok() -> None:
    legacy = {"status": "ok", "message": "cycle finished"}
    probe = ServicePostStateProbe(
        probe_status="ok",
        service="event_service",
        service_status=ServiceStatus.STOPPED.value,
    )
    updated = finalize_restart_rebuild_result(legacy, probe)
    assert updated["status"] == "completed"
    assert updated["service_live"] is False
    assert updated["service_post_state"]["post_action_liveness"] == "not_live"


@pytest.mark.offline
def test_scheduled_restart_is_unobservable_not_ok() -> None:
    legacy = {"status": "ok", "message": "MCP restart scheduled"}
    probe = ServicePostStateProbe(
        probe_status="ok",
        service="mcp",
        service_status=ServiceStatus.RUNNING.value,
        restart_scheduled=True,
    )
    updated = finalize_restart_rebuild_result(legacy, probe)
    assert updated["status"] == "scheduled"
    assert updated["service_live"] is None
    assert updated["service_post_state"]["restart_scheduled"] is True


@pytest.mark.offline
def test_deferred_result_untouched() -> None:
    deferred = {"status": "deferred", "state": "busy", "service": "stargate"}
    probe = ServicePostStateProbe(probe_status="error", service="stargate")
    assert finalize_restart_rebuild_result(deferred, probe) == deferred


@pytest.mark.offline
def test_probe_exception_is_unobservable_not_empty() -> None:
    svc = MagicMock()
    probe = probe_service_post_state(
        svc,
        "cortex_api",
        check_one=lambda _s, _n: (_ for _ in ()).throw(TimeoutError("probe")),
    )
    assert probe.probe_status == "error"
    assert probe.service_live is None
    assert probe.error_class == "TimeoutError"


@pytest.mark.offline
def test_probe_success_maps_service_info() -> None:
    svc = MagicMock()

    def _check(_svc: object, _name: str) -> ServiceInfo:
        return ServiceInfo(
            name="Agent Bus",
            status=ServiceStatus.RUNNING,
            pid=99,
            detail="uds live",
        )

    probe = probe_service_post_state(svc, "agent_bus", check_one=_check)
    assert probe.probe_status == "ok"
    assert probe.service_status == "running"
    assert probe.pid == 99
    assert probe.detail == "uds live"
