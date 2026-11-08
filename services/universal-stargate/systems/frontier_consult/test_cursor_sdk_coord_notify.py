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

_MIRROR_SIGNAL = "frontier.worker_subscription_mirror"

_BUS_CLIENT_PATCH_TARGETS = (
    "systems.frontier_consult.cursor_sdk_coord_notify.make_async_client",
    "systems.frontier_consult.worker_subscription_mirror_event.make_async_client",
)


def _patch_bus_clients(monkeypatch: pytest.MonkeyPatch, factory: object) -> None:
    for target in _BUS_CLIENT_PATCH_TARGETS:
        monkeypatch.setattr(target, factory)


@pytest.fixture(autouse=True)
def _reset_counter() -> None:
    reset_admit_pointer_would_have_refused_counter_for_tests()


@pytest.mark.asyncio
async def test_coord_admit_skips_when_same_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    post_turn = AsyncMock()
    _patch_bus_clients(monkeypatch, lambda *a, **k: _ClientCtx(post_turn))
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
    _patch_bus_clients(monkeypatch, lambda *a, **k: _ClientCtx(post_turn))
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
    _patch_bus_clients(monkeypatch, lambda *a, **k: _ClientCtx(post_turn))
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

    _patch_bus_clients(monkeypatch, lambda *a, **k: _ClientCtx(post_turn))
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
    _patch_bus_clients(monkeypatch, lambda *a, **k: _ClientCtx(post_turn))
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
async def test_a6655_frozen_same_thread_admits_without_loop_closure_emits_mirror(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    post_turn = AsyncMock()
    published: list[str] = []

    _patch_bus_clients(monkeypatch, lambda *a, **k: _ClientCtx(post_turn))
    monkeypatch.setenv("AGENT_BUS_TOKEN", "test-token")
    def _capture(event: object) -> None:
        published.append(getattr(event, "signal", ""))

    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_coord_notify.publish_frontier_event",
        _capture,
    )
    monkeypatch.setattr(
        "systems.frontier_consult.worker_subscription_mirror_event.publish_frontier_event",
        _capture,
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
    assert "frontier.admit_pointer.loop_closure" not in published
    assert published == [_MIRROR_SIGNAL]
    assert admit_pointer_would_have_refused_total() == 0


def _mirror_event_payload(published: list[object]) -> dict:
    mirror = [
        e
        for e in published
        if getattr(e, "signal", "") == _MIRROR_SIGNAL
    ]
    assert len(mirror) == 1
    payload = getattr(mirror[0], "payload", {})
    assert isinstance(payload, dict)
    return payload


class _ClientCtx:
    def __init__(
        self,
        post_turn: AsyncMock,
        *,
        coord_subscriptions: list[dict] | None = None,
        subscription_list_invalid: bool = False,
        subscription_get_raises: bool = False,
        subscription_json_raises: bool = False,
        subscription_post_fail_agents: frozenset[str] = frozenset(),
    ) -> None:
        self._post_turn = post_turn
        self._coord_subscriptions = list(coord_subscriptions or [])
        self._subscription_list_invalid = subscription_list_invalid
        self._subscription_get_raises = subscription_get_raises
        self._subscription_json_raises = subscription_json_raises
        self._subscription_post_fail_agents = subscription_post_fail_agents
        self.subscription_posts: list[dict] = []

    async def __aenter__(self) -> _ClientCtx:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def get(self, path: str, **kwargs: object) -> object:
        if path == "/endpoint-subscriptions":
            if self._subscription_get_raises:
                import httpx

                raise httpx.HTTPError("get failed")
            subs = self._coord_subscriptions
            list_invalid = self._subscription_list_invalid
            json_raises = self._subscription_json_raises

            class _Resp:
                status_code = 200

                def raise_for_status(self) -> None:
                    return None

                def json(self) -> dict:
                    if list_invalid:
                        return {"subscriptions": "not-a-list"}
                    if json_raises:
                        raise ValueError("invalid json payload")
                    return {"subscriptions": subs}

            return _Resp()
        raise AssertionError(f"unexpected GET {path}")

    async def post(self, path: str, **kwargs: object) -> object:
        if path == "/turns":
            await self._post_turn(path, kwargs.get("json"))
        elif path == "/endpoint-subscriptions":
            payload = kwargs.get("json")
            if isinstance(payload, dict):
                agent = str(payload.get("agent") or "")
                if agent in self._subscription_post_fail_agents:
                    import httpx

                    raise httpx.HTTPError(f"post failed for {agent}")
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


def _two_coord_subscription_rows(
    *,
    parent_agent_id: str = "composer-impresario-dbbfc4c2",
) -> list[dict]:
    base = {
        "thread_scope": "20995",
        "state": "subscribed",
        "surface": "cursor",
        "display": "orion-node",
        "agent_id": parent_agent_id,
    }
    return [
        {**base, "agent": "cursor", "title": "20995 impresario"},
        {**base, "agent": "dispatch", "title": "20995 dispatch"},
    ]


@pytest.mark.asyncio
async def test_worker_subscription_mirror_outcome_mirrored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    post_turn = AsyncMock()
    coord_rows = _two_coord_subscription_rows()
    ctx_holder: list[_ClientCtx] = []
    resolve_calls = 0
    published: list[object] = []
    serving_uuid = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"

    def _factory(*_a: object, **_k: object) -> _ClientCtx:
        ctx = _ClientCtx(post_turn, coord_subscriptions=coord_rows)
        ctx_holder.append(ctx)
        return ctx

    _patch_bus_clients(monkeypatch, _factory)
    monkeypatch.setenv("AGENT_BUS_TOKEN", "test-token")
    monkeypatch.setattr(
        "systems.frontier_consult.worker_subscription_mirror_event.publish_frontier_event",
        lambda event: published.append(event),
    )

    async def _resolve(_client, *, worker_thread_id, serving_to_agent, headers):
        nonlocal resolve_calls
        resolve_calls += 1
        assert worker_thread_id == "21015"
        assert serving_to_agent == "composer-impresario-dbbfc4c2"
        return serving_uuid, f"serving_to_agent:{serving_uuid}"

    monkeypatch.setattr(
        "systems.frontier_consult.worker_subscription_mirror_event.resolve_worker_subscription_agent_id",
        _resolve,
    )
    await post_coord_admit_pointer(
        coord_thread_id="20995",
        worker_thread_id="21015",
        to_agent="composer-impresario-dbbfc4c2",
        caller_agent="composer-impresario-dbbfc4c2",
        contract="implement",
    )
    assert resolve_calls == 1
    posts = []
    for ctx in ctx_holder:
        posts.extend(ctx.subscription_posts)
    assert len(posts) == 2
    evidence = f"serving_to_agent:{serving_uuid}"
    for post in posts:
        assert post["agent_id"] == serving_uuid
        assert post["agent_id"] != coord_rows[0]["agent_id"]
        assert post["bound_by"] == "cursor-sdk-admit"
        assert post["evidence"] == evidence
        assert post["thread_scope"] == "21015"
    payload = _mirror_event_payload(published)
    assert payload["outcome"] == "mirrored"
    assert payload["eligible_agents"] == ["cursor", "dispatch"]
    assert payload["mirrored_agents"] == ["cursor", "dispatch"]
    assert payload["failed_agents"] == []
    assert payload["resolved_agent_id"] == serving_uuid
    assert payload["bind_evidence"] == evidence
    assert payload.get("error") is None


@pytest.mark.parametrize(
    "coord_parent_agent_id",
    [
        pytest.param("composer-impresario-dbbfc4c2", id="parent_set"),
        pytest.param("omit_key", id="parent_key_missing"),
        pytest.param(None, id="parent_none"),
    ],
)
@pytest.mark.asyncio
async def test_coord_admit_mirror_skips_unresolved_worker_agent_id(
    monkeypatch: pytest.MonkeyPatch,
    coord_parent_agent_id: str | None,
) -> None:
    post_turn = AsyncMock()
    coord_rows = _two_coord_subscription_rows()
    if coord_parent_agent_id == "omit_key":
        for row in coord_rows:
            row.pop("agent_id", None)
    elif coord_parent_agent_id is None:
        for row in coord_rows:
            row["agent_id"] = None
    resolve_calls = 0
    published: list[object] = []
    ctx_holder: list[_ClientCtx] = []

    def _factory(*_a: object, **_k: object) -> _ClientCtx:
        ctx = _ClientCtx(post_turn, coord_subscriptions=coord_rows)
        ctx_holder.append(ctx)
        return ctx

    _patch_bus_clients(monkeypatch, _factory)
    monkeypatch.setenv("AGENT_BUS_TOKEN", "test-token")
    monkeypatch.setattr(
        "systems.frontier_consult.worker_subscription_mirror_event.publish_frontier_event",
        lambda event: published.append(event),
    )

    async def _resolve_unresolved(
        _client, *, worker_thread_id, serving_to_agent, headers
    ):
        nonlocal resolve_calls
        resolve_calls += 1
        return None, "serving_to_agent:missing"

    monkeypatch.setattr(
        "systems.frontier_consult.worker_subscription_mirror_event.resolve_worker_subscription_agent_id",
        _resolve_unresolved,
    )
    await post_coord_admit_pointer(
        coord_thread_id="20995",
        worker_thread_id="21015",
        to_agent="composer-impresario-dbbfc4c2",
        caller_agent="composer-impresario-dbbfc4c2",
        contract="implement",
    )
    post_turn.assert_awaited_once()
    assert resolve_calls == 1
    posts: list[dict] = []
    for ctx in ctx_holder:
        posts.extend(ctx.subscription_posts)
    assert len(posts) == 0
    payload = _mirror_event_payload(published)
    assert payload["outcome"] == "skipped_unresolved"
    assert payload["bind_evidence"] == "serving_to_agent:missing"
    assert payload["resolved_agent_id"] is None


@pytest.mark.asyncio
async def test_worker_subscription_mirror_outcome_partial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    post_turn = AsyncMock()
    coord_rows = _two_coord_subscription_rows()
    resolve_calls = 0
    published: list[object] = []
    serving_uuid = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"

    _patch_bus_clients(
        monkeypatch,
        lambda *a, **k: _ClientCtx(
            post_turn,
            coord_subscriptions=coord_rows,
            subscription_post_fail_agents=frozenset({"dispatch"}),
        ),
    )
    monkeypatch.setenv("AGENT_BUS_TOKEN", "test-token")
    monkeypatch.setattr(
        "systems.frontier_consult.worker_subscription_mirror_event.publish_frontier_event",
        lambda event: published.append(event),
    )

    async def _resolve(_client, *, worker_thread_id, serving_to_agent, headers):
        nonlocal resolve_calls
        resolve_calls += 1
        return serving_uuid, f"serving_to_agent:{serving_uuid}"

    monkeypatch.setattr(
        "systems.frontier_consult.worker_subscription_mirror_event.resolve_worker_subscription_agent_id",
        _resolve,
    )
    await post_coord_admit_pointer(
        coord_thread_id="20995",
        worker_thread_id="21015",
        to_agent="composer-impresario-dbbfc4c2",
        caller_agent="composer-impresario-dbbfc4c2",
        contract="implement",
    )
    assert resolve_calls == 1
    evidence = f"serving_to_agent:{serving_uuid}"
    payload = _mirror_event_payload(published)
    assert payload["outcome"] == "partial"
    assert payload["eligible_agents"] == ["cursor", "dispatch"]
    assert payload["mirrored_agents"] == ["cursor"]
    assert payload["failed_agents"] == ["dispatch"]
    assert payload["resolved_agent_id"] == serving_uuid
    assert payload["bind_evidence"] == evidence
    assert payload["error"] is not None


@pytest.mark.asyncio
async def test_worker_subscription_mirror_outcome_no_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    post_turn = AsyncMock()
    resolve_calls = 0
    published: list[object] = []

    _patch_bus_clients(
        monkeypatch, lambda *a, **k: _ClientCtx(post_turn, coord_subscriptions=[])
    )
    monkeypatch.setenv("AGENT_BUS_TOKEN", "test-token")
    monkeypatch.setattr(
        "systems.frontier_consult.worker_subscription_mirror_event.publish_frontier_event",
        lambda event: published.append(event),
    )

    async def _must_not_resolve(*_a, **_k):
        nonlocal resolve_calls
        resolve_calls += 1
        raise AssertionError("resolver must not run")

    monkeypatch.setattr(
        "systems.frontier_consult.worker_subscription_mirror_event.resolve_worker_subscription_agent_id",
        _must_not_resolve,
    )

    await post_coord_admit_pointer(
        coord_thread_id="20995",
        worker_thread_id="21015",
        to_agent="composer-impresario-dbbfc4c2",
        caller_agent="composer-impresario-dbbfc4c2",
        contract="implement",
    )
    assert resolve_calls == 0
    payload = _mirror_event_payload(published)
    assert payload["outcome"] == "no_rows"
    assert payload["eligible_agents"] == []


@pytest.mark.asyncio
async def test_worker_subscription_mirror_outcome_fetch_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    post_turn = AsyncMock()
    resolve_calls = 0
    published: list[object] = []

    _patch_bus_clients(
        monkeypatch,
        lambda *a, **k: _ClientCtx(
            post_turn,
            coord_subscriptions=[],
            subscription_list_invalid=True,
        ),
    )
    monkeypatch.setenv("AGENT_BUS_TOKEN", "test-token")
    monkeypatch.setattr(
        "systems.frontier_consult.worker_subscription_mirror_event.publish_frontier_event",
        lambda event: published.append(event),
    )

    async def _must_not_resolve(*_a, **_k):
        nonlocal resolve_calls
        resolve_calls += 1
        raise AssertionError("resolver must not run")

    monkeypatch.setattr(
        "systems.frontier_consult.worker_subscription_mirror_event.resolve_worker_subscription_agent_id",
        _must_not_resolve,
    )

    await post_coord_admit_pointer(
        coord_thread_id="20995",
        worker_thread_id="21015",
        to_agent="composer-impresario-dbbfc4c2",
        caller_agent="composer-impresario-dbbfc4c2",
        contract="implement",
    )
    payload = _mirror_event_payload(published)
    assert payload["outcome"] == "fetch_failed"
    assert payload["error"] == "subscriptions payload not a list"


@pytest.mark.asyncio
async def test_worker_subscription_mirror_outcome_fetch_failed_subscription_get(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    post_turn = AsyncMock()
    resolve_calls = 0
    published: list[object] = []

    _patch_bus_clients(
        monkeypatch,
        lambda *a, **k: _ClientCtx(
            post_turn,
            coord_subscriptions=[],
            subscription_get_raises=True,
        ),
    )
    monkeypatch.setenv("AGENT_BUS_TOKEN", "test-token")
    monkeypatch.setattr(
        "systems.frontier_consult.worker_subscription_mirror_event.publish_frontier_event",
        lambda event: published.append(event),
    )

    async def _must_not_resolve(*_a, **_k):
        nonlocal resolve_calls
        resolve_calls += 1
        raise AssertionError("resolver must not run")

    monkeypatch.setattr(
        "systems.frontier_consult.worker_subscription_mirror_event.resolve_worker_subscription_agent_id",
        _must_not_resolve,
    )

    await post_coord_admit_pointer(
        coord_thread_id="20995",
        worker_thread_id="21015",
        to_agent="composer-impresario-dbbfc4c2",
        caller_agent="composer-impresario-dbbfc4c2",
        contract="implement",
    )
    assert resolve_calls == 0
    payload = _mirror_event_payload(published)
    assert payload["outcome"] == "fetch_failed"
    assert payload["error"] is not None


@pytest.mark.asyncio
async def test_worker_subscription_mirror_outcome_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    post_turn = AsyncMock()
    coord_rows = _two_coord_subscription_rows()
    resolve_calls = 0
    published: list[object] = []
    serving_uuid = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"

    _patch_bus_clients(
        monkeypatch,
        lambda *a, **k: _ClientCtx(
            post_turn,
            coord_subscriptions=coord_rows,
            subscription_post_fail_agents=frozenset({"cursor", "dispatch"}),
        ),
    )
    monkeypatch.setenv("AGENT_BUS_TOKEN", "test-token")
    monkeypatch.setattr(
        "systems.frontier_consult.worker_subscription_mirror_event.publish_frontier_event",
        lambda event: published.append(event),
    )

    async def _resolve(_client, *, worker_thread_id, serving_to_agent, headers):
        nonlocal resolve_calls
        resolve_calls += 1
        return serving_uuid, f"serving_to_agent:{serving_uuid}"

    monkeypatch.setattr(
        "systems.frontier_consult.worker_subscription_mirror_event.resolve_worker_subscription_agent_id",
        _resolve,
    )
    await post_coord_admit_pointer(
        coord_thread_id="20995",
        worker_thread_id="21015",
        to_agent="composer-impresario-dbbfc4c2",
        caller_agent="composer-impresario-dbbfc4c2",
        contract="implement",
    )
    assert resolve_calls == 1
    payload = _mirror_event_payload(published)
    assert payload["outcome"] == "failed"
    assert payload["failed_agents"] == ["cursor", "dispatch"]
    assert payload["mirrored_agents"] == []
    assert payload["error"] is not None


@pytest.mark.asyncio
async def test_worker_subscription_mirror_resolver_error_skipped_unresolved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    post_turn = AsyncMock()
    coord_rows = _two_coord_subscription_rows()
    resolve_calls = 0
    published: list[object] = []
    ctx_holder: list[_ClientCtx] = []

    def _factory(*_a: object, **_k: object) -> _ClientCtx:
        ctx = _ClientCtx(post_turn, coord_subscriptions=coord_rows)
        ctx_holder.append(ctx)
        return ctx

    _patch_bus_clients(monkeypatch, _factory)
    monkeypatch.setenv("AGENT_BUS_TOKEN", "test-token")
    monkeypatch.setattr(
        "systems.frontier_consult.worker_subscription_mirror_event.publish_frontier_event",
        lambda event: published.append(event),
    )

    async def _resolve_raises(*_a, **_k):
        nonlocal resolve_calls
        resolve_calls += 1
        import httpx

        raise httpx.HTTPError("resolver down")

    monkeypatch.setattr(
        "systems.frontier_consult.worker_subscription_mirror_event.resolve_worker_subscription_agent_id",
        _resolve_raises,
    )
    await post_coord_admit_pointer(
        coord_thread_id="20995",
        worker_thread_id="21015",
        to_agent="composer-impresario-dbbfc4c2",
        caller_agent="composer-impresario-dbbfc4c2",
        contract="implement",
    )
    assert resolve_calls == 1
    posts: list[dict] = []
    for ctx in ctx_holder:
        posts.extend(ctx.subscription_posts)
    assert len(posts) == 0
    payload = _mirror_event_payload(published)
    assert payload["outcome"] == "skipped_unresolved"
    assert payload["bind_evidence"] == "unresolved:resolver_error"
    assert payload["resolved_agent_id"] is None


@pytest.mark.asyncio
async def test_worker_subscription_mirror_json_value_error_one_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    post_turn = AsyncMock()
    resolve_calls = 0
    published: list[object] = []

    _patch_bus_clients(
        monkeypatch,
        lambda *a, **k: _ClientCtx(
            post_turn,
            coord_subscriptions=[],
            subscription_json_raises=True,
        ),
    )
    monkeypatch.setenv("AGENT_BUS_TOKEN", "test-token")
    monkeypatch.setattr(
        "systems.frontier_consult.worker_subscription_mirror_event.publish_frontier_event",
        lambda event: published.append(event),
    )

    async def _must_not_resolve(*_a, **_k):
        nonlocal resolve_calls
        resolve_calls += 1
        raise AssertionError("resolver must not run")

    monkeypatch.setattr(
        "systems.frontier_consult.worker_subscription_mirror_event.resolve_worker_subscription_agent_id",
        _must_not_resolve,
    )
    await post_coord_admit_pointer(
        coord_thread_id="20995",
        worker_thread_id="21015",
        to_agent="composer-impresario-dbbfc4c2",
        caller_agent="composer-impresario-dbbfc4c2",
        contract="implement",
    )
    assert resolve_calls == 0
    payload = _mirror_event_payload(published)
    assert payload["outcome"] == "fetch_failed"
    assert "ValueError" in (payload.get("error") or "")


@pytest.mark.asyncio
async def test_worker_subscription_mirror_emission_failure_does_not_break_admit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    post_turn = AsyncMock()

    def _raise_publish(_event: object) -> None:
        raise RuntimeError("event bus down")

    _patch_bus_clients(
        monkeypatch, lambda *a, **k: _ClientCtx(post_turn, coord_subscriptions=[])
    )
    monkeypatch.setenv("AGENT_BUS_TOKEN", "test-token")
    monkeypatch.setattr(
        "systems.frontier_consult.worker_subscription_mirror_event.publish_frontier_event",
        _raise_publish,
    )
    await post_coord_admit_pointer(
        coord_thread_id="20995",
        worker_thread_id="21015",
        to_agent="composer-impresario-dbbfc4c2",
        caller_agent="composer-impresario-dbbfc4c2",
        contract="implement",
    )
    post_turn.assert_awaited_once()


@pytest.mark.asyncio
async def test_house_land_wake_budget_coord_admit_and_worker_closeout_qualify(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Admit posts once; subscription mirror; qualify budget on worker closeout path."""
    from bus_watch.endpoint_wake_dispatcher import qualify

    post_turn = AsyncMock()
    imp = "composer-impresario-dbbfc4c2"
    serving_uuid = "f907a88b-1111-2222-3333-444455556666"

    async def _resolve(_client, *, worker_thread_id, serving_to_agent, headers):
        return serving_uuid, f"serving_to_agent:{serving_uuid}"

    monkeypatch.setattr(
        "systems.frontier_consult.worker_subscription_mirror_event.resolve_worker_subscription_agent_id",
        _resolve,
    )
    _patch_bus_clients(
        monkeypatch,
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
