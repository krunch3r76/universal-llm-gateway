"""Unit tests for cursor-sdk generate admit-only coord-thread pointer."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from .admit_provenance import AdmitProvenanceContext
from .cursor_sdk_admit_loop import (
    admit_pointer_would_have_refused_total,
    reset_admit_pointer_would_have_refused_counter_for_tests,
)
from .cursor_sdk_coord_notify import post_coord_admit_pointer
from .handoff import post_pointer_turn


@pytest.fixture(autouse=True)
def _reset_counter() -> None:
    reset_admit_pointer_would_have_refused_counter_for_tests()


@pytest.mark.asyncio
async def test_coord_admit_skips_when_same_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    post_turn = AsyncMock()
    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_coord_notify.make_async_client",
        lambda *a, **k: _ClientCtx(post_turn),
    )
    await post_coord_admit_pointer(
        coord_thread_id="1959",
        worker_thread_id="1959",
        to_agent="claude-web",
        caller_agent="claude-web",
        contract="implement",
    )
    post_turn.assert_not_awaited()


@pytest.mark.asyncio
async def test_coord_admit_posts_when_threads_differ(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    post_turn = AsyncMock()
    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_coord_notify.make_async_client",
        lambda *a, **k: _ClientCtx(post_turn),
    )
    monkeypatch.setenv("AGENT_BUS_TOKEN", "test-token")
    await post_coord_admit_pointer(
        coord_thread_id="1959",
        worker_thread_id="1960",
        to_agent="claude-web",
        caller_agent="claude-web",
        contract="implement",
    )
    post_turn.assert_awaited_once()
    payload = post_turn.await_args.args[1]
    assert payload["thread"] == "1959"
    assert payload["subject"] == "cursor-sdk generate admitted"
    assert "poll_hint" in payload["body"]
    assert "1960" in payload["body"]
    assert "bound todo" in payload["body"]


@pytest.mark.asyncio
async def test_coord_admit_non_implement_omits_todo_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    post_turn = AsyncMock()
    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_coord_notify.make_async_client",
        lambda *a, **k: _ClientCtx(post_turn),
    )
    monkeypatch.setenv("AGENT_BUS_TOKEN", "test-token")
    await post_coord_admit_pointer(
        coord_thread_id="1959",
        worker_thread_id="1960",
        to_agent="claude-web",
        caller_agent="claude-web",
        contract="none",
    )
    payload = post_turn.await_args.args[1]
    assert "bound todo" not in payload["body"]


@pytest.mark.asyncio
async def test_a6655_loop_closure_refuses_and_does_not_post(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """B.3 — loop detector fires, admit does not post."""
    post_turn = AsyncMock()
    published: list[str] = []

    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_coord_notify.make_async_client",
        lambda *a, **k: _ClientCtx(post_turn),
    )
    monkeypatch.setenv("AGENT_BUS_TOKEN", "test-token")

    def _capture(event: object) -> None:
        published.append(getattr(event, "signal", ""))

    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_coord_notify.publish_frontier_event",
        _capture,
    )

    await post_coord_admit_pointer(
        coord_thread_id="7031",
        worker_thread_id="7032",
        to_agent="dispatch",
        caller_agent="cursor-auto",
        contract="implement",
        request_id="req-6655",
        execution_id="exec-6655",
        prompt_source_thread="7031",
        prompt_bind_mode="latest",
        prompt_turn_number=None,
        has_explicit_prompt_source=False,
    )
    post_turn.assert_not_awaited()
    assert "frontier.admit_pointer.loop_closure" in published
    assert admit_pointer_would_have_refused_total() == 1


_WORK_KEY = "decision:maestro-seat-cost-effectiveness"


@pytest.mark.asyncio
async def test_coord_admit_and_worker_turn1_stamp_work_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    post_turn = AsyncMock()
    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_coord_notify.make_async_client",
        lambda *a, **k: _ClientCtx(post_turn),
    )
    monkeypatch.setattr(
        "systems.frontier_consult.handoff.make_async_client",
        lambda *a, **k: _ClientCtx(post_turn),
    )
    monkeypatch.setenv("AGENT_BUS_TOKEN", "test-token")
    ctx = AdmitProvenanceContext(
        missing=(),
        caller_seat="cursor-auto",
        lane_or_thread="16416",
        work_key=_WORK_KEY,
        coord_thread="16416",
        root_lane=True,
        request_id="req-l3",
    )
    await post_coord_admit_pointer(
        coord_thread_id="16416",
        worker_thread_id="18418",
        to_agent="dispatch",
        caller_agent="cursor-auto",
        contract="implement",
        admit_provenance=ctx,
    )
    coord_payload = post_turn.await_args_list[0].args[1]
    assert f"work_key: {_WORK_KEY}" in coord_payload["body"]
    assert "PROVENANCE_MISSING:work_key" not in coord_payload["body"]

    post_turn.reset_mock()
    await post_pointer_turn(
        request_id="req-l3",
        thread_id="18418",
        to_agent="cursor-sdk:dispatch:exec",
        subject="cursor-sdk generate — exec",
        pointer_body="Worker thread opened.",
        caller_agent="cursor-auto",
        provenance_missing=(),
        work_key=_WORK_KEY,
    )
    worker_payload = post_turn.await_args.args[1]
    assert f"work_key: {_WORK_KEY}" in worker_payload["body"]
    assert "PROVENANCE_MISSING:work_key" not in worker_payload["body"]


@pytest.mark.asyncio
async def test_a6655_frozen_same_thread_silent_no_event(
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

    await post_coord_admit_pointer(
        coord_thread_id="7031",
        worker_thread_id="7032",
        to_agent="dispatch",
        caller_agent="cursor-auto",
        contract="implement",
        prompt_source_thread="7031",
        prompt_bind_mode="frozen_turn",
        prompt_turn_number=12,
        has_explicit_prompt_source=False,
    )
    post_turn.assert_awaited_once()
    assert published == []
    assert admit_pointer_would_have_refused_total() == 0


class _ClientCtx:
    def __init__(
        self,
        post_turn: AsyncMock,
        *,
        coord_subscriptions: list[dict] | None = None,
    ) -> None:
        self._post_turn = post_turn
        self._coord_subscriptions = list(coord_subscriptions or [])
        self.subscription_posts: list[dict] = []

    async def __aenter__(self) -> _ClientCtx:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def get(self, path: str, **kwargs: object) -> object:
        if path == "/endpoint-subscriptions":
            subs = self._coord_subscriptions

            class _Resp:
                status_code = 200

                def raise_for_status(self) -> None:
                    return None

                def json(self) -> dict:
                    return {"subscriptions": subs}

            return _Resp()
        raise AssertionError(f"unexpected GET {path}")

    async def post(self, path: str, **kwargs: object) -> object:
        if path == "/turns":
            await self._post_turn(path, kwargs.get("json"))
        elif path == "/endpoint-subscriptions":
            payload = kwargs.get("json")
            if isinstance(payload, dict):
                self.subscription_posts.append(payload)
        return type(
            "Resp",
            (),
            {
                "status_code": 200,
                "raise_for_status": lambda self: None,
                "json": lambda self: {"turn_number": 1},
            },
        )()


@pytest.mark.asyncio
async def test_coord_admit_mirrors_worker_scoped_subscription(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    post_turn = AsyncMock()
    coord_row = {
        "agent": "cursor",
        "thread_scope": "20995",
        "state": "subscribed",
        "surface": "cursor",
        "display": "orion-node",
        "title": "20995 impresario",
        "agent_id": "composer-impresario-dbbfc4c2",
    }
    ctx_holder: list[_ClientCtx] = []

    def _factory(*_a: object, **_k: object) -> _ClientCtx:
        ctx = _ClientCtx(post_turn, coord_subscriptions=[coord_row])
        ctx_holder.append(ctx)
        return ctx

    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_coord_notify.make_async_client",
        _factory,
    )
    monkeypatch.setenv("AGENT_BUS_TOKEN", "test-token")
    await post_coord_admit_pointer(
        coord_thread_id="20995",
        worker_thread_id="21015",
        to_agent="composer-impresario-dbbfc4c2",
        caller_agent="composer-impresario-dbbfc4c2",
        contract="implement",
    )
    post_turn.assert_awaited_once()
    posts = []
    for ctx in ctx_holder:
        posts.extend(ctx.subscription_posts)
    assert len(posts) == 1
    assert posts[0]["thread_scope"] == "21015"
    assert posts[0]["agent"] == "cursor"
    assert posts[0]["bound_by"] == "cursor-sdk-admit"
    assert posts[0]["evidence"] == "parent:20995"
    assert posts[0]["agent_id"] == coord_row["agent_id"]


@pytest.mark.asyncio
async def test_house_land_wake_budget_coord_admit_and_worker_closeout_qualify(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Admit posts once; subscription mirror; qualify budget on worker closeout path."""
    from bus_watch.endpoint_wake_dispatcher import qualify

    post_turn = AsyncMock()
    imp = "composer-impresario-dbbfc4c2"
    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_coord_notify.make_async_client",
        lambda *a, **k: _ClientCtx(
            post_turn,
            coord_subscriptions=[
                {
                    "agent": "cursor",
                    "thread_scope": "20995",
                    "state": "subscribed",
                    "surface": "cursor",
                    "display": "orion-node",
                    "title": "house",
                    "agent_id": imp,
                }
            ],
        ),
    )
    monkeypatch.setenv("AGENT_BUS_TOKEN", "test-token")
    await post_coord_admit_pointer(
        coord_thread_id="20995",
        worker_thread_id="21015",
        to_agent=imp,
        caller_agent=imp,
        contract="implement",
    )
    admit_verdict = qualify(
        {
            "to_agent": imp,
            "from_agent": imp,
            "subject": "cursor-sdk generate admitted",
            "thread": "20995",
        },
        cse=False,
        store=None,
    )
    assert admit_verdict.wake is False
    closeout_verdict = qualify(
        {
            "to_agent": imp,
            "from_agent": "cursor-sdk",
            "subject": "CLOSEOUT|cursor-sdk CLOSEOUT lane-21015 contract=implement",
            "thread": "21015",
        },
        cse=False,
        store=None,
    )
    assert closeout_verdict.wake is True
    review_verdict = qualify(
        {
            "to_agent": imp,
            "from_agent": "web-anthropic",
            "subject": "VERDICT|PASS",
            "thread": "20995",
        },
        cse=False,
        store=None,
    )
    wakes = sum(
        1
        for v in (admit_verdict, closeout_verdict, review_verdict)
        if v.wake
    )
    assert wakes <= 2
