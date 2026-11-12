"""MCP manage health shares one deadline between check_one and stamp read."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from scripts.model_manager.ui import api_dispatch
from scripts.model_manager.ui.model.service_state import ServiceInfo, ServiceStatus

pytestmark = pytest.mark.offline


def _service_info() -> ServiceInfo:
    return ServiceInfo(name="MCP", status=ServiceStatus.RUNNING, detail="ok")


@pytest.mark.asyncio
async def test_mcp_health_passes_remaining_budget_to_stamp_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    budgets: list[float] = []

    def _check_one_slow(_svc: object, _service: str) -> ServiceInfo:
        time.sleep(1.0)
        return _service_info()

    def _stamp(*, budget_s: float = 4.5, **_kwargs: object) -> tuple[dict, str | None]:
        budgets.append(budget_s)
        return {"deploy_mode": "source_synced", "source_synced_at": "2026-01-01T00:00:00Z"}, None

    monkeypatch.setattr(api_dispatch, "_check_one", _check_one_slow)
    monkeypatch.setattr(api_dispatch, "read_mcp_container_deploy_identity", _stamp)

    ctl = MagicMock()
    result = await api_dispatch.execute(ctl, "health", "mcp", {})

    assert result["status"] == "running"
    assert "deploy_mode" in result
    assert budgets == [pytest.approx(4.0, abs=0.35)]


@pytest.mark.asyncio
async def test_mcp_health_stamp_budget_exhausted_when_check_one_consumes_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _check_one_slow(_svc: object, _service: str) -> ServiceInfo:
        time.sleep(5.2)
        return _service_info()

    def _stamp(*, budget_s: float = 4.5, **_kwargs: object) -> tuple[dict, str | None]:
        if budget_s <= 0:
            return {}, "stamp_budget_exhausted"
        return {}, "stamp_unreadable"

    monkeypatch.setattr(api_dispatch, "_check_one", _check_one_slow)
    monkeypatch.setattr(api_dispatch, "read_mcp_container_deploy_identity", _stamp)

    ctl = MagicMock()
    result = await api_dispatch.execute(ctl, "health", "mcp", {})

    assert result["deploy_stamp_reason"] == "stamp_budget_exhausted"
