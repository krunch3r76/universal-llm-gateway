"""Charter tick gate-admission defer — R1 L2 (25956 / G1-G3)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from scripts.model_manager.ui.controller.charter_runner.admission import CapStore
from scripts.model_manager.ui.controller.charter_runner.executor_defaults import (
    consult_host_generate_body,
    default_judgment_body,
    operator_proxy_host_generate_body,
)
from scripts.model_manager.ui.controller.charter_runner.gate_admission_defer import (
    DEFER_MAX_AGE_S,
    clear_gate_defer,
    preflight_write_lease,
    record_gate_defer,
)
from scripts.model_manager.ui.controller.charter_runner.window_exec import dispatch


@pytest.fixture(autouse=True)
def _reset_gate_defer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import shutil

    data_dir = tmp_path / "runner-data"
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(data_dir))
    yield
    if data_dir.exists():
        shutil.rmtree(data_dir, ignore_errors=True)


@pytest.fixture
def events_log(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    log: list[tuple[str, dict[str, Any]]] = []

    async def _fake_emit(signal: str, payload: dict[str, Any], **_kw: Any) -> None:
        log.append((signal, payload))

    monkeypatch.setattr("scripts.model_manager.observation_event._emit", _fake_emit)
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.telemetry._emit",
        _fake_emit,
    )
    return log


@pytest.mark.offline
def test_write_bodies_carry_refuse_if_lease_held_flag() -> None:
    body = default_judgment_body(
        root_id="6186",
        window_index=1,
        packet_path="tmp/p.md",
        subject="s",
        caller_agent="charter-runner",
    )
    assert body.get("refuse_if_lease_held") is True
    assert body.get("read_only") is not True

    consult = consult_host_generate_body(
        root_id="6186",
        window_index=1,
        packet_path="tmp/p.md",
        subject="s",
        caller_agent="charter-runner",
    )
    assert consult.get("read_only") is True
    assert "refuse_if_lease_held" not in consult

    proxy = operator_proxy_host_generate_body(
        root_id="6006",
        window_index=1,
        packet_path="tmp/p.md",
        subject="s",
        caller_agent="charter-runner",
    )
    assert proxy.get("read_only") is True


@pytest.mark.offline
@pytest.mark.asyncio
async def test_g1_preflight_defer_emits_admission_deferred_gate_held(
    events_log: list[tuple[str, dict[str, Any]]],
) -> None:
    payload = {
        "write_lease": {
            "holder_dispatch_id": "auto-disp-abc123",
            "holder_started_at": "2026-07-28T10:00:00+00:00",
            "queue_depth": 0,
        },
        "cursor_dispatches": {"dispatch_ids": ["auto-disp-abc123"]},
        "active_ops": [{"op_id": "auto-disp-abc123"}],
    }
    with patch(
        "scripts.model_manager.ui.controller.charter_runner.gate_admission_defer.fetch_giw_active_work_payload",
        new=AsyncMock(return_value=payload),
    ):
        result = await preflight_write_lease(root_id="6186")

    assert result.outcome == "defer"
    assert result.holder_dispatch_id == "auto-disp-abc123"
    assert result.defer_count == 1


@pytest.mark.offline
@pytest.mark.asyncio
async def test_g2_defer_age_exceeded_escalates(events_log: list[tuple[str, dict[str, Any]]]) -> None:
    root = "6186"
    record_gate_defer(root, now=__import__("time").time() - DEFER_MAX_AGE_S - 1)
    payload = {
        "write_lease": {
            "holder_dispatch_id": "auto-disp-stale",
            "holder_started_at": "2026-07-28T08:00:00+00:00",
            "queue_depth": 0,
        },
        "cursor_dispatches": {"dispatch_ids": ["auto-disp-stale"]},
        "active_ops": [{"op_id": "auto-disp-stale"}],
    }
    with patch(
        "scripts.model_manager.ui.controller.charter_runner.gate_admission_defer.fetch_giw_active_work_payload",
        new=AsyncMock(return_value=payload),
    ):
        result = await preflight_write_lease(root_id=root)

    assert result.outcome == "escalate"
    assert result.escalation_reason == "defer_age_exceeded"


@pytest.mark.offline
@pytest.mark.asyncio
async def test_g3_orphan_holder_escalates_on_first_observation() -> None:
    payload = {
        "write_lease": {
            "holder_dispatch_id": "orphan-disp-dead",
            "holder_started_at": "2026-07-28T10:00:00+00:00",
            "queue_depth": 2,
        },
        "cursor_dispatches": {"dispatch_ids": []},
        "active_ops": [],
    }
    with patch(
        "scripts.model_manager.ui.controller.charter_runner.gate_admission_defer.fetch_giw_active_work_payload",
        new=AsyncMock(return_value=payload),
    ):
        result = await preflight_write_lease(root_id="6186")

    assert result.outcome == "escalate"
    assert result.escalation_reason == "orphan_holder_no_live_backing"
    assert result.queue_depth == 2


@pytest.mark.offline
@pytest.mark.asyncio
async def test_ac3_fire_and_pointer_defers_without_queued_row(
    monkeypatch: pytest.MonkeyPatch,
    events_log: list[tuple[str, dict[str, Any]]],
    tmp_path: Path,
) -> None:
    """AC3: gate held ⇒ BLOCKED/DEFERRED, never silently queued."""
    caps = CapStore(intent_dir=tmp_path / "intent")
    fire = AsyncMock()
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.window_exec.dispatch.dispatch_client.fire_window",
        fire,
    )
    payload = {
        "write_lease": {
            "holder_dispatch_id": "cursor-auto-holder-25956",
            "holder_started_at": "2026-07-28T12:00:00+00:00",
            "queue_depth": 0,
        },
        "cursor_dispatches": {"dispatch_ids": ["cursor-auto-holder-25956"]},
        "active_ops": [{"op_id": "cursor-auto-holder-25956"}],
    }
    with patch(
        "scripts.model_manager.ui.controller.charter_runner.gate_admission_defer.fetch_giw_active_work_payload",
        new=AsyncMock(return_value=payload),
    ):
        ok = await dispatch._fire_and_pointer(
            root_id="6186",
            window_index=3,
            packet="packet",
            subject="subject",
            caps=caps,
            workspace_root=tmp_path,
            admission_mode="generate",
            consult_role=None,
            implement_source_ref=None,
            on_admit=None,
            is_implement=False,
        )

    assert ok.admitted is False
    fire.assert_not_called()
    assert not caps.has_admit_intent("6186", 3)
    signals = [s for s, _ in events_log]
    assert "manage.charter.tick.admission_deferred_gate_held" in signals
    held = next(p for s, p in events_log if s.endswith("admission_deferred_gate_held"))
    assert held["holder_dispatch_id"] == "cursor-auto-holder-25956"
    assert held["defer_count"] >= 1


def _http_error(status: int, *, body: str) -> httpx.HTTPStatusError:
    req = httpx.Request("POST", "http://test/api/v1/team/dispatch")
    resp = httpx.Response(status_code=status, text=body, request=req)
    return httpx.HTTPStatusError("upstream failure", request=req, response=resp)


@pytest.mark.offline
@pytest.mark.asyncio
async def test_giw_draining_503_defers_without_cap_stop(
    monkeypatch: pytest.MonkeyPatch,
    events_log: list[tuple[str, dict[str, Any]]],
    tmp_path: Path,
) -> None:
    """503 GIT_WORKER_DRAINING is transient — defer, never CapStore stop."""
    caps = CapStore(intent_dir=tmp_path / "intent")

    async def fire_fail(*_a: object, **_k: object) -> dict[str, str]:
        raise _http_error(
            503,
            body='{"code":"GIT_WORKER_DRAINING","detail":"git-integration-worker is draining (epoch=1)"}',
        )

    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.window_exec.dispatch.dispatch_client.fire_window",
        fire_fail,
    )
    with patch(
        "scripts.model_manager.ui.controller.charter_runner.gate_admission_defer.fetch_giw_active_work_payload",
        new=AsyncMock(return_value={"write_lease": {}}),
    ):
        ok = await dispatch._fire_and_pointer(
            root_id="6237",
            window_index=16,
            packet="packet",
            subject="subject",
            caps=caps,
            workspace_root=tmp_path,
            admission_mode="generate",
            consult_role=None,
            implement_source_ref=None,
            on_admit=None,
            is_implement=False,
        )

    assert ok.admitted is False
    assert ok.fire_attempt_outcome.value == "deferred_legal"
    assert ok.fire_attempt_reason == "giw_draining"
    allowed, reason = caps.check("6237")
    assert allowed is True
    assert reason is None
    assert not caps.has_admit_intent("6237", 16)
    signals = [s for s, _ in events_log]
    assert "manage.charter.tick.admission_deferred_gate_held" in signals


@pytest.mark.offline
@pytest.mark.asyncio
async def test_ac6_g1_event_on_kernel_defer_path(
    events_log: list[tuple[str, dict[str, Any]]],
    tmp_path: Path,
) -> None:
    caps = CapStore(intent_dir=tmp_path / "intent")
    with patch(
        "scripts.model_manager.ui.controller.charter_runner.gate_admission_defer.fetch_giw_active_work_payload",
        new=AsyncMock(
            return_value={
                "write_lease": {
                    "holder_dispatch_id": "h1",
                    "holder_started_at": "2026-07-28T12:00:00+00:00",
                },
                "cursor_dispatches": {"dispatch_ids": ["h1"]},
                "active_ops": [{"op_id": "h1"}],
            }
        ),
    ):
        ok = await dispatch._fire_and_pointer(
            root_id="5975",
            window_index=1,
            packet="p",
            subject="s",
            caps=caps,
            workspace_root=tmp_path,
            admission_mode="autonomous",
            consult_role=None,
            implement_source_ref=None,
            on_admit=None,
            is_implement=False,
        )
    assert ok.admitted is False
    assert any(
        s == "manage.charter.tick.admission_deferred_gate_held" for s, _ in events_log
    )


@pytest.mark.offline
@pytest.mark.asyncio
async def test_ac6_g2_escalation_event_and_stopped_root(
    events_log: list[tuple[str, dict[str, Any]]],
    tmp_path: Path,
) -> None:
    caps = CapStore(intent_dir=tmp_path / "intent")
    record_gate_defer("5975", now=__import__("time").time() - DEFER_MAX_AGE_S - 5)
    with patch(
        "scripts.model_manager.ui.controller.charter_runner.gate_admission_defer.fetch_giw_active_work_payload",
        new=AsyncMock(
            return_value={
                "write_lease": {
                    "holder_dispatch_id": "h-old",
                    "holder_started_at": "2026-07-28T06:00:00+00:00",
                },
                "cursor_dispatches": {"dispatch_ids": ["h-old"]},
                "active_ops": [{"op_id": "h-old"}],
            }
        ),
    ):
        ok = await dispatch._fire_and_pointer(
            root_id="5975",
            window_index=2,
            packet="p",
            subject="s",
            caps=caps,
            workspace_root=tmp_path,
            admission_mode="generate",
            consult_role=None,
            implement_source_ref=None,
            on_admit=None,
            is_implement=False,
        )
    assert ok.admitted is False
    allowed, reason = caps.check("5975")
    assert not allowed
    assert reason == "stopped:gate_defer_escalated:defer_age_exceeded"
    assert any(
        s == "manage.charter.tick.admission_defer_escalated" for s, _ in events_log
    )


@pytest.mark.offline
@pytest.mark.asyncio
async def test_clear_gate_defer_on_successful_admit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    record_gate_defer("6186")
    caps = CapStore(intent_dir=tmp_path / "intent")

    async def _fire_ok(*_a: object, **_k: object) -> dict[str, str]:
        return {
            "thread_id": "agent-bus:worker-1",
            "dispatch_id": "disp-ok",
            "packet_path": "tmp/p.md",
            "executor": {"model": "cursor/grok-4.5", "contract": "light-bounded"},
        }

    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.window_exec.dispatch.dispatch_client.fire_window",
        _fire_ok,
    )
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.window_exec.dispatch.bus_client.post_admission_pointer",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.window_exec.dispatch.events.emit_manage_charter_tick_admitted",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.window_exec.dispatch.window_log.append_admit",
        lambda **_k: None,
    )
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.window_exec.dispatch.window_log.append_executor_note",
        lambda *_a, **_k: None,
    )
    with patch(
        "scripts.model_manager.ui.controller.charter_runner.gate_admission_defer.fetch_giw_active_work_payload",
        new=AsyncMock(return_value={"write_lease": {}}),
    ):
        ok = await dispatch._fire_and_pointer(
            root_id="6186",
            window_index=1,
            packet="p",
            subject="s",
            caps=caps,
            workspace_root=tmp_path,
            admission_mode="generate",
            consult_role=None,
            implement_source_ref=None,
            on_admit=None,
            is_implement=False,
        )
    assert ok.admitted is True
    from scripts.model_manager.ui.controller.charter_runner.gate_admission_defer import (
        gate_defer_count,
    )

    assert gate_defer_count("6186") == 0
