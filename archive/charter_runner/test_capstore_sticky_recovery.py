"""CapStore sticky recoverable stop auto-clear (6486 Path B #2)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from scripts.model_manager.ui.controller.charter_runner.admission.caps import (
    RECOVERABLE_STOP_REASONS,
    CapStore,
)
from scripts.model_manager.ui.controller.charter_runner.cap_stop_recovery import (
    substrate_healthy_for_cap_clear,
)


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "runner-data"
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(root))
    return root


@pytest.fixture
def caps(data_dir: Path) -> CapStore:
    return CapStore()


def _healthy_giw_payload() -> dict:
    return {"write_lease": {}, "busy": False}


@pytest.mark.offline
def test_persist_stop_writes_metadata(data_dir: Path, caps: CapStore) -> None:
    caps.mark_failed("6489", "admission_rejected")
    stop_path = data_dir / "cap-stops" / "6489.json"
    raw = json.loads(stop_path.read_text(encoding="utf-8"))
    assert raw["stopped_reason"] == "admission_rejected"
    assert isinstance(raw["stopped_at"], float)
    assert raw["auto_clear_count"] == 0


@pytest.mark.offline
def test_healthy_probe_clears_recoverable_stop_once(caps: CapStore) -> None:
    caps.mark_failed("6489", "admission_rejected")
    assert caps.try_auto_clear_recoverable_stop("6489", healthy=True) is True
    allowed, reason = caps.check("6489")
    assert allowed is True
    assert reason is None


@pytest.mark.offline
def test_second_sticky_stop_does_not_auto_clear(caps: CapStore) -> None:
    caps.mark_failed("6489", "admission_rejected")
    assert caps.try_auto_clear_recoverable_stop("6489", healthy=True) is True
    caps.mark_failed("6489", "admission_rejected")
    assert caps.try_auto_clear_recoverable_stop("6489", healthy=True) is False
    allowed, reason = caps.check("6489")
    assert allowed is False
    assert reason == "stopped:admission_rejected"


@pytest.mark.offline
def test_unhealthy_probe_keeps_stop(caps: CapStore) -> None:
    caps.mark_failed("6489", "admission_transport_error")
    assert caps.try_auto_clear_recoverable_stop("6489", healthy=False) is False
    allowed, reason = caps.check("6489")
    assert allowed is False
    assert reason == "stopped:admission_transport_error"


@pytest.mark.offline
def test_non_recoverable_reason_never_auto_clears(caps: CapStore) -> None:
    caps.mark_failed("6489", "gate_defer_escalated:defer_age_exceeded")
    assert caps.try_auto_clear_recoverable_stop("6489", healthy=True) is False
    allowed, reason = caps.check("6489")
    assert allowed is False
    assert "gate_defer_escalated" in (reason or "")


@pytest.mark.offline
def test_human_reset_clears_recovery_and_allows_fresh_auto_clear(
    data_dir: Path, caps: CapStore
) -> None:
    caps.mark_failed("6489", "admission_rejected")
    assert caps.try_auto_clear_recoverable_stop("6489", healthy=True) is True
    caps.mark_failed("6489", "admission_rejected")
    assert caps.try_auto_clear_recoverable_stop("6489", healthy=True) is False

    caps.reset("6489")
    recovery_path = data_dir / "cap-stop-recovery" / "6489.json"
    assert not recovery_path.is_file()

    caps.mark_failed("6489", "admission_rejected")
    assert caps.try_auto_clear_recoverable_stop("6489", healthy=True) is True


@pytest.mark.offline
def test_recoverable_stop_survives_capstore_recycle(data_dir: Path) -> None:
    caps = CapStore()
    caps.mark_failed("6489", "admission_transport_error")
    recycled = CapStore()
    allowed, reason = recycled.check("6489")
    assert allowed is False
    assert reason == "stopped:admission_transport_error"
    assert recycled.try_auto_clear_recoverable_stop("6489", healthy=True) is True


@pytest.mark.offline
@pytest.mark.asyncio
async def test_substrate_healthy_when_no_drain_and_no_lease() -> None:
    with patch(
        "scripts.model_manager.ui.controller.charter_runner.cap_stop_recovery.fetch_giw_active_work_payload",
        new=AsyncMock(return_value=_healthy_giw_payload()),
    ), patch(
        "scripts.model_manager.ui.controller.restart_intent_store.RestartIntentStore.instance",
    ) as mock_store:
        mock_store.return_value.active_for_service.return_value = None
        assert await substrate_healthy_for_cap_clear() is True


@pytest.mark.offline
@pytest.mark.asyncio
async def test_substrate_unhealthy_when_lease_held() -> None:
    payload = {
        "write_lease": {"holder_dispatch_id": "auto-disp-live"},
        "busy": False,
    }
    with patch(
        "scripts.model_manager.ui.controller.charter_runner.cap_stop_recovery.fetch_giw_active_work_payload",
        new=AsyncMock(return_value=payload),
    ), patch(
        "scripts.model_manager.ui.controller.restart_intent_store.RestartIntentStore.instance",
    ) as mock_store:
        mock_store.return_value.active_for_service.return_value = None
        assert await substrate_healthy_for_cap_clear() is False


@pytest.mark.offline
@pytest.mark.asyncio
async def test_substrate_unhealthy_when_giw_draining() -> None:
    with patch(
        "scripts.model_manager.ui.controller.charter_runner.cap_stop_recovery.fetch_giw_active_work_payload",
        new=AsyncMock(return_value=_healthy_giw_payload()),
    ), patch(
        "scripts.model_manager.ui.controller.restart_intent_store.RestartIntentStore.instance",
    ) as mock_store:
        mock_store.return_value.active_for_service.return_value = object()
        assert await substrate_healthy_for_cap_clear() is False


@pytest.mark.offline
def test_recoverable_reasons_cover_transport_and_rejected() -> None:
    assert "admission_rejected" in RECOVERABLE_STOP_REASONS
    assert "admission_transport_error" in RECOVERABLE_STOP_REASONS
    assert "gate_defer_escalated:defer_age_exceeded" not in RECOVERABLE_STOP_REASONS
