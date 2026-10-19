"""Admit provenance flags for team_dispatch generate / to_thread (friction:39669)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest

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
from .route import TeamDispatchGenerateBody


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
    assert provenance_missing_marker(missing) == "PROVENANCE_MISSING:caller_seat,work_key"
    body = append_provenance_marker("hello", missing)
    assert body.startswith("hello\nPROVENANCE_MISSING:")
    assert admit_turn_from_agent(None, provenance_missing=missing) == MISSING_CALLER_FROM_AGENT
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
        lambda *a, **k: _client_ctx(post_turn),
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
        lambda *a, **k: _client_ctx(post_turn),
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


class _client_ctx:
    def __init__(self, post_turn: AsyncMock) -> None:
        self._post_turn = post_turn

    async def __aenter__(self) -> _client_ctx:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def post(self, path: str, **kwargs: object) -> object:
        if path == "/turns":
            await self._post_turn(path, kwargs.get("json"))
        return type("Resp", (), {"status_code": 200})()
