"""Click-fact seal for the operator paste send path.

Drives the real ``_submit_composer_send_button`` and ``dom_click_send`` against
a fake CDP socket. A click script that has been sent is a dispatched click even
when the page result says ``cdp_eval_timeout`` or ``send_button_error``. The
permit stamp timing out before that script is not a click, so Enter may run and
the result stays unsealed.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any
from unittest.mock import AsyncMock

import pytest
import websockets
from cursor_bridge.operator_paste import deliver_operator_paste

pytestmark = pytest.mark.offline

_EvalHandler = Callable[[str, dict[str, Any]], Awaitable[Any]]


class _FakeSocket:
    """One CDP connection. ``recv`` answers the last ``send``."""

    def __init__(self, handler: _EvalHandler) -> None:
        self._handler = handler
        self.sent: list[dict[str, Any]] = []

    async def send(self, data: str) -> None:
        self.sent.append(json.loads(data))

    async def recv(self) -> str:
        msg = self.sent[-1]
        expr = str((msg.get("params") or {}).get("expression") or "")
        body = await self._handler(expr, msg)
        if isinstance(body, str):
            return body
        return json.dumps({"id": msg["id"], "result": {"result": {"value": body}}})

    async def __aenter__(self) -> _FakeSocket:
        return self

    async def __aexit__(self, *_exc: object) -> bool:
        return False


class _FakeConnect:
    def __init__(self, handler: _EvalHandler) -> None:
        self._handler = handler
        self.sockets: list[_FakeSocket] = []

    def __call__(self, *_args: object, **_kwargs: object) -> _FakeSocket:
        sock = _FakeSocket(self._handler)
        self.sockets.append(sock)
        return sock


def _surface(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "state": "chat_list",
        "active_title": "Test Grok",
        "active_agent_id": "agent-1",
        "titles": ["Test Grok"],
        "title_counts": {"Test Grok": 1},
        "agent_ids": {"agent-1": "Test Grok"},
        "composer_draft": "",
        "generating": False,
        "recent_messages": [],
    }
    base.update(overrides)
    return base


def _click_script(expr: str) -> bool:
    return "findSendBtn" in expr or "btn.click()" in expr


def _install_page(
    monkeypatch: pytest.MonkeyPatch,
    handler: _EvalHandler,
) -> _FakeConnect:
    connect = _FakeConnect(handler)
    monkeypatch.setattr(websockets, "connect", connect)
    monkeypatch.setattr(
        "cursor_bridge.dom_composer_paste.install_operator_activity_probe",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.wait_for_operator_idle",
        AsyncMock(return_value={"ok": True, "idle": True}),
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.read_operator_busy",
        AsyncMock(return_value={"ok": True, "operator_active": False}),
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.list_pages",
        lambda _origin: [{"title": "Grok Bot", "web_socket_debugger_url": "ws://fake"}],
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.visible_sidebar_agent_ids",
        AsyncMock(return_value={"agent-1"}),
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste._insert_composer_text",
        AsyncMock(return_value={"ok": True}),
    )
    return connect


async def _deliver(*, text: str = "Operator relay test") -> dict[str, Any]:
    return await deliver_operator_paste(
        display="10",
        app="grok",
        agent_id="agent-1",
        text=text,
        grant="operator",
        authorization_source="agent-bus:21614#2",
        scope="test",
    )


def _handler(mode: str, state: dict[str, bool]) -> _EvalHandler:
    async def handle(expr: str, _msg: dict[str, Any]) -> Any:
        if _click_script(expr):
            state["click_sent"] = True
            if mode == "bad_json":
                return "NOT-JSON"
            if mode == "timeout_after_click":
                return {
                    "ok": False,
                    "reason": "cdp_eval_timeout",
                    "send_clicked": False,
                }
            if mode == "send_button_error":
                return {
                    "ok": False,
                    "reason": "send_button_error",
                    "send_clicked": False,
                }
            if mode in {
                "no_send_button",
                "no_composer",
                "composer_text_mismatch",
            }:
                return {
                    "ok": False,
                    "reason": mode,
                    "send_clicked": False,
                }
            return {"ok": True, "send_clicked": True}
        if mode == "permit_timeout":
            await asyncio.sleep(30)
        return {"ok": True}

    return handle


@pytest.mark.asyncio
async def test_timeout_result_after_click_script_is_sealed_and_skips_enter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break: a cdp_eval_timeout result after the click script was sent retries via Enter."""
    state = {"click_sent": False}
    _install_page(monkeypatch, _handler("timeout_after_click", state))
    enter = AsyncMock(return_value={"ok": False, "reason": "enter_refused"})
    cleared = AsyncMock(return_value={"ok": True, "cleared": True})
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.probe_surface", AsyncMock(return_value=_surface())
    )
    monkeypatch.setattr("cursor_bridge.operator_paste._submit_composer_enter", enter)
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.clear_our_composer_draft", cleared
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste._poll_submit_verified",
        AsyncMock(return_value=(_surface(), False)),
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste._capture_screenshot_artifact",
        AsyncMock(return_value={"sha256_png": None, "screenshot_path": None}),
    )

    out = await _deliver()

    assert state["click_sent"] is True
    assert out["retryable"] is False
    enter.assert_not_awaited()
    assert cleared.await_count <= 1


@pytest.mark.asyncio
async def test_send_button_error_after_click_script_skips_enter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break: send_button_error after the click script still falls through to Enter."""
    state = {"click_sent": False}
    _install_page(monkeypatch, _handler("send_button_error", state))
    enter = AsyncMock(return_value={"ok": True, "method": "cdp_enter"})
    cleared = AsyncMock(return_value={"ok": True, "cleared": True})
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.probe_surface", AsyncMock(return_value=_surface())
    )
    monkeypatch.setattr("cursor_bridge.operator_paste._submit_composer_enter", enter)
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.clear_our_composer_draft", cleared
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste._poll_submit_verified",
        AsyncMock(return_value=(_surface(), False)),
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste._capture_screenshot_artifact",
        AsyncMock(return_value={"sha256_png": None, "screenshot_path": None}),
    )

    out = await _deliver()

    assert state["click_sent"] is True
    enter.assert_not_awaited()
    assert out["retryable"] is False
    assert cleared.await_count <= 1


@pytest.mark.asyncio
async def test_permit_timeout_before_click_script_allows_enter_unsealed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break: a permit-stamp timeout is treated as a click and Enter is skipped."""
    import cursor_bridge.dom_composer_paste as paste

    state = {"click_sent": False}
    _install_page(monkeypatch, _handler("permit_timeout", state))
    monkeypatch.setattr(paste, "SEND_BTN_CDP_EVAL_TIMEOUT_S", 0.05)
    enter = AsyncMock(return_value={"ok": False, "reason": "enter_refused"})
    cleared = AsyncMock(return_value={"ok": True, "cleared": True})
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.probe_surface", AsyncMock(return_value=_surface())
    )
    monkeypatch.setattr("cursor_bridge.operator_paste._submit_composer_enter", enter)
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.clear_our_composer_draft", cleared
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste._poll_submit_verified",
        AsyncMock(return_value=(_surface(), False)),
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste._capture_screenshot_artifact",
        AsyncMock(return_value={"sha256_png": None, "screenshot_path": None}),
    )

    out = await _deliver()

    assert state["click_sent"] is False
    enter.assert_awaited_once()
    assert out.get("retryable") is not False
    assert cleared.await_count <= 1


_DECISIVE_NO_CLICK = ("no_send_button", "no_composer", "composer_text_mismatch")


def _install_decisive_refusal(
    monkeypatch: pytest.MonkeyPatch,
    *,
    reason: str,
    enter: AsyncMock,
) -> dict[str, bool]:
    """Real ``dom_click_send`` over the fake socket; Enter is the injected fallback."""
    state = {"click_sent": False}
    _install_page(monkeypatch, _handler(reason, state))
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.probe_surface", AsyncMock(return_value=_surface())
    )
    monkeypatch.setattr("cursor_bridge.operator_paste._submit_composer_enter", enter)
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.clear_our_composer_draft",
        AsyncMock(return_value={"ok": True, "cleared": True}),
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste._poll_submit_verified",
        AsyncMock(return_value=(_surface(), False)),
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste._capture_screenshot_artifact",
        AsyncMock(return_value={"sha256_png": None, "screenshot_path": None}),
    )
    return state


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", _DECISIVE_NO_CLICK)
async def test_decisive_page_refusal_awaits_enter_and_seals_when_enter_ok(
    monkeypatch: pytest.MonkeyPatch, reason: str
) -> None:
    """Break: a decisive no-click page result skips Enter and stamps retryable anyway."""
    enter = AsyncMock(return_value={"ok": True, "method": "cdp_enter"})
    state = _install_decisive_refusal(monkeypatch, reason=reason, enter=enter)

    out = await _deliver()

    assert state["click_sent"] is True
    enter.assert_awaited_once()
    assert out["retryable"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", _DECISIVE_NO_CLICK)
async def test_decisive_page_refusal_preflight_enter_stays_retryable(
    monkeypatch: pytest.MonkeyPatch, reason: str
) -> None:
    """Break: Enter refused before keyDown still leaves retryable false after a no-click."""
    enter = AsyncMock(
        return_value={
            "ok": False,
            "reason": "enter_nonce_missing",
            "method": "cdp_enter",
        }
    )
    state = _install_decisive_refusal(monkeypatch, reason=reason, enter=enter)

    out = await _deliver()

    assert state["click_sent"] is True
    enter.assert_awaited_once()
    assert out.get("retryable") is not False


@pytest.mark.asyncio
async def test_decisive_page_refusal_enter_timeout_is_sealed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break: Enter raising after dispatch leaves a decisive no-click retryable."""
    enter = AsyncMock(side_effect=TimeoutError("after keyDown"))
    state = _install_decisive_refusal(monkeypatch, reason="no_send_button", enter=enter)

    out = await _deliver()

    assert state["click_sent"] is True
    enter.assert_awaited_once()
    assert out["retryable"] is False


@pytest.mark.asyncio
async def test_verified_send_readback_failed_is_not_retryable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break: readback_failed after a verified send omits retryable."""
    state = {"click_sent": False}
    _install_page(monkeypatch, _handler("clicked", state))
    cleared = AsyncMock(return_value={"ok": True, "cleared": True})
    enter = AsyncMock(return_value={"ok": True, "method": "cdp_enter"})
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.probe_surface", AsyncMock(return_value=_surface())
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.clear_our_composer_draft", cleared
    )
    monkeypatch.setattr("cursor_bridge.operator_paste._submit_composer_enter", enter)
    monkeypatch.setattr(
        "cursor_bridge.operator_paste._poll_submit_verified",
        AsyncMock(return_value=(_surface(), True)),
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste._poll_readback",
        AsyncMock(
            return_value=(
                _surface(),
                {
                    "readback_ok": False,
                    "composer_empty_after": True,
                    "assistant_began": False,
                    "watermark_visible": False,
                },
            )
        ),
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste._capture_screenshot_artifact",
        AsyncMock(return_value={"sha256_png": None, "screenshot_path": None}),
    )

    out = await _deliver()

    assert state["click_sent"] is True
    assert out["reason"] == "readback_failed"
    assert out["submitted"] is True
    assert out["retryable"] is False
    enter.assert_not_awaited()
    assert cleared.await_count <= 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "site",
    ["readback", "artifact", "screenshot", "transcript", "probe"],
)
async def test_post_click_await_exception_is_sealed(
    monkeypatch: pytest.MonkeyPatch, site: str
) -> None:
    """Break: an exception after the click script leaves deliver_operator_paste unsealed."""
    state = {"click_sent": False}
    _install_page(monkeypatch, _handler("clicked", state))
    cleared = AsyncMock(return_value={"ok": True, "cleared": True})
    enter = AsyncMock(return_value={"ok": True, "method": "cdp_enter"})

    async def _probe(_ws: str) -> dict[str, Any]:
        if site == "probe" and state["click_sent"]:
            raise ValueError("probe after click")
        return _surface()

    monkeypatch.setattr("cursor_bridge.operator_paste.probe_surface", _probe)
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.clear_our_composer_draft", cleared
    )
    monkeypatch.setattr("cursor_bridge.operator_paste._submit_composer_enter", enter)
    if site == "readback":
        monkeypatch.setattr(
            "cursor_bridge.operator_paste._poll_submit_verified",
            AsyncMock(return_value=(_surface(), True)),
        )

        async def _readback(
            *_a: object, **_k: object
        ) -> tuple[dict[str, Any], dict[str, bool]]:
            raise RuntimeError("readback after click")

        monkeypatch.setattr("cursor_bridge.operator_paste._poll_readback", _readback)
    else:
        monkeypatch.setattr(
            "cursor_bridge.operator_paste._poll_submit_verified",
            AsyncMock(return_value=(_surface(), False)),
        )
    if site == "artifact":

        async def _artifacts(*_a: object, **_k: object) -> dict[str, Any]:
            raise RuntimeError("artifact after click")

        monkeypatch.setattr(
            "cursor_bridge.operator_paste._finalize_verification_artifacts",
            _artifacts,
        )
    if site == "screenshot":

        async def _shot(*_a: object, **_k: object) -> dict[str, Any]:
            raise RuntimeError("screenshot after click")

        monkeypatch.setattr(
            "cursor_bridge.operator_paste._capture_screenshot_artifact", _shot
        )
    else:
        monkeypatch.setattr(
            "cursor_bridge.operator_paste._capture_screenshot_artifact",
            AsyncMock(return_value={"sha256_png": None, "screenshot_path": None}),
        )
    if site == "transcript":

        def _transcript(*_a: object, **_k: object) -> bool:
            if state["click_sent"]:
                raise ValueError("transcript after click")
            return False

        monkeypatch.setattr(
            "cursor_bridge.operator_paste._message_in_transcript", _transcript
        )

    out = await _deliver()

    assert state["click_sent"] is True
    assert out["retryable"] is False
    enter.assert_not_awaited()
    assert cleared.await_count <= 1


@pytest.mark.asyncio
async def test_verified_send_then_agent_flip_stays_submitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break: a target change after a verified send reports submitted false."""
    state = {"click_sent": False}
    _install_page(monkeypatch, _handler("clicked", state))
    calls = {"n": 0}

    async def _probe(_ws: str) -> dict[str, Any]:
        calls["n"] += 1
        if calls["n"] >= 4:
            return _surface(active_agent_id="other-agent")
        return _surface()

    cleared = AsyncMock(return_value={"ok": True, "cleared": True})
    enter = AsyncMock(return_value={"ok": True, "method": "cdp_enter"})
    monkeypatch.setattr("cursor_bridge.operator_paste.probe_surface", _probe)
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.clear_our_composer_draft", cleared
    )
    monkeypatch.setattr("cursor_bridge.operator_paste._submit_composer_enter", enter)
    monkeypatch.setattr(
        "cursor_bridge.operator_paste._poll_submit_verified",
        AsyncMock(return_value=(_surface(), True)),
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste._capture_screenshot_artifact",
        AsyncMock(return_value={"sha256_png": None, "screenshot_path": None}),
    )

    out = await _deliver()

    assert state["click_sent"] is True
    assert out["reason"] == "target_changed"
    assert out.get("submitted") is True or out.get("send_verified") is True
    enter.assert_not_awaited()
    assert cleared.await_count <= 1


@pytest.mark.asyncio
async def test_json_decode_after_click_script_is_sealed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break: a malformed CDP frame after the click script escapes without retryable false."""
    state = {"click_sent": False}
    _install_page(monkeypatch, _handler("bad_json", state))
    enter = AsyncMock(return_value={"ok": True, "method": "cdp_enter"})
    cleared = AsyncMock(return_value={"ok": True, "cleared": True})
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.probe_surface", AsyncMock(return_value=_surface())
    )
    monkeypatch.setattr("cursor_bridge.operator_paste._submit_composer_enter", enter)
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.clear_our_composer_draft", cleared
    )

    out = await _deliver()

    assert state["click_sent"] is True
    assert out["retryable"] is False
    enter.assert_not_awaited()
    assert cleared.await_count <= 1
