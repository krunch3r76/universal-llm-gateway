"""Spawn recovery: concurrency, cancel, hide readback, unknown steer."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from cursor_bridge.grok_agent_ops import (
    CdpPreSendError,
    retire_grok_agent_async,
    spawn_grok_agent_async,
)
from cursor_bridge.grok_spawn_idempotency import clear_spawn_cache_for_tests

pytestmark = pytest.mark.offline


@pytest.fixture(autouse=True)
def _clear() -> None:
    clear_spawn_cache_for_tests()


def _eval_factory(store: dict[str, Any]) -> Any:
    async def _eval(_display: object, expr: str, **_kw: Any) -> dict[str, Any]:
        if "createAgent" in expr:
            store["creates"] = int(store.get("creates") or 0) + 1
            if store.get("hold_create") is not None:
                await store["hold_create"].wait()
            return {"ok": True, "created": True, "agent_id": "agent-1"}
        if "setAgentHiddenFromSidebar" in expr:
            store["hides"] = int(store.get("hides") or 0) + 1
            hidden = bool(store.get("hidden", True))
            return {
                "ok": hidden,
                "hidden": hidden,
                "code": "hidden" if hidden else "hide_unverified",
            }
        return {"ok": True, "currentAgentId": "agent-1"}

    return _eval


@pytest.mark.asyncio
async def test_concurrent_fresh_calls_one_create() -> None:
    store: dict[str, Any] = {"hold_create": asyncio.Event()}

    async def _release() -> None:
        await asyncio.sleep(0.05)
        store["hold_create"].set()

    with patch(
        "cursor_bridge.grok_agent_ops._eval_display", side_effect=_eval_factory(store)
    ):
        asyncio.get_running_loop().create_task(_release())
        first, second = await asyncio.gather(
            spawn_grok_agent_async("9", "bot", idempotency_key="race", hidden=True),
            spawn_grok_agent_async("9", "bot", idempotency_key="race", hidden=True),
        )
    outcomes = (first, second)
    assert store["creates"] == 1
    assert sum(item.get("ok") is True for item in outcomes) == 1
    assert sum(item.get("code") == "idempotency_in_flight" for item in outcomes) == 1


@pytest.mark.asyncio
async def test_hide_failure_retries_same_agent_without_create() -> None:
    store: dict[str, Any] = {"hidden": False}

    with patch(
        "cursor_bridge.grok_agent_ops._eval_display", side_effect=_eval_factory(store)
    ):
        first = await spawn_grok_agent_async(
            "9", "bot", idempotency_key="hide-1", hidden=True
        )
        store["hidden"] = True
        second = await spawn_grok_agent_async(
            "9", "bot", idempotency_key="hide-1", hidden=True
        )
    assert first["ok"] is False
    assert first["code"] == "hide_unverified"
    assert second["ok"] is True
    assert second["hidden"] is True
    assert store["creates"] == 1


@pytest.mark.asyncio
async def test_cancel_during_create_blocks_retry() -> None:
    async def _eval(_display: object, expr: str, **_kw: Any) -> dict[str, Any]:
        if "createAgent" in expr:
            raise asyncio.CancelledError()
        return {"ok": True}

    with patch("cursor_bridge.grok_agent_ops._eval_display", side_effect=_eval):
        with pytest.raises(asyncio.CancelledError):
            await spawn_grok_agent_async("9", "bot", idempotency_key="cancel-create")
    with patch(
        "cursor_bridge.grok_agent_ops._eval_display", new_callable=AsyncMock
    ) as ev:
        retry = await spawn_grok_agent_async(
            "9", "bot", idempotency_key="cancel-create"
        )
    assert retry["code"] == "create_unknown"
    ev.assert_not_awaited()


@pytest.mark.asyncio
async def test_known_not_sent_retries_steer_once() -> None:
    store: dict[str, Any] = {}
    steer = AsyncMock(
        side_effect=[
            {
                "ok": False,
                "code": "composer_not_empty",
                "delivered": False,
                "pasted": False,
            },
            {"ok": True, "delivered": True, "pasted": True},
        ]
    )
    with (
        patch(
            "cursor_bridge.grok_agent_ops._eval_display",
            side_effect=_eval_factory(store),
        ),
        patch("interlocutor.display_adapter.steer_display", steer),
    ):
        first = await spawn_grok_agent_async(
            "9", "bot", text="hi", idempotency_key="steer-ok"
        )
        second = await spawn_grok_agent_async(
            "9", "bot", text="hi", idempotency_key="steer-ok"
        )
    assert first["ok"] is False
    assert second["ok"] is True
    assert store["creates"] == 1
    assert steer.await_count == 2


@pytest.mark.asyncio
async def test_subscription_unavailable_before_create() -> None:
    with (
        patch(
            "cursor_bridge.grok_spawn_steps.preflight_subscriptions",
            return_value={"ok": False, "code": "subscription_unavailable"},
        ),
        patch(
            "cursor_bridge.grok_agent_ops._eval_display", new_callable=AsyncMock
        ) as ev,
    ):
        out = await spawn_grok_agent_async(
            "9", "bot", mailbox="box", thread_scope="t1", idempotency_key="sub-pre"
        )
    assert out["code"] == "subscription_unavailable"
    ev.assert_not_awaited()


def _unconfirmed_create(store: dict[str, Any], raw: Any) -> Any:
    async def _eval(_display: object, expr: str, **_kw: Any) -> Any:
        if "createAgent" in expr:
            store["creates"] = int(store.get("creates") or 0) + 1
            return raw
        return {"ok": True, "hidden": True, "currentAgentId": "agent-1"}

    return _eval


@pytest.mark.asyncio
async def test_create_agent_no_id_blocks_second_create() -> None:
    store: dict[str, Any] = {}
    raw = {"ok": False, "code": "create_agent_no_id", "created": False}
    with patch(
        "cursor_bridge.grok_agent_ops._eval_display",
        side_effect=_unconfirmed_create(store, raw),
    ):
        first = await spawn_grok_agent_async("9", "bot", idempotency_key="no-id")
    with patch(
        "cursor_bridge.grok_agent_ops._eval_display", new_callable=AsyncMock
    ) as ev:
        second = await spawn_grok_agent_async("9", "bot", idempotency_key="no-id")
    assert first["code"] == "create_unknown"
    assert first["needs_operator"] is True
    assert second["code"] == "create_unknown"
    assert store["creates"] == 1
    ev.assert_not_awaited()


@pytest.mark.asyncio
async def test_non_dict_create_blocks_second_create() -> None:
    store: dict[str, Any] = {}
    with patch(
        "cursor_bridge.grok_agent_ops._eval_display",
        side_effect=_unconfirmed_create(store, ["nope"]),
    ):
        first = await spawn_grok_agent_async("9", "bot", idempotency_key="non-dict")
    with patch(
        "cursor_bridge.grok_agent_ops._eval_display", new_callable=AsyncMock
    ) as ev:
        second = await spawn_grok_agent_async("9", "bot", idempotency_key="non-dict")
    assert first["code"] == "create_unknown"
    assert second["code"] == "create_unknown"
    assert store["creates"] == 1
    ev.assert_not_awaited()


@pytest.mark.asyncio
async def test_cdp_down_then_recovered_creates_once() -> None:
    store: dict[str, Any] = {"down": True}

    async def _eval(_display: object, expr: str, **_kw: Any) -> dict[str, Any]:
        if "createAgent" in expr and store.get("down"):
            store["down"] = False
            raise CdpPreSendError("down")
        return await _eval_factory(store)(_display, expr)

    with patch("cursor_bridge.grok_agent_ops._eval_display", side_effect=_eval):
        first = await spawn_grok_agent_async(
            "9", "bot", idempotency_key="down-1", hidden=True
        )
        second = await spawn_grok_agent_async(
            "9", "bot", idempotency_key="down-1", hidden=True
        )
    assert first["code"] == "cdp_unreachable"
    assert second["ok"] is True
    assert store["creates"] == 1


@pytest.mark.asyncio
async def test_ambiguous_eval_error_burns_key() -> None:
    async def _eval(_display: object, expr: str, **_kw: Any) -> dict[str, Any]:
        if "createAgent" in expr:
            raise OSError("reset")
        return {"ok": True}

    with patch("cursor_bridge.grok_agent_ops._eval_display", side_effect=_eval):
        first = await spawn_grok_agent_async("9", "bot", idempotency_key="ambig")
    with patch(
        "cursor_bridge.grok_agent_ops._eval_display", new_callable=AsyncMock
    ) as ev:
        second = await spawn_grok_agent_async("9", "bot", idempotency_key="ambig")
    assert first["code"] == "create_unknown"
    assert second["code"] == "create_unknown"
    ev.assert_not_awaited()


@pytest.mark.asyncio
async def test_concurrent_resumes_one_steer() -> None:
    store: dict[str, Any] = {}
    hold = asyncio.Event()
    steers = {"n": 0}

    async def _steer(*_a: object, **_k: object) -> dict[str, Any]:
        steers["n"] += 1
        if steers["n"] == 1:
            return {
                "ok": False,
                "code": "composer_not_empty",
                "delivered": False,
                "pasted": False,
            }
        await hold.wait()
        return {"ok": True, "delivered": True, "pasted": True}

    with (
        patch(
            "cursor_bridge.grok_agent_ops._eval_display",
            side_effect=_eval_factory(store),
        ),
        patch("interlocutor.display_adapter.steer_display", side_effect=_steer),
    ):
        opened = await spawn_grok_agent_async(
            "9", "bot", text="hi", idempotency_key="resume-race"
        )
        assert opened["code"] == "composer_not_empty"
        loser_task = asyncio.create_task(
            spawn_grok_agent_async("9", "bot", text="hi", idempotency_key="resume-race")
        )
        winner_task = asyncio.create_task(
            spawn_grok_agent_async("9", "bot", text="hi", idempotency_key="resume-race")
        )
        await asyncio.sleep(0.05)
        hold.set()
        loser, winner = await asyncio.gather(loser_task, winner_task)
    pair = (loser, winner)
    assert store["creates"] == 1
    assert steers["n"] == 2
    assert sum(item.get("ok") is True for item in pair) == 1
    assert sum(item.get("code") == "idempotency_in_flight" for item in pair) == 1


@pytest.mark.asyncio
async def test_steer_exception_does_not_resend() -> None:
    store: dict[str, Any] = {}
    steer = AsyncMock(side_effect=RuntimeError("boom"))
    with (
        patch(
            "cursor_bridge.grok_agent_ops._eval_display",
            side_effect=_eval_factory(store),
        ),
        patch("interlocutor.display_adapter.steer_display", steer),
    ):
        first = await spawn_grok_agent_async(
            "9", "bot", text="hi", idempotency_key="steer-exc"
        )
    later = AsyncMock(return_value={"ok": True, "delivered": True, "pasted": True})
    with patch("interlocutor.display_adapter.steer_display", later):
        second = await spawn_grok_agent_async(
            "9", "bot", text="hi", idempotency_key="steer-exc"
        )
    assert first["code"] == "initial_text_unconfirmed"
    assert second["code"] == "initial_text_unconfirmed"
    assert steer.await_count == 1
    later.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancel_mid_steer_does_not_resend() -> None:
    store: dict[str, Any] = {}

    async def _steer(*_a: object, **_k: object) -> dict[str, Any]:
        raise asyncio.CancelledError()

    with (
        patch(
            "cursor_bridge.grok_agent_ops._eval_display",
            side_effect=_eval_factory(store),
        ),
        patch("interlocutor.display_adapter.steer_display", side_effect=_steer),
    ):
        with pytest.raises(asyncio.CancelledError):
            await spawn_grok_agent_async(
                "9", "bot", text="hi", idempotency_key="mid-steer"
            )
    later = AsyncMock()
    with patch("interlocutor.display_adapter.steer_display", later):
        second = await spawn_grok_agent_async(
            "9", "bot", text="hi", idempotency_key="mid-steer"
        )
    assert second["code"] == "initial_text_unconfirmed"
    later.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancel_before_steer_resumes_once() -> None:
    store: dict[str, Any] = {}
    selects = {"n": 0}

    async def _sel(*_a: object, **_k: object) -> dict[str, Any]:
        selects["n"] += 1
        if selects["n"] == 1:
            raise asyncio.CancelledError()
        return {"ok": True, "currentAgentId": "agent-1"}

    steer = AsyncMock(return_value={"ok": True, "delivered": True, "pasted": True})
    with (
        patch(
            "cursor_bridge.grok_agent_ops._eval_display",
            side_effect=_eval_factory(store),
        ),
        patch("cursor_bridge.grok_agent_ops.select_grok_agent_async", side_effect=_sel),
        patch("interlocutor.display_adapter.steer_display", steer),
    ):
        with pytest.raises(asyncio.CancelledError):
            await spawn_grok_agent_async(
                "9", "bot", text="hi", idempotency_key="pre-steer"
            )
        second = await spawn_grok_agent_async(
            "9", "bot", text="hi", idempotency_key="pre-steer"
        )
    assert second["ok"] is True
    assert store["creates"] == 1
    assert steer.await_count == 1


@pytest.mark.asyncio
async def test_clicked_unknown_and_pasted_none_do_not_resend() -> None:
    store: dict[str, Any] = {}
    clicked = AsyncMock(
        return_value={
            "ok": False,
            "code": "clicked_unknown",
            "delivered": False,
            "pasted": False,
            "clicked_unknown": True,
        }
    )
    with (
        patch(
            "cursor_bridge.grok_agent_ops._eval_display",
            side_effect=_eval_factory(store),
        ),
        patch("interlocutor.display_adapter.steer_display", clicked),
    ):
        first = await spawn_grok_agent_async(
            "9", "bot", text="hi", idempotency_key="click-unk"
        )
    later = AsyncMock()
    with patch("interlocutor.display_adapter.steer_display", later):
        second = await spawn_grok_agent_async(
            "9", "bot", text="hi", idempotency_key="click-unk"
        )
    assert first["code"] == "initial_text_unconfirmed"
    assert second["code"] == "initial_text_unconfirmed"
    assert clicked.await_count == 1
    later.assert_not_awaited()

    clear_spawn_cache_for_tests()
    vague = AsyncMock(
        return_value={
            "ok": False,
            "code": "composer_not_empty",
            "delivered": False,
            "pasted": None,
        }
    )
    with (
        patch(
            "cursor_bridge.grok_agent_ops._eval_display",
            side_effect=_eval_factory(store),
        ),
        patch("interlocutor.display_adapter.steer_display", vague),
    ):
        third = await spawn_grok_agent_async(
            "9", "bot", text="hi", idempotency_key="pasted-none"
        )
    again = AsyncMock()
    with patch("interlocutor.display_adapter.steer_display", again):
        fourth = await spawn_grok_agent_async(
            "9", "bot", text="hi", idempotency_key="pasted-none"
        )
    assert third["code"] == "initial_text_unconfirmed"
    assert fourth["code"] == "initial_text_unconfirmed"
    assert vague.await_count == 1
    again.assert_not_awaited()


@pytest.mark.asyncio
async def test_preflight_transport_failure_creates_nothing() -> None:
    for exc in (FileNotFoundError("sock"), SystemExit("no token")):
        clear_spawn_cache_for_tests()
        with (
            patch("cursor_bridge.grok_agent_subscriptions._client", side_effect=exc),
            patch(
                "cursor_bridge.grok_agent_ops._eval_display", new_callable=AsyncMock
            ) as ev,
        ):
            out = await spawn_grok_agent_async(
                "9",
                "bot",
                mailbox="box",
                thread_scope="t1",
                idempotency_key=f"pre-{type(exc).__name__}",
            )
        assert out["code"] == "subscription_unavailable"
        ev.assert_not_awaited()


@pytest.mark.asyncio
async def test_subscribe_failure_resumes_without_second_create() -> None:
    store: dict[str, Any] = {}
    calls = {"n": 0}

    def _sub(**_k: object) -> dict[str, Any]:
        calls["n"] += 1
        if calls["n"] == 1:
            return {"ok": False, "code": "subscription_failed"}
        return {"ok": True, "status_code": 200}

    with (
        patch(
            "cursor_bridge.grok_spawn_steps.preflight_subscriptions",
            return_value={"ok": True},
        ),
        patch("cursor_bridge.grok_spawn_steps.subscribe_grok", side_effect=_sub),
        patch(
            "cursor_bridge.grok_agent_ops._eval_display",
            side_effect=_eval_factory(store),
        ),
    ):
        first = await spawn_grok_agent_async(
            "9", "bot", mailbox="box", thread_scope="t1", idempotency_key="sub-resume"
        )
        second = await spawn_grok_agent_async(
            "9", "bot", mailbox="box", thread_scope="t1", idempotency_key="sub-resume"
        )
    assert first["code"] == "subscription_failed"
    assert second["ok"] is True
    assert store["creates"] == 1
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_retire_partial_through_public_path() -> None:
    with (
        patch(
            "cursor_bridge.grok_agent_ops._eval_display",
            new_callable=AsyncMock,
            return_value={"ok": True, "name": "Bot (retired)", "hidden": True},
        ),
        patch(
            "cursor_bridge.grok_agent_ops.unsubscribe_grok",
            return_value={
                "ok": False,
                "code": "unsubscribe_partial",
                "unsubscribed": [{"agent": "box", "thread_scope": "t1"}],
                "unsubscribe_errors": [
                    {"code": "unsubscribe_failed", "agent": "other"}
                ],
            },
        ),
    ):
        out = await retire_grok_agent_async("9", "aid")
    assert out["ok"] is False
    assert out["code"] == "unsubscribe_partial"
    assert out["unsubscribed"]
    assert out["unsubscribe_errors"]
