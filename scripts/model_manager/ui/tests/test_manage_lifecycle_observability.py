"""Manage lifecycle attribution — window reason + manage.lifecycle.requested."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from scripts.model_manager.ui import manage_lifecycle_observability as mlo
from scripts.model_manager.ui.controller import restart_window_ctl as rwc
from scripts.model_manager.ui.controller.restart_window_store import RestartWindow


def test_manage_window_reason_includes_caller_reason() -> None:
    text = mlo.manage_window_reason(
        "sync_restart", {"reason": "land c33c6ec14: recycle cdp-ask"}
    )
    assert text == "manage sync_restart: land c33c6ec14: recycle cdp-ask"


def test_manage_window_reason_without_caller_reason() -> None:
    assert mlo.manage_window_reason("sync_restart", {}) == "manage sync_restart"


@pytest.mark.asyncio
async def test_record_manage_lifecycle_requested_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[dict[str, object]] = []

    async def _emit(**kwargs: object) -> None:
        captured.append(dict(kwargs))

    monkeypatch.setattr(mlo, "emit_manage_lifecycle_requested", _emit)
    await mlo.record_manage_lifecycle_requested(
        action="sync_restart",
        service="cdp_ask",
        reason="go-live",
        caller="dispatch-abc",
        caller_thread_id="23677",
        force=False,
        peer_pid=1234,
        peer_uid=1000,
    )
    assert captured[0]["action"] == "sync_restart"
    assert captured[0]["service"] == "cdp_ask"
    assert captured[0]["reason"] == "go-live"
    assert captured[0]["caller"] == "dispatch-abc"
    assert captured[0]["caller_thread_id"] == "23677"
    assert captured[0]["peer_pid"] == 1234


@pytest.mark.asyncio
async def test_record_manage_lifecycle_requested_swallows_emit_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _boom(**_kwargs: object) -> None:
        raise RuntimeError("event down")

    monkeypatch.setattr(mlo, "emit_manage_lifecycle_requested", _boom)
    await mlo.record_manage_lifecycle_requested(
        action="start",
        service="gateway",
        reason=None,
        caller=None,
        caller_thread_id=None,
        force=None,
        peer_pid=None,
        peer_uid=None,
    )


@pytest.mark.asyncio
async def test_lifecycle_wrapper_passes_caller_reason_to_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC1: bind request context so restart window.reason carries caller reason."""
    opened: list[str] = []

    async def _open(
        store: object, service: str, *, reason: str
    ) -> RestartWindow:
        opened.append(reason)
        now = "2026-01-01T00:00:00+00:00"
        return RestartWindow(
            window_id="w1",
            scope="service",
            service_set=[service],
            state="open",
            opened_at=now,
            deadline_at="2099-01-01T00:00:00+00:00",
            cleared_at=None,
            reason=reason,
            created_at=now,
            updated_at=now,
        )

    monkeypatch.setattr(rwc, "open_service_window", _open)
    monkeypatch.setattr(rwc, "clear_service_windows", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        rwc,
        "invoke_propagation_settle_for_service",
        AsyncMock(),
    )
    token = mlo.bind_manage_request_params(
        {"reason": "land c33c6ec14: recycle cdp-ask"}
    )
    store = MagicMock()
    try:

        async def _life() -> str:
            return "ok"

        await rwc.lifecycle_with_restart_window(
            store, "cdp_ask", "sync_restart", _life
        )
    finally:
        mlo.reset_manage_request_params(token)
    assert opened == ["manage sync_restart: land c33c6ec14: recycle cdp-ask"]


@pytest.mark.asyncio
async def test_execute_lifecycle_emit_failure_still_returns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Emit failure at entry must not alter the lifecycle handler response."""
    from scripts.model_manager.ui import api_dispatch

    ctl = MagicMock()

    async def _boom(**_kwargs: object) -> None:
        raise OSError("uds down")

    monkeypatch.setattr(mlo, "emit_manage_lifecycle_requested", _boom)

    async def _fake_dispatch(
        *_args: object, **_kwargs: object
    ) -> dict[str, object]:
        return {"status": "ok", "message": "noop"}

    monkeypatch.setattr(api_dispatch, "_execute_dispatch", _fake_dispatch)

    result = await api_dispatch.execute(
        ctl,
        "sync_restart",
        "cdp_ask",
        {"service": "cdp_ask", "reason": "test"},
        peer_pid=99,
        peer_uid=1000,
    )
    assert result["status"] == "ok"
