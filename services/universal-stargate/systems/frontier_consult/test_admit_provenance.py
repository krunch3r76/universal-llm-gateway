"""Admit provenance flags for team_dispatch generate / to_thread (friction:39669)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import Response
from pipelines.cursor_paste_resolve.v1.handlers._message import cursor_sdk_dispatch_body

from .admit_provenance import (
    MISSING_CALLER_FROM_AGENT,
    AdmitProvenanceContext,
    admit_turn_from_agent,
    append_provenance_marker,
    build_team_dispatch_admit_provenance,
    missing_admit_provenance,
    provenance_missing_marker,
)
from .cursor_sdk_coord_notify import post_coord_admit_pointer
from .route import (
    FrontierDispatchGenerateBody,
    TeamDispatchGenerateBody,
    frontier_dispatch,
    team_dispatch,
)


def test_missing_admit_provenance_empty() -> None:
    assert missing_admit_provenance("cursor", "16416", "todo:x") == ()


def test_missing_admit_provenance_one_field() -> None:
    assert missing_admit_provenance(None, "16416", "todo:x") == ("caller_seat",)


def test_missing_admit_provenance_all_three() -> None:
    assert missing_admit_provenance("  ", None, "") == (
        "caller_seat",
        "lane_or_thread",
        "work_key",
    )


def test_provenance_marker_and_from_agent_when_flagged() -> None:
    missing = ("caller_seat", "work_key")
    assert (
        provenance_missing_marker(missing) == "PROVENANCE_MISSING:caller_seat,work_key"
    )
    body = append_provenance_marker("hello", missing)
    assert body.startswith("hello\nPROVENANCE_MISSING:")
    assert (
        admit_turn_from_agent(None, provenance_missing=missing)
        == MISSING_CALLER_FROM_AGENT
    )
    assert admit_turn_from_agent("lead", provenance_missing=missing) == "lead"


def test_full_provenance_keeps_dispatch_fallback() -> None:
    assert admit_turn_from_agent(None, provenance_missing=()) == "dispatch"
    assert provenance_missing_marker(()) is None


@pytest.mark.asyncio
async def test_coord_admit_missing_caller_posts_marker_not_dispatch_from(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    post_turn = AsyncMock()
    published: list[str] = []

    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_coord_notify.make_async_client",
        lambda *a, **k: _ClientCtx(post_turn),
    )
    monkeypatch.setenv("AGENT_BUS_TOKEN", "test-token")
    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_coord_notify.publish_frontier_event",
        lambda event: published.append(getattr(event, "signal", "")),
    )

    ctx = AdmitProvenanceContext(
        missing=("caller_seat",),
        caller_seat=None,
        lane_or_thread="16416",
        work_key="todo:job",
        coord_thread="16416",
        root_lane=True,
        request_id="req-39669",
    )
    await post_coord_admit_pointer(
        coord_thread_id="16416",
        worker_thread_id="18185",
        to_agent="dispatch",
        caller_agent=None,
        contract="implement",
        admit_provenance=ctx,
    )
    post_turn.assert_awaited_once()
    payload = post_turn.await_args.args[1]
    assert payload["from"] == MISSING_CALLER_FROM_AGENT
    assert payload["body"].splitlines()[-1].startswith("PROVENANCE_MISSING:")


@pytest.mark.asyncio
async def test_coord_admit_full_provenance_no_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    post_turn = AsyncMock()
    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_coord_notify.make_async_client",
        lambda *a, **k: _ClientCtx(post_turn),
    )
    monkeypatch.setenv("AGENT_BUS_TOKEN", "test-token")
    await post_coord_admit_pointer(
        coord_thread_id="16416",
        worker_thread_id="18185",
        to_agent="claude-web",
        caller_agent="claude-web",
        contract="implement",
    )
    payload = post_turn.await_args.args[1]
    assert payload["from"] == "claude-web"
    assert "PROVENANCE_MISSING" not in payload["body"]


@pytest.mark.asyncio
async def test_coord_admit_bus_http_error_swallowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FailingClient:
        async def __aenter__(self) -> _FailingClient:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def post(self, path: str, **kwargs: object) -> object:
            raise httpx.HTTPError("bus down")

    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_coord_notify.make_async_client",
        lambda *a, **k: _FailingClient(),
    )
    monkeypatch.setenv("AGENT_BUS_TOKEN", "test-token")
    await post_coord_admit_pointer(
        coord_thread_id="16416",
        worker_thread_id="18185",
        to_agent="dispatch",
        caller_agent="cursor-auto",
        contract="implement",
    )


def test_build_team_dispatch_provenance_root_lane() -> None:
    body = TeamDispatchGenerateBody(
        op="generate",
        seat="cursor-sdk",
        model="cursor/composer-2.5",
        lane="B",
        job="implement",
        dispatch_thread_id="16416",
        caller_agent=None,
        work_key="todo:39669",
    )
    ctx = build_team_dispatch_admit_provenance(body, request_id="req-t")
    assert ctx is not None
    assert ctx.missing == ("caller_seat",)
    assert ctx.root_lane is True


def test_cursor_sdk_dispatch_body_stamps_pipeline_caller() -> None:
    body = cursor_sdk_dispatch_body(
        kind="friction",
        assertion_id=39669,
        prompt="probe",
        dispatch_thread_id="16416",
    )
    assert body["caller_agent"] == "pipeline:cursor-paste-resolve"


def _root_lane_body() -> TeamDispatchGenerateBody:
    return TeamDispatchGenerateBody(
        op="generate",
        seat="cursor-sdk",
        lane="B",
        job="freeform",
        model="cursor/composer-2.5",
        dispatch_thread_id="16416",
        caller_agent=None,
        work_key="todo:39669",
        prompt="probe",
    )


def _stub_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    proxy = MagicMock()
    proxy.event_bus = None
    monkeypatch.setattr(
        "systems.proxy.dependencies.get_proxy",
        lambda: proxy,
    )


def _record_events(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    published: list[object] = []

    def _publish(event: object) -> None:
        published.append(event)

    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_generate_signals.publish_frontier_event",
        _publish,
    )
    return published


@pytest.mark.asyncio
async def test_team_dispatch_root_lane_no_caller_flags_and_admits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    published = _record_events(monkeypatch)
    _stub_proxy(monkeypatch)
    monkeypatch.setattr(
        "systems.frontier_consult.generate_wrap.dispatch_cursor_sdk_generate",
        AsyncMock(return_value={"status": "admitted", "execution_id": "exec-root"}),
    )
    response = Response()
    result = await team_dispatch(_root_lane_body(), response)
    assert isinstance(result, dict)
    assert response.status_code == 202
    assert result["status"] == "admitted"
    assert result["execution_id"] == "exec-root"
    assert "caller_seat" in result["provenance_missing"]
    missing_events = [
        event
        for event in published
        if getattr(event, "signal", "") == "dispatch.admit.provenance_missing"
    ]
    assert missing_events
    payload = missing_events[0].payload
    assert payload["root_lane"] is True
    assert "caller_seat" in payload["missing"]


@pytest.mark.asyncio
async def test_team_dispatch_event_emit_raises_still_202(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(_event: object) -> None:
        raise RuntimeError("event bus down")

    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_generate_signals.publish_frontier_event",
        _boom,
    )
    _stub_proxy(monkeypatch)
    monkeypatch.setattr(
        "systems.frontier_consult.generate_wrap.dispatch_cursor_sdk_generate",
        AsyncMock(return_value={"status": "admitted", "execution_id": "exec-emit"}),
    )
    response = Response()
    result = await team_dispatch(_root_lane_body(), response)
    assert isinstance(result, dict)
    assert response.status_code == 202
    assert result["status"] == "admitted"
    assert "caller_seat" in result["provenance_missing"]


def _patch_sdk_admit_io(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _thread(**_kwargs: object) -> str:
        return "thread-worker"

    async def _targets(**kwargs: object) -> tuple[None, object, bool]:
        return None, kwargs.get("dispatch_thread_id"), False

    async def _worker(**_kwargs: object) -> tuple[bool, dict[str, object]]:
        return True, {"status_code": 200}

    async def _continuity(_thread_id: str | None) -> None:
        return None

    monkeypatch.setattr(
        "systems.frontier_consult.generate_wrap.resolve_cursor_sdk_thread_targets",
        _targets,
    )
    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_generate_prepare.create_handoff_thread",
        _thread,
    )
    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_generate_prepare.admit_handoff_dispatch",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_generate_prepare.probe_continuity_root_thread_id",
        _continuity,
    )
    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_generate.dispatch_cursor_sdk_worker_message",
        _worker,
    )
    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_generate.emit_sdk_worker_outcome",
        lambda **_k: None,
    )


@pytest.mark.asyncio
async def test_team_dispatch_store_write_raises_still_202(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    published = _record_events(monkeypatch)
    _stub_proxy(monkeypatch)
    _patch_sdk_admit_io(monkeypatch)

    def _write(**_kwargs: object) -> None:
        raise RuntimeError("sqlite locked")

    monkeypatch.setattr(
        "systems.frontier_consult.generate_admission_context_store.write_admission_context",
        _write,
    )
    response = Response()
    result = await team_dispatch(_root_lane_body(), response)
    assert isinstance(result, dict)
    assert response.status_code == 202
    assert result.get("error") is None
    assert result["provenance_record_failed"] is True
    assert "caller_seat" in result["provenance_missing"]
    failed = [
        event
        for event in published
        if getattr(event, "payload", {}).get("provenance_record_failed") is True
    ]
    assert failed


class _RaisingBus:
    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    async def __aenter__(self) -> _RaisingBus:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def post(self, _path: str, **_kwargs: object) -> object:
        raise self._exc


async def _flagged_bus_failure_still_202(
    monkeypatch: pytest.MonkeyPatch, exc: BaseException
) -> None:
    _stub_proxy(monkeypatch)
    _patch_sdk_admit_io(monkeypatch)
    monkeypatch.setenv("AGENT_BUS_TOKEN", "test-token")
    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_coord_notify.make_async_client",
        lambda *_a, **_k: _RaisingBus(exc),
    )
    response = Response()
    result = await team_dispatch(_root_lane_body(), response)
    assert isinstance(result, dict)
    assert response.status_code == 202
    assert "caller_seat" in result["provenance_missing"]


@pytest.mark.asyncio
async def test_flagged_bus_post_http_error_still_202(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _flagged_bus_failure_still_202(monkeypatch, httpx.HTTPError("bus down"))


@pytest.mark.asyncio
async def test_flagged_bus_post_generic_error_still_202(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _flagged_bus_failure_still_202(monkeypatch, RuntimeError("bus exploded"))


@pytest.mark.asyncio
async def test_frontier_dispatch_missing_caller_flags_202(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    published = _record_events(monkeypatch)

    async def _ok(_req: object, response: Response) -> dict[str, str]:
        response.status_code = 202
        return {"status": "admitted", "execution_id": "exec-frontier"}

    monkeypatch.setattr(
        "systems.frontier_consult.route._dispatch",
        _ok,
    )
    response = Response()
    result = await frontier_dispatch(
        FrontierDispatchGenerateBody(
            op="generate",
            model="cursor/composer-2.5",
            messages=[{"role": "user", "content": "probe"}],
        ),
        response,
    )
    assert isinstance(result, dict)
    assert response.status_code == 202
    assert result["status"] == "admitted"
    assert "caller_seat" in result["provenance_missing"]
    assert "work_key" in result["provenance_missing"]
    assert "lane_or_thread" in result["provenance_missing"]
    missing_events = [
        event
        for event in published
        if getattr(event, "signal", "") == "dispatch.admit.provenance_missing"
    ]
    assert missing_events
    assert missing_events[0].payload["root_lane"] is False


class _ClientCtx:
    def __init__(self, post_turn: AsyncMock) -> None:
        self._post_turn = post_turn

    async def __aenter__(self) -> _ClientCtx:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def post(self, path: str, **kwargs: object) -> object:
        if path == "/turns":
            await self._post_turn(path, kwargs.get("json"))
        return type("Resp", (), {"status_code": 200})()
