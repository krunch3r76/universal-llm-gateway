"""Offline tests for Orion pointer wakes (fake page, never live chatgpt.com)."""

from __future__ import annotations

import re
from contextlib import asynccontextmanager

import pytest
from bus_watch.endpoint_wake_transport import classify_orion
from web_chat_relay.orion_wake import (
    PINNED_CONVERSATION_ID,
    PINNED_CONVERSATION_URL,
    _attach_misses,
    _dom_deliver,
    _guarded_close_orion_tab,
    _open_orion_binding_tab,
    _tab_row_matches_conversation_id,
    build_wake_pointer,
    conversation_id_from_url,
    deliver_orion_chat_wake,
    deliver_orion_wake,
    holder_conversation_url,
    sweep_orion_duplicate_tabs,
)
from web_tab.provider_selectors import composer_selector_for_site

pytestmark = pytest.mark.offline

PINNED = PINNED_CONVERSATION_URL


@pytest.fixture(autouse=True)
def stub_orion_dom_deliver(
    monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    if request.node.get_closest_marker("no_dom_stub"):
        return

    async def _fake(page: object, text: str, **kwargs: object) -> dict[str, object]:
        delivered = getattr(page, "dom_delivered", None)
        if delivered is None:
            page.dom_delivered = []  # type: ignore[attr-defined]
            delivered = page.dom_delivered  # type: ignore[attr-defined]
        delivered.append(text)
        page.pasted = True  # type: ignore[attr-defined]
        return {"ok": True, "receipt": "dom_committed"}

    monkeypatch.setattr("web_chat_relay.orion_wake._dom_deliver", _fake)
    monkeypatch.setattr("web_chat_relay.orion_hop._dom_deliver", _fake)


def _dialog_meta(
    *,
    text: str = "",
    aria: str = "",
    labels: list[str] | None = None,
    floating: bool | None = None,
    connector: bool | None = None,
    composers_in_dlg: int = 0,
) -> dict:
    labels = list(labels or [])
    hay = f"{text} {aria}".lower()
    if floating is None:
        floating = "join ulg council" in hay or any(
            re.search(r"floating chat|dock chat|minimize chat", lb, re.I)
            for lb in labels
        )
    if connector is None:
        connector = (
            bool(re.search(r"ulg-code|\bulg code\b|\blife\b", hay, re.I))
            or any(re.search(r"ulg-code|\blife\b", lb, re.I) for lb in labels)
        ) and len(labels) >= 2
    return {
        "text": text,
        "aria": aria,
        "labels": labels,
        "floating": floating,
        "connector": connector,
        "composersInDlg": composers_in_dlg,
    }


class _FakeKeyboard:
    def __init__(self) -> None:
        self.pressed: list[str] = []
        self.inserted: list[str] = []

    async def press(self, key: str) -> None:
        self.pressed.append(key)

    async def insert_text(self, text: str) -> None:
        self.inserted.append(text)
        if hasattr(self, "_page"):
            self._page.pasted = True


class _FakeLocator:
    def __init__(
        self, page: _FakePage, *, visible: bool = True, count: int = 1
    ) -> None:
        self._page = page
        self._visible = visible
        self._count = count

    @property
    def last(self) -> _FakeLocator:
        return self

    @property
    def first(self) -> _FakeLocator:
        return self

    async def count(self) -> int:
        return self._count

    async def is_visible(self) -> bool:
        return self._visible

    async def click(self) -> None:
        self._page.clicked.append("click")
        if getattr(self._page, "_steer_target", False):
            self._page.clicked.append("steer")
        if self._page.approval_click_index is not None and self._page._dialog_i < len(
            self._page.approval_dialogs
        ):
            self._page._dialog_i += 1
            self._page.approval_click_index = None
        if self._page._dock_target:
            self._page.docked = True
            self._page._dock_target = False

    async def press(self, key: str) -> None:
        await self._page.keyboard.press(key)

    def locator(self, _sel: str) -> _FakeLocator:
        n = len(self._page._active_labels()) or 1
        return _FakeLocator(self._page, visible=bool(n), count=n)

    def nth(self, index: int) -> _FakeLocator:
        self._page.approval_click_index = index
        self._page._active_dialog_index = index
        return self

    async def inner_text(self) -> str:
        idx = self._page.approval_click_index
        buttons = self._page._active_labels()
        if idx is not None and idx < len(buttons):
            return buttons[idx]
        return ""

    def filter(self, *, has_text: str = "") -> _FakeLocator:
        if has_text == "Steer" and self._page.steer_visible:
            self._page._steer_target = True
            return _FakeLocator(self._page, visible=True, count=1)
        return _FakeLocator(self._page, visible=False, count=0)


class _FakePage:
    def __init__(
        self,
        *,
        url: str = PINNED,
        streaming: bool = False,
        visibility_state: str = "visible",
        composer_draft: str = "",
        composer_draft_after_reload: str | None = None,
        streaming_after_reload: bool = False,
        preflight_dialogs: list[dict] | None = None,
        approval_dialogs: list[list[str]] | None = None,
        fail_paste: bool = False,
        dock_visible: bool = False,
        steer_visible: bool = False,
        send_visible: bool = False,
    ) -> None:
        self.url = url
        self.streaming = streaming
        self.steer_visible = steer_visible
        self.send_visible = send_visible
        self._steer_target = False
        self.visibility_state = visibility_state
        self.composer_draft = composer_draft
        self.composer_draft_after_reload = composer_draft_after_reload
        self.streaming_after_reload = streaming_after_reload
        self.brought_to_front = False
        self.reloaded = 0
        self.preflight_dialogs = list(preflight_dialogs or [])
        self.approval_dialogs = list(approval_dialogs or [])
        self._dialog_i = 0
        self.fail_paste = fail_paste
        self.keyboard = _FakeKeyboard()
        self.keyboard._page = self  # noqa: SLF001 — test double
        self.clicked: list[str] = []
        self.approval_click_index: int | None = None
        self._active_dialog_index = 0
        self.pasted = False
        self.dom_delivered: list[str] = []
        self.docked = False
        self.dock_visible = dock_visible
        self._dock_target = False

    def _active_labels(self) -> list[str]:
        if not self.pasted:
            if self.preflight_dialogs:
                return list(self.preflight_dialogs[0].get("labels") or [])
            return []
        if self._dialog_i < len(self.approval_dialogs):
            return list(self.approval_dialogs[self._dialog_i])
        return []

    def _scan_dialogs(self) -> list[dict]:
        if not self.pasted:
            specs = list(self.preflight_dialogs)
        elif self.approval_dialogs:
            specs = [
                _dialog_meta(labels=labels)
                for labels in self.approval_dialogs[self._dialog_i :]
            ]
        else:
            specs = list(self.preflight_dialogs)
        entries = []
        for i, spec in enumerate(specs):
            meta = _dialog_meta(
                text=str(spec.get("text") or ""),
                aria=str(spec.get("aria") or ""),
                labels=list(spec.get("labels") or []),
                floating=spec.get("floating"),
                connector=spec.get("connector"),
                composers_in_dlg=int(spec.get("composersInDlg") or 0),
            )
            entries.append({"index": i, **meta})
        return entries

    def locator(self, selector: str) -> _FakeLocator:
        self._dock_target = False
        if "Dock Chat" in selector:
            self._dock_target = True
            return _FakeLocator(
                self, visible=self.dock_visible, count=1 if self.dock_visible else 0
            )
        if "dialog" in selector:
            n = len(self._active_labels()) or len(self.preflight_dialogs) or 1
            return _FakeLocator(self, visible=bool(n), count=max(n, 1))
        if "Steer" in selector:
            return _FakeLocator(
                self, visible=self.steer_visible, count=1 if self.steer_visible else 0
            )
        if 'aria-label="Send"' in selector:
            return _FakeLocator(
                self, visible=self.send_visible, count=1 if self.send_visible else 0
            )
        if selector.strip() == "button":
            return _FakeLocator(self, visible=self.steer_visible, count=1)
        if "button" in selector and "Send" not in selector:
            n = len(self._active_labels()) or 1
            return _FakeLocator(self, count=n)
        return _FakeLocator(self)

    async def bring_to_front(self) -> None:
        self.brought_to_front = True

    async def reload(self, wait_until: str = "domcontentloaded") -> None:
        _ = wait_until
        self.reloaded += 1
        if self.streaming_after_reload:
            self.streaming = True

    async def evaluate(self, expression: str) -> object:
        if "visibilityState" in expression and "composerEmpty" in expression:
            if self.reloaded > 0 and self.composer_draft_after_reload is not None:
                draft = self.composer_draft_after_reload
            else:
                draft = self.composer_draft
            return {
                "visibilityState": self.visibility_state,
                "composerEmpty": not (draft or "").strip(),
            }
        if "stop" in expression.lower() and "streaming" not in expression:
            return self.streaming
        if "dialogs.find" in expression and "d.connector" in expression:
            entries = self._scan_dialogs()
            for ent in entries:
                if ent.get("connector"):
                    return {
                        "present": True,
                        "buttons": ent["labels"],
                        "index": ent["index"],
                    }
            return {"present": False, "buttons": []}
        if "composersInDlg" in expression:
            entries = []
            for i, ent in enumerate(self._scan_dialogs()):
                entries.append({"index": i, **ent})
            return {"dialogs": entries}
        if "return dlg.querySelectorAll" in expression:
            if self.preflight_dialogs:
                return int(self.preflight_dialogs[0].get("composersInDlg") or 0)
            return 0
        if (
            "dialog" in expression
            and "dialogs.find" not in expression
            and "composersInDlg" not in expression
        ):
            labels = self._active_labels()
            if not labels:
                return {"present": False, "buttons": []}
            return {"present": True, "buttons": labels}
        return {}

    async def wait_for_timeout(self, _ms: int) -> None:
        if self.dom_delivered or self.keyboard.inserted:
            self.pasted = True
        return None


@pytest.mark.asyncio
async def test_happy_path_paste_and_second_approval() -> None:
    page = _FakePage(
        approval_dialogs=[
            ["Deny", "Allow ulg-code for this conversation", "Always allow"],
        ],
    )
    out = await deliver_orion_wake(
        thread="21207",
        turn=7,
        from_agent="web-anthropic",
        page=page,
    )
    assert out["ok"] is True, out
    assert out["delivered"] is True
    assert out["approval"]["outcome"] == "approval_complete"
    assert (
        out["approval"]["dialogs"][0]["clicked_text"]
        == "Allow ulg-code for this conversation"
    )
    assert "21207" in page.dom_delivered[0]
    assert "turn 7" in page.dom_delivered[0]
    assert (
        "maestro" in page.dom_delivered[0].lower()
        or "web-anthropic" in page.dom_delivered[0].lower()
    )
    assert not page.keyboard.inserted


@pytest.mark.asyncio
async def test_floating_layout_delivers_with_floating_dialog_open() -> None:
    page = _FakePage(
        preflight_dialogs=[
            _dialog_meta(
                aria="Join ULG Council",
                labels=[
                    "Restore floating chat: Join ULG Council",
                    "Expand floating chat",
                    "Minimize chat",
                    "Dock Chat",
                ],
            ),
        ],
        approval_dialogs=[],
    )
    out = await deliver_orion_wake(
        thread="21207",
        turn=99,
        from_agent="web-anthropic",
        page=page,
    )
    assert out["ok"] is True, out
    assert out["delivered"] is True
    assert out.get("layout") == "floating"
    assert page.dom_delivered
    assert not page.keyboard.inserted


@pytest.mark.asyncio
async def test_non_connector_dialog_refuses_before_paste() -> None:
    page = _FakePage(
        preflight_dialogs=[
            _dialog_meta(text="Delete this project?", labels=["Cancel", "OK"]),
        ],
    )
    out = await deliver_orion_wake(
        thread="21207",
        turn=2,
        from_agent="maestro",
        page=page,
    )
    assert out["reason"] == "non_connector_dialog"
    assert out["pasted"] is False
    assert not page.dom_delivered
    assert not page.keyboard.inserted


@pytest.mark.asyncio
async def test_conversation_missing_wrong_tab() -> None:
    page = _FakePage(url="https://chatgpt.com/plugins")
    out = await deliver_orion_wake(
        thread="21207",
        turn=1,
        from_agent="maestro",
        page=page,
        conversation_url=PINNED,
    )
    assert out["reason"] == "conversation_missing"


@pytest.mark.asyncio
async def test_streaming_tab_steers_without_defer() -> None:
    page = _FakePage(streaming=True)
    out = await deliver_orion_wake(
        thread="21207", turn=1, from_agent="maestro", page=page
    )
    assert out["ok"] is True, out
    assert out["reason"] == "delivered"
    assert out["delivered"] is True
    assert page.pasted is True


@pytest.mark.asyncio
async def test_hidden_tab_with_empty_composer_brings_to_front_and_reloads_before_paste(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = _FakePage(visibility_state="hidden", composer_draft="")
    page.target_id = "tab-wake"

    class _Port:
        def list_tabs(self, *, cdp_url: str) -> dict:
            return {
                "pages": [
                    {
                        "id": "tab-wake",
                        "url": PINNED,
                        "web_socket_debugger_url": "ws://fake/wake",
                    }
                ],
                "reachable": True,
                "error": None,
            }

    monkeypatch.setattr("web_tab.cdp_actuation.default_actuation_port", lambda: _Port())
    monkeypatch.setattr(
        "web_tab.write_ops._created_by_agent_for_target", lambda **kwargs: True
    )
    out = await deliver_orion_wake(
        thread="21207",
        turn=4,
        from_agent="maestro",
        page=page,
    )
    assert out["ok"] is True, out
    assert out["brought_to_front"] is True
    assert out["tab_reloaded"] is True
    assert page.reloaded == 1
    assert page.dom_delivered
    assert not page.keyboard.inserted


@pytest.mark.asyncio
async def test_hidden_tab_draft_restored_after_reload_refuses_non_empty() -> None:
    page = _FakePage(
        visibility_state="hidden",
        composer_draft="",
        composer_draft_after_reload="restored after reload",
    )
    out = await deliver_orion_wake(
        thread="21207",
        turn=4,
        from_agent="maestro",
        page=page,
    )
    assert out["reason"] == "non_empty_composer"
    assert out["pasted"] is False
    assert out["tab_reloaded"] is True
    assert page.reloaded == 1
    assert not page.dom_delivered
    assert not page.keyboard.inserted


@pytest.mark.asyncio
async def test_hidden_tab_with_composer_draft_refuses_non_empty() -> None:
    page = _FakePage(
        visibility_state="hidden",
        composer_draft="unsent user text",
    )
    out = await deliver_orion_wake(
        thread="21207",
        turn=4,
        from_agent="maestro",
        page=page,
    )
    assert out["reason"] == "non_empty_composer"
    assert out["pasted"] is False
    assert page.reloaded == 0
    assert not page.dom_delivered
    assert not page.keyboard.inserted


@pytest.mark.asyncio
@pytest.mark.no_dom_stub
async def test_dom_deliver_uses_chatgpt_selector_not_keyboard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = _FakePage()
    insert_calls: list[tuple[str, str, bool]] = []

    class _Port:
        def list_tabs(self, *, cdp_url: str) -> dict:
            return {
                "pages": [
                    {
                        "id": "tab-orion",
                        "url": PINNED,
                        "web_socket_debugger_url": "ws://127.0.0.1/devtools/page/orion",
                    }
                ]
            }

    async def _insert(
        ws: str, *, selector: str, line: str, preserve_newlines: bool = False, **_kwargs: object
    ) -> dict:
        insert_calls.append((selector, line, preserve_newlines))
        return {"ok": True}

    async def _baseline(*_a: object, **_k: object) -> dict:
        return {"user_count": 0}

    async def _verify(*_a: object, **_k: object) -> dict:
        return {"ok": True, "receipt": "dom_committed"}

    async def _pre(*_a: object, **_k: object) -> dict:
        return {"ok": True}

    async def _recheck(*_a: object, **_k: object) -> dict:
        return {"ok": True, "probe": {}}

    lock_kwargs: dict[str, object] = {}

    @asynccontextmanager
    async def _lock(_target_id: str, *, timeout_s: float = 120, op: str = ""):
        lock_kwargs["timeout_s"] = timeout_s
        lock_kwargs["op"] = op
        yield

    expected_selector = composer_selector_for_site("chatgpt")
    monkeypatch.setattr("web_tab.cdp_actuation.default_actuation_port", lambda: _Port())
    monkeypatch.setattr(
        "cursor_bridge.dom_composer_paste.dom_insert_and_send", _insert
    )
    monkeypatch.setattr("web_tab.commit_verify.capture_dom_baseline", _baseline)
    monkeypatch.setattr("web_tab.commit_verify.poll_verify_committed", _verify)
    monkeypatch.setattr(
        "web_tab.operator_guard.wait_operator_idle_before_lock", _pre
    )
    monkeypatch.setattr(
        "web_tab.operator_guard.operator_recheck_under_lock", _recheck
    )
    monkeypatch.setattr("web_tab.target_lock.target_write_lock", _lock)

    out = await _dom_deliver(
        page,
        build_wake_pointer(thread="21207", turn=1, from_agent="maestro"),
    )
    assert out["ok"] is True, out
    assert insert_calls
    assert insert_calls[0][0] == expected_selector
    assert insert_calls[0][2] is False
    assert lock_kwargs.get("timeout_s") == 15
    assert not page.keyboard.inserted


@pytest.mark.asyncio
async def test_dom_deliver_unique_draft_writer_each_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each _dom_deliver call must pass a per-attempt writer, not agent bool 'True'."""
    page = _FakePage()
    writers: list[str] = []

    class _Port:
        def list_tabs(self, *, cdp_url: str) -> dict:
            return {
                "pages": [
                    {
                        "id": "tab-orion",
                        "url": PINNED,
                        "web_socket_debugger_url": "ws://127.0.0.1/devtools/page/orion",
                    }
                ]
            }

    async def _insert(ws: str, *, draft_writer: str = "", **_kwargs: object) -> dict:
        writers.append(str(draft_writer))
        return {"ok": True}

    async def _baseline(*_a: object, **_k: object) -> dict:
        return {"user_count": 0}

    async def _verify(*_a: object, **_k: object) -> dict:
        return {"ok": True, "receipt": "dom_committed"}

    async def _pre(*_a: object, **_k: object) -> dict:
        return {"ok": True}

    async def _recheck(*_a: object, **_k: object) -> dict:
        return {"ok": True, "probe": {}}

    @asynccontextmanager
    async def _lock(_target_id: str, *, timeout_s: float = 120, op: str = ""):
        yield

    monkeypatch.setattr("web_tab.cdp_actuation.default_actuation_port", lambda: _Port())
    monkeypatch.setattr(
        "cursor_bridge.dom_composer_paste.dom_insert_and_send", _insert
    )
    monkeypatch.setattr("web_tab.commit_verify.capture_dom_baseline", _baseline)
    monkeypatch.setattr("web_tab.commit_verify.poll_verify_committed", _verify)
    monkeypatch.setattr(
        "web_tab.operator_guard.wait_operator_idle_before_lock", _pre
    )
    monkeypatch.setattr(
        "web_tab.operator_guard.operator_recheck_under_lock", _recheck
    )
    monkeypatch.setattr("web_tab.target_lock.target_write_lock", _lock)
    monkeypatch.setattr(
        "web_tab.write_ops._created_by_agent_for_target", lambda **_k: True
    )

    line = build_wake_pointer(thread="21207", turn=1, from_agent="maestro")
    await _dom_deliver(page, line)
    await _dom_deliver(page, line)
    assert len(writers) == 2
    assert writers[0] != writers[1]
    assert writers[0] != "True"
    assert writers[1] != "True"
    assert all(w.startswith("orion:tab-orion:") for w in writers)


@pytest.mark.asyncio
async def test_dom_deliver_distinct_writer_blocks_used_existing_draft_shortcut(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A new wake attempt must not reuse the prior wake's writer for used_existing_draft."""
    page = _FakePage()
    prior_writer = "orion:tab-orion:deadbeef"
    prior_sig = build_wake_pointer(thread="21207", turn=1, from_agent="maestro")

    class _Port:
        def list_tabs(self, *, cdp_url: str) -> dict:
            return {
                "pages": [
                    {
                        "id": "tab-orion",
                        "url": PINNED,
                        "web_socket_debugger_url": "ws://127.0.0.1/devtools/page/orion",
                    }
                ]
            }

    async def _insert(
        ws: str, *, draft_writer: str = "", line: str = "", **_kwargs: object
    ) -> dict:
        if draft_writer == prior_writer:
            return {
                "ok": True,
                "used_existing_draft": True,
                "pasted": True,
                "inserted": True,
            }
        return {
            "ok": False,
            "reason": "composer_not_empty",
            "pasted": False,
            "phase": "foreign_draft",
        }

    async def _baseline(*_a: object, **_k: object) -> dict:
        return {"user_count": 0}

    async def _verify(*_a: object, **_k: object) -> dict:
        return {"ok": True, "receipt": "dom_committed"}

    async def _pre(*_a: object, **_k: object) -> dict:
        return {"ok": True}

    async def _recheck(*_a: object, **_k: object) -> dict:
        return {"ok": True, "probe": {}}

    @asynccontextmanager
    async def _lock(_target_id: str, *, timeout_s: float = 120, op: str = ""):
        yield

    monkeypatch.setattr("web_tab.cdp_actuation.default_actuation_port", lambda: _Port())
    monkeypatch.setattr(
        "cursor_bridge.dom_composer_paste.dom_insert_and_send", _insert
    )
    monkeypatch.setattr("web_tab.commit_verify.capture_dom_baseline", _baseline)
    monkeypatch.setattr("web_tab.commit_verify.poll_verify_committed", _verify)
    monkeypatch.setattr(
        "web_tab.operator_guard.wait_operator_idle_before_lock", _pre
    )
    monkeypatch.setattr(
        "web_tab.operator_guard.operator_recheck_under_lock", _recheck
    )
    monkeypatch.setattr("web_tab.target_lock.target_write_lock", _lock)

    out = await _dom_deliver(page, prior_sig)
    assert out.get("ok") is False
    assert out.get("pasted") is False


def _dom_deliver_port_tab(tab_id: str = "tab-op") -> object:
    class _Port:
        def list_tabs(self, *, cdp_url: str) -> dict:
            return {
                "pages": [
                    {
                        "id": tab_id,
                        "url": PINNED,
                        "web_socket_debugger_url": "ws://127.0.0.1/devtools/page/op",
                    }
                ],
                "reachable": True,
                "error": None,
            }

    return _Port()


@pytest.mark.asyncio
@pytest.mark.no_dom_stub
async def test_dom_deliver_accepts_visible_idle_operator_tab(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC7 (a): paste may proceed on a visible, idle operator-provenance tab."""
    from unittest.mock import AsyncMock

    page = _FakePage()
    insert_calls: list[str] = []

    async def _insert(ws: str, *, selector: str, line: str, **_kwargs: object) -> dict:
        insert_calls.append(line)
        return {"ok": True}

    async def _baseline(*_a: object, **_k: object) -> dict:
        return {"user_count": 0}

    async def _verify(*_a: object, **_k: object) -> dict:
        return {"ok": True, "receipt": "dom_committed"}

    async def _pre(*_a: object, **_k: object) -> dict:
        return {"ok": True}

    @asynccontextmanager
    async def _lock(_target_id: str, *, timeout_s: float = 120, op: str = ""):
        yield

    monkeypatch.setattr(
        "web_tab.cdp_actuation.default_actuation_port",
        lambda: _dom_deliver_port_tab(),
    )
    monkeypatch.setattr(
        "web_tab.write_ops._created_by_agent_for_target", lambda **kwargs: False
    )
    monkeypatch.setattr(
        "cursor_bridge.dom_composer_paste.dom_insert_and_send", _insert
    )
    monkeypatch.setattr("web_tab.commit_verify.capture_dom_baseline", _baseline)
    monkeypatch.setattr("web_tab.commit_verify.poll_verify_committed", _verify)
    monkeypatch.setattr(
        "web_tab.operator_guard.wait_operator_idle_before_lock", _pre
    )
    monkeypatch.setattr("web_tab.target_lock.target_write_lock", _lock)
    monkeypatch.setattr(
        "web_tab.operator_guard.read_operator_busy",
        AsyncMock(
            return_value={
                "document_has_focus": False,
                "document_visibility": "visible",
                "operator_active": False,
                "foreign_draft": False,
            }
        ),
    )
    monkeypatch.setattr(
        "web_tab.operator_guard.wait_for_operator_idle",
        AsyncMock(return_value={"ok": True, "idle": True}),
    )

    out = await _dom_deliver(
        page,
        build_wake_pointer(thread="21207", turn=2, from_agent="maestro"),
    )
    assert out["ok"] is True, out
    assert insert_calls


@pytest.mark.asyncio
@pytest.mark.no_dom_stub
async def test_dom_deliver_refuses_visible_operator_tab_when_operator_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC7 (b): operator_active still blocks paste on visible operator tabs."""
    from unittest.mock import AsyncMock

    page = _FakePage()

    async def _pre(*_a: object, **_k: object) -> dict:
        return {"ok": True}

    @asynccontextmanager
    async def _lock(_target_id: str, *, timeout_s: float = 120, op: str = ""):
        yield

    monkeypatch.setattr(
        "web_tab.cdp_actuation.default_actuation_port",
        lambda: _dom_deliver_port_tab(),
    )
    monkeypatch.setattr(
        "web_tab.write_ops._created_by_agent_for_target", lambda **kwargs: False
    )
    monkeypatch.setattr(
        "web_tab.operator_guard.wait_operator_idle_before_lock", _pre
    )
    monkeypatch.setattr("web_tab.target_lock.target_write_lock", _lock)
    monkeypatch.setattr(
        "web_tab.operator_guard.read_operator_busy",
        AsyncMock(
            return_value={
                "document_has_focus": False,
                "document_visibility": "visible",
                "operator_active": True,
                "foreign_draft": False,
            }
        ),
    )
    monkeypatch.setattr(
        "web_tab.operator_guard.wait_for_operator_idle",
        AsyncMock(return_value={"ok": True, "idle": True}),
    )

    out = await _dom_deliver(
        page,
        build_wake_pointer(thread="21207", turn=2, from_agent="maestro"),
    )
    assert out["ok"] is False
    assert "operator" in str(out.get("reason") or "").lower()


@pytest.mark.asyncio
@pytest.mark.no_dom_stub
async def test_dom_deliver_refuses_visible_operator_tab_when_foreign_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC7 (b): foreign draft still blocks paste on visible operator tabs."""
    from unittest.mock import AsyncMock

    page = _FakePage()

    async def _pre(*_a: object, **_k: object) -> dict:
        return {"ok": True}

    @asynccontextmanager
    async def _lock(_target_id: str, *, timeout_s: float = 120, op: str = ""):
        yield

    monkeypatch.setattr(
        "web_tab.cdp_actuation.default_actuation_port",
        lambda: _dom_deliver_port_tab(),
    )
    monkeypatch.setattr(
        "web_tab.write_ops._created_by_agent_for_target", lambda **kwargs: False
    )
    monkeypatch.setattr(
        "web_tab.operator_guard.wait_operator_idle_before_lock", _pre
    )
    monkeypatch.setattr("web_tab.target_lock.target_write_lock", _lock)
    monkeypatch.setattr(
        "web_tab.operator_guard.read_operator_busy",
        AsyncMock(
            return_value={
                "document_has_focus": False,
                "document_visibility": "visible",
                "operator_active": False,
                "foreign_draft": True,
                "composer_draft": "operator text",
            }
        ),
    )
    monkeypatch.setattr(
        "web_tab.operator_guard.wait_for_operator_idle",
        AsyncMock(return_value={"ok": True, "idle": True}),
    )

    out = await _dom_deliver(
        page,
        build_wake_pointer(thread="21207", turn=2, from_agent="maestro"),
    )
    assert out["ok"] is False
    assert "foreign" in str(out.get("reason") or "").lower()


@pytest.mark.asyncio
async def test_guarde_close_refuses_focus_for_agent_and_operator_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC7 (c): close path keeps focus/visible refusal (agent tab via tab_has_focus)."""
    from web_chat_relay.orion_wake import _guarded_close_orion_tab

    async def _pre(*_a: object, **_k: object) -> dict:
        return {"ok": True}

    @asynccontextmanager
    async def _lock(_target_id: str, *, timeout_s: float = 120, op: str = ""):
        yield

    class _Port:
        def list_tabs(self, *, cdp_url: str) -> dict:
            return {
                "pages": [
                    {
                        "id": "tab-agent",
                        "url": PINNED,
                        "web_socket_debugger_url": "ws://agent",
                    },
                    {
                        "id": "tab-operator",
                        "url": PINNED,
                        "web_socket_debugger_url": "ws://operator",
                    },
                ],
                "reachable": True,
                "error": None,
            }

    async def _recheck_focus(*_a: object, **_k: object) -> dict:
        return {
            "ok": True,
            "probe": {
                "document_has_focus": True,
                "document_visibility": "hidden",
                "operator_active": False,
            },
        }

    async def _recheck_visible(*_a: object, **_k: object) -> dict:
        return {
            "ok": True,
            "probe": {
                "document_has_focus": False,
                "document_visibility": "visible",
                "operator_active": False,
            },
        }

    monkeypatch.setattr("web_tab.cdp_actuation.default_actuation_port", lambda: _Port())
    monkeypatch.setattr("web_tab.operator_guard.wait_operator_idle_before_lock", _pre)
    monkeypatch.setattr("web_tab.target_lock.target_write_lock", _lock)
    monkeypatch.setattr("web_tab.cdp_actuation._close_target", lambda *a, **k: None)

    def _created(*, target_id: str, **kwargs: object) -> bool:
        return target_id == "tab-agent"

    monkeypatch.setattr("web_tab.write_ops._created_by_agent_for_target", _created)
    monkeypatch.setattr(
        "web_tab.operator_guard.operator_recheck_under_lock", _recheck_focus
    )
    agent_focus = await _guarded_close_orion_tab(
        cdp_url="http://127.0.0.1:9342",
        target_id="tab-agent",
        conversation_url=PINNED,
    )
    assert agent_focus["ok"] is False
    assert agent_focus["reason"] == "tab_has_focus"

    monkeypatch.setattr(
        "web_tab.operator_guard.operator_recheck_under_lock", _recheck_visible
    )
    agent_visible = await _guarded_close_orion_tab(
        cdp_url="http://127.0.0.1:9342",
        target_id="tab-agent",
        conversation_url=PINNED,
    )
    assert agent_visible["ok"] is False
    assert agent_visible["reason"] == "tab_has_focus"

    operator_out = await _guarded_close_orion_tab(
        cdp_url="http://127.0.0.1:9342",
        target_id="tab-operator",
        conversation_url=PINNED,
    )
    assert operator_out["ok"] is False
    assert operator_out["reason"] == "operator_provenance_tab"


@pytest.mark.asyncio
async def test_dom_deliver_returns_busy_when_target_lock_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = _FakePage()

    class _Port:
        def list_tabs(self, *, cdp_url: str) -> dict:
            return {
                "pages": [
                    {
                        "id": "tab-busy",
                        "url": PINNED,
                        "web_socket_debugger_url": "ws://127.0.0.1/devtools/page/busy",
                    }
                ]
            }

    async def _pre(*_a: object, **_k: object) -> dict:
        return {"ok": True}

    from web_tab.target_lock import TargetLockBusyError

    @asynccontextmanager
    async def _lock(_target_id: str, *, timeout_s: float = 120, op: str = ""):
        raise TargetLockBusyError(
            target_id=_target_id,
            waited_s=timeout_s,
            holder={"pid": 1, "op": op},
        )
        yield  # pragma: no cover

    monkeypatch.setattr("web_tab.cdp_actuation.default_actuation_port", lambda: _Port())
    monkeypatch.setattr(
        "web_tab.operator_guard.wait_operator_idle_before_lock", _pre
    )
    monkeypatch.setattr("web_tab.target_lock.target_write_lock", _lock)

    out = await _dom_deliver(
        page,
        build_wake_pointer(thread="21207", turn=2, from_agent="maestro"),
    )
    assert out["ok"] is False
    assert out.get("reason") == "target_lock_busy"
    assert out.get("retryable") is True


@pytest.mark.asyncio
async def test_hidden_tab_streaming_skips_reload_and_delivers() -> None:
    page = _FakePage(
        visibility_state="hidden",
        composer_draft="",
        streaming=True,
    )
    out = await deliver_orion_wake(
        thread="21207",
        turn=4,
        from_agent="maestro",
        page=page,
    )
    assert out["ok"] is True, out
    assert out["reason"] == "delivered"
    assert out["tab_reloaded"] is False
    assert page.reloaded == 0
    assert page.dom_delivered
    assert not page.keyboard.inserted


@pytest.mark.asyncio
async def test_orion_echo_drop() -> None:
    page = _FakePage()
    out = await deliver_orion_wake(
        thread="21207", turn=1, from_agent="orion", page=page
    )
    assert out["reason"] == "orion_echo_drop"
    assert not page.dom_delivered
    assert not page.keyboard.inserted


@pytest.mark.asyncio
async def test_approval_absent() -> None:
    page = _FakePage(approval_dialogs=[])
    out = await deliver_orion_wake(
        thread="21207", turn=2, from_agent="maestro", page=page
    )
    assert out["ok"] is True
    assert out["approval"]["outcome"] == "approval_absent"


@pytest.mark.asyncio
async def test_single_connector_label_not_enough_for_approval() -> None:
    page = _FakePage(
        approval_dialogs=[["Only one ulg-code button"]],
    )
    out = await deliver_orion_wake(
        thread="21207", turn=2, from_agent="maestro", page=page
    )
    assert out["ok"] is True
    assert out["delivered"] is True
    assert out["approval"]["outcome"] == "approval_absent"


@pytest.mark.asyncio
async def test_two_connector_dialogs_ulg_code_then_life() -> None:
    page = _FakePage(
        approval_dialogs=[
            ["Deny", "Allow ulg-code for this conversation", "Always"],
            ["Deny", "Allow life for this conversation", "Always"],
        ],
    )
    out = await deliver_orion_wake(
        thread="21207",
        turn=10,
        from_agent="web-anthropic",
        page=page,
    )
    assert out["ok"] is True
    assert len(out["approval"]["dialogs"]) == 2
    assert "ulg-code" in out["approval"]["dialogs"][0]["clicked_text"]
    assert "life" in out["approval"]["dialogs"][1]["clicked_text"]


@pytest.mark.asyncio
async def test_floating_layout_docks_when_no_inner_composer() -> None:
    page = _FakePage(
        preflight_dialogs=[
            _dialog_meta(
                aria="Join ULG Council",
                labels=["Restore floating chat", "Dock Chat"],
                composers_in_dlg=0,
            ),
        ],
        approval_dialogs=[],
        dock_visible=True,
    )
    out = await deliver_orion_wake(
        thread="21207",
        turn=3,
        from_agent="maestro",
        page=page,
    )
    assert out["ok"] is True, out
    assert out["delivered"] is True
    assert out.get("docked") is True


@pytest.mark.asyncio
async def test_cdp_down() -> None:
    async def _boom(_url: str):
        raise ConnectionError("refused")

    out = await deliver_orion_wake(
        thread="21207",
        turn=1,
        from_agent="maestro",
        connect_cdp=_boom,
        binding_supplied=True,
        stored_endpoint=PINNED,
    )
    assert out["reason"] == "cdp_unreachable"


def test_classify_orion_outcomes() -> None:
    assert (
        classify_orion({"ok": True, "delivered": True, "reason": "delivered"})[0]
        == "delivered"
    )
    assert classify_orion({"ok": False, "reason": "deferred_streaming"})[0] == "pending"
    assert classify_orion({"ok": False, "reason": "orion_echo_drop"})[0] == "skipped"
    assert (
        classify_orion(
            {"ok": False, "reason": "approval_unrecognized", "pasted": True}
        )[0]
        == "refused"
    )
    assert (
        classify_orion(
            {"ok": False, "reason": "non_connector_dialog", "pasted": False}
        )[0]
        == "refused"
    )


def test_conversation_id_from_url_project_path() -> None:
    assert conversation_id_from_url(PINNED_CONVERSATION_URL) == PINNED_CONVERSATION_ID


def test_extract_orion_seat_registration_url_hop06_specimen() -> None:
    from web_chat_relay.orion_wake import extract_orion_seat_registration_url

    body = (
        "TYPE: SEAT_REGISTRATION\n"
        "Old exact URL: https://chatgpt.com/g/g-p-6ae3debfee8481918ff3fa45b7d18d3a-ulg-orion"
        "/c/6ae44d27-06cc-83e8-8b8a-df9b819755bd\n"
        "New exact URL: https://chatgpt.com/g/g-p-6ae3debfee8481918ff3fa45b7d18d3a-ulg-orion"
        "/c/6ae4cfde-151c-83e8-8966-fdd8893f472d\n"
    )
    url = extract_orion_seat_registration_url(
        body, subject="SEAT_REGISTRATION|orion-HOP06"
    )
    assert url is not None
    assert url.endswith("/c/6ae4cfde-151c-83e8-8966-fdd8893f472d")
    assert "6ae44d27" not in url


def test_holder_conversation_url_from_subscription() -> None:
    sub = {"agent_id": PINNED_CONVERSATION_ID}
    assert PINNED_CONVERSATION_ID in holder_conversation_url(sub)
    project_sub = {"chat_url": PINNED_CONVERSATION_URL}
    assert (
        conversation_id_from_url(holder_conversation_url(project_sub))
        == PINNED_CONVERSATION_ID
    )
    assert build_wake_pointer(thread="1", turn=2, from_agent="x").startswith("WAKE —")


_NEUTRAL_CONV_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
_NEUTRAL_PROJECT_URL = "https://chatgpt.com/g/g-p-neutral-project-slug/c/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
_NEUTRAL_FLAT_URL = f"https://chatgpt.com/c/{_NEUTRAL_CONV_ID}"


def test_holder_conversation_url_mixed_synthetic_preserves_project_url() -> None:
    """Break: bare agent_id scanned first → flat URL vs ledger project-qualified endpoint."""
    sub = {
        "title": _NEUTRAL_PROJECT_URL,
        "agent_id": _NEUTRAL_CONV_ID,
    }
    assert holder_conversation_url(sub) == _NEUTRAL_PROJECT_URL


def test_holder_conversation_url_explicit_url_strips_query() -> None:
    sub = {
        "title": _NEUTRAL_PROJECT_URL + "?model=gpt-4",
        "agent_id": _NEUTRAL_CONV_ID,
    }
    assert holder_conversation_url(sub) == _NEUTRAL_PROJECT_URL


def test_holder_conversation_url_bare_id_only_flat_fallback() -> None:
    sub = {"agent_id": _NEUTRAL_CONV_ID}
    assert holder_conversation_url(sub) == _NEUTRAL_FLAT_URL


def test_holder_conversation_url_mismatched_title_not_candidate() -> None:
    other_id = "11111111-2222-3333-4444-555555555555"
    other_url = f"https://chatgpt.com/g/g-p-other/c/{other_id}"
    sub = {"title": other_url, "agent_id": _NEUTRAL_CONV_ID}
    assert holder_conversation_url(sub) == _NEUTRAL_FLAT_URL


def test_holder_conversation_url_full_agent_id_url_precedence() -> None:
    flat_other = "https://chatgpt.com/c/11111111-2222-3333-4444-555555555555"
    sub = {
        "agent_id": _NEUTRAL_PROJECT_URL,
        "title": flat_other,
    }
    assert holder_conversation_url(sub) == _NEUTRAL_PROJECT_URL


def _defer_claim_opts():
    from web_chat_relay.tests.test_orion_hop import _wake_claim

    claim = _wake_claim(wake_id="wake-defer-1", turn_numbers=(12,), turn_ids=(912,))
    opts = {
        "wake_id": claim.wake_id,
        "thread": claim.thread_id,
        "turn_id": 912,
        "turn": 12,
        "conversation_url": PINNED_CONVERSATION_URL,
        "agent": "orion",
    }
    return claim, opts


@pytest.mark.asyncio
async def test_chat_wake_defers_target_lock_busy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A target lock timeout is re-deferred through record_paste_receipt, not paste_failed."""
    from web_chat_relay.orion_wake import deliver_orion_chat_wake
    from web_chat_relay.orion_wake_owner import InMemoryWakeDeliveryOwner

    async def _busy(page: object, text: str, **kwargs: object) -> dict:
        return {"ok": False, "reason": "target_lock_busy", "retryable": True}

    monkeypatch.setattr("web_chat_relay.orion_wake._dom_deliver", _busy)
    owner = InMemoryWakeDeliveryOwner()
    claim, opts = _defer_claim_opts()
    owner.register_claim(claim)
    out = await deliver_orion_chat_wake(
        thread="21207",
        turn=12,
        from_agent="maestro",
        page=_FakePage(url=PINNED_CONVERSATION_URL),
        conversation_url=PINNED_CONVERSATION_URL,
        pipeline_options=opts,
        delivery_owner=owner,
    )
    stored = owner.paste_receipts[claim.wake_id]
    assert out["reason"] == "deferred_streaming"
    assert stored["reason"] == "deferred_streaming"
    assert stored.get("error") == "target_lock_busy"
    assert "paste_failed" not in (out["reason"], stored["reason"])
    assert classify_orion(out) == ("pending", "deferred_streaming")


@pytest.mark.asyncio
async def test_chat_wake_defers_operator_busy_before_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An operator-busy refusal before the lock is re-deferred, not recorded paste_failed."""
    from web_chat_relay.orion_wake import deliver_orion_chat_wake
    from web_chat_relay.orion_wake_owner import InMemoryWakeDeliveryOwner

    async def _busy(page: object, text: str, **kwargs: object) -> dict:
        return {
            "ok": False,
            "reason": "operator_busy",
            "code": "operator_active",
            "before_lock": True,
        }

    monkeypatch.setattr("web_chat_relay.orion_wake._dom_deliver", _busy)
    owner = InMemoryWakeDeliveryOwner()
    claim, opts = _defer_claim_opts()
    claim = claim.__class__(
        wake_id="wake-defer-2",
        agent=claim.agent,
        thread_id=claim.thread_id,
        turn_ids=claim.turn_ids,
        turn_numbers=claim.turn_numbers,
        endpoint_url=claim.endpoint_url,
    )
    opts = {**opts, "wake_id": claim.wake_id}
    owner.register_claim(claim)
    out = await deliver_orion_chat_wake(
        thread="21207",
        turn=12,
        from_agent="maestro",
        page=_FakePage(url=PINNED_CONVERSATION_URL),
        conversation_url=PINNED_CONVERSATION_URL,
        pipeline_options=opts,
        delivery_owner=owner,
    )
    stored = owner.paste_receipts[claim.wake_id]
    assert out["reason"] == "deferred_streaming"
    assert stored["reason"] == "deferred_streaming"
    assert stored.get("error") == "operator_busy"
    assert classify_orion(out)[0] == "pending"


@pytest.mark.no_dom_stub
@pytest.mark.asyncio
async def test_dom_deliver_marks_operator_refusal_before_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pre-lock idle refusal is marked before_lock so the wake can re-defer it."""
    page = _FakePage()

    class _Port:
        def list_tabs(self, *, cdp_url: str) -> dict:
            return {
                "pages": [
                    {
                        "id": "tab-op",
                        "url": PINNED,
                        "web_socket_debugger_url": "ws://127.0.0.1/devtools/page/op",
                    }
                ]
            }

    async def _pre(*_a: object, **_k: object) -> dict:
        return {
            "ok": False,
            "code": "operator_active",
            "reason": "operator_busy",
            "retryable": True,
        }

    monkeypatch.setattr("web_tab.cdp_actuation.default_actuation_port", lambda: _Port())
    monkeypatch.setattr("web_tab.operator_guard.wait_operator_idle_before_lock", _pre)
    out = await _dom_deliver(
        page,
        build_wake_pointer(thread="21207", turn=2, from_agent="maestro"),
    )
    assert out["ok"] is False
    assert out["before_lock"] is True
    assert out["reason"] == "operator_busy"
    assert out["code"] == "operator_active"


@pytest.mark.asyncio
async def test_post_lock_operator_refusal_is_deferred(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An operator refusal after the lock is deferred_streaming and stays pending."""
    from web_chat_relay.orion_wake import deliver_orion_chat_wake
    from web_chat_relay.orion_wake_owner import InMemoryWakeDeliveryOwner

    async def _busy(page: object, text: str, **kwargs: object) -> dict:
        return {
            "ok": False,
            "reason": "recent operator input or foreign draft in composer",
            "code": "operator_active",
            "after_lock": True,
        }

    monkeypatch.setattr("web_chat_relay.orion_wake._dom_deliver", _busy)
    owner = InMemoryWakeDeliveryOwner()
    claim, opts = _defer_claim_opts()
    owner.register_claim(claim)
    out = await deliver_orion_chat_wake(
        thread="21207",
        turn=12,
        from_agent="maestro",
        page=_FakePage(url=PINNED_CONVERSATION_URL),
        conversation_url=PINNED_CONVERSATION_URL,
        pipeline_options=opts,
        delivery_owner=owner,
    )
    stored = owner.paste_receipts[claim.wake_id]
    assert out["reason"] == "deferred_streaming"
    assert out.get("after_lock") is True
    assert out.get("error") == "recent operator input or foreign draft in composer"
    assert stored["reason"] == "deferred_streaming"
    assert out["reason"] != "paste_failed"
    assert classify_orion(out)[0] == "pending"


@pytest.mark.asyncio
async def test_visibility_only_refusal_is_not_deferred(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Focus or visibility alone stays paste_failed so an unfocusable tab does not retry."""
    from web_chat_relay.orion_wake import deliver_orion_chat_wake
    from web_chat_relay.orion_wake_owner import InMemoryWakeDeliveryOwner

    async def _visible(page: object, text: str, **kwargs: object) -> dict:
        return {
            "ok": False,
            "reason": "tab not created by agent has focus or is visible to the operator",
            "code": "operator_active",
            "after_lock": True,
        }

    monkeypatch.setattr("web_chat_relay.orion_wake._dom_deliver", _visible)
    owner = InMemoryWakeDeliveryOwner()
    claim, opts = _defer_claim_opts()
    claim = claim.__class__(
        wake_id="wake-defer-vis",
        agent=claim.agent,
        thread_id=claim.thread_id,
        turn_ids=claim.turn_ids,
        turn_numbers=claim.turn_numbers,
        endpoint_url=claim.endpoint_url,
    )
    opts = {**opts, "wake_id": claim.wake_id}
    owner.register_claim(claim)
    out = await deliver_orion_chat_wake(
        thread="21207",
        turn=12,
        from_agent="maestro",
        page=_FakePage(url=PINNED_CONVERSATION_URL),
        conversation_url=PINNED_CONVERSATION_URL,
        pipeline_options=opts,
        delivery_owner=owner,
    )
    stored = owner.paste_receipts[claim.wake_id]
    assert out["reason"] == "paste_failed"
    assert out.get("deferred") is not True
    assert stored["reason"] == "paste_failed"


@pytest.mark.no_dom_stub
@pytest.mark.asyncio
async def test_bring_to_front_does_not_refuse_as_operator_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The wake's own bring-to-front passes lock_holder so focus is not operator_active."""
    page = _FakePage()
    page.target_id = "tab-front"

    async def _front() -> None:
        page.brought_to_front = True

    page.bring_to_front = _front  # type: ignore[attr-defined]
    captured: dict[str, object] = {}

    class _Port:
        def list_tabs(self, *, cdp_url: str) -> dict:
            return {
                "pages": [
                    {
                        "id": "tab-front",
                        "url": PINNED,
                        "web_socket_debugger_url": "ws://127.0.0.1/devtools/page/front",
                    }
                ],
                "reachable": True,
                "error": None,
            }

    async def _pre(*_a: object, **_k: object) -> dict:
        return {"ok": True}

    async def _recheck(*_a: object, **kwargs: object) -> dict:
        captured["lock_holder"] = kwargs.get("lock_holder")
        captured["created_by_agent"] = kwargs.get("created_by_agent")
        if kwargs.get("created_by_agent") is not True:
            return {
                "ok": False,
                "code": "operator_active",
                "reason": "tab not created by agent has focus or is visible to the operator",
            }
        return {
            "ok": True,
            "probe": {
                "document_has_focus": True,
                "document_visibility": "visible",
                "operator_active": False,
            },
        }

    async def _baseline(*_a: object, **_k: object) -> dict:
        return {"ok": True, "user_count": 0}

    async def _insert(*_a: object, **_k: object) -> dict:
        return {"ok": True}

    async def _verify(*_a: object, **_k: object) -> dict:
        return {"ok": True, "receipt": "dom_committed"}

    @asynccontextmanager
    async def _lock(_target_id: str, *, timeout_s: float = 120, op: str = ""):
        yield

    monkeypatch.setattr("web_tab.cdp_actuation.default_actuation_port", lambda: _Port())
    monkeypatch.setattr(
        "web_tab.write_ops._created_by_agent_for_target", lambda **_k: True
    )
    monkeypatch.setattr("web_tab.operator_guard.wait_operator_idle_before_lock", _pre)
    monkeypatch.setattr("web_tab.operator_guard.operator_recheck_under_lock", _recheck)
    monkeypatch.setattr("web_tab.commit_verify.capture_dom_baseline", _baseline)
    monkeypatch.setattr("cursor_bridge.dom_composer_paste.dom_insert_and_send", _insert)
    monkeypatch.setattr("web_tab.commit_verify.poll_verify_committed", _verify)
    monkeypatch.setattr("web_tab.target_lock.target_write_lock", _lock)
    out = await deliver_orion_wake(
        thread="21207",
        turn=2,
        from_agent="maestro",
        page=page,
    )
    assert page.brought_to_front is True
    assert captured.get("lock_holder") is False
    assert out.get("reason") != "operator_active"
    assert out.get("reason") != "paste_failed"


@pytest.mark.asyncio
async def test_binding_open_uses_stored_url_verbatim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stored = (
        "https://chatgpt.com/g/g-p-test-orion/c/"
        "6ae44d27-06cc-83e8-8b8a-df9b819755bd"
    )
    opened_urls: list[str] = []

    class _Ctx:
        pages: list[object] = []

    class _Port:
        def list_tabs(self, *, cdp_url: str) -> dict:
            return {"pages": [], "reachable": True, "error": None}

        def create_background_tab(self, *, cdp_url: str, url: str, **kwargs: object) -> dict:
            opened_urls.append(url)
            return {
                "ok": True,
                "target_id": "tab-new",
                "url": url,
                "web_socket_debugger_url": "ws://fake/tab-new",
            }

    class _Pw:
        async def stop(self) -> None:
            return None

    async def _connect(_cdp: str):
        return _Pw(), object(), _Ctx(), None

    async def _poll(_ctx: object, conversation_id: str, **kwargs: object):
        page = _FakePage(url=stored)
        _Ctx.pages = [page]
        return page

    monkeypatch.setattr(
        "web_chat_relay.orion_wake._poll_playwright_page_for_conversation", _poll
    )
    monkeypatch.setattr("web_tab.cdp_actuation.default_actuation_port", lambda: _Port())
    monkeypatch.setattr(
        "web_tab.tab_binding_store.save_binding",
        lambda *args, **kwargs: None,
    )

    out = await deliver_orion_wake(
        thread="21207",
        turn=1,
        from_agent="maestro",
        connect_cdp=_connect,
        binding_supplied=True,
        stored_endpoint=stored,
    )
    assert out["ok"] is True, out
    assert opened_urls == [stored.split("?", 1)[0]]
    assert out.get("opened") is True


@pytest.mark.asyncio
async def test_endpoint_url_mismatch_refuses_both_strings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stored = PINNED
    wrong = "https://chatgpt.com/c/11111111-2222-3333-4444-555555555555"

    class _Ctx:
        pages: list[object] = []

    class _Port:
        def list_tabs(self, *, cdp_url: str) -> dict:
            return {"pages": [], "reachable": True, "error": None}

        def create_background_tab(self, *, cdp_url: str, url: str, **kwargs: object) -> dict:
            return {
                "ok": True,
                "target_id": "tab-new",
                "url": wrong,
                "web_socket_debugger_url": "ws://fake/tab-new",
            }

    class _Pw:
        async def stop(self) -> None:
            return None

    async def _connect(_cdp: str):
        return _Pw(), object(), _Ctx(), None

    monkeypatch.setattr("web_tab.cdp_actuation.default_actuation_port", lambda: _Port())
    monkeypatch.setattr(
        "web_tab.tab_binding_store.save_binding",
        lambda *args, **kwargs: None,
    )

    async def _poll(_ctx: object, conversation_id: str, **kwargs: object):
        return _FakePage(url=stored)

    monkeypatch.setattr(
        "web_chat_relay.orion_wake._poll_playwright_page_for_conversation", _poll
    )

    out = await deliver_orion_wake(
        thread="21207",
        turn=1,
        from_agent="maestro",
        connect_cdp=_connect,
        binding_supplied=True,
        stored_endpoint=stored,
    )
    assert out.get("reason") != "endpoint_url_mismatch"
    assert out["ok"] is True, out


@pytest.mark.asyncio
async def test_focus_refuses_close_even_for_agent_tab(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stored = PINNED
    dup_url = PINNED
    primary = _FakePage(url=stored)
    duplicate = _FakePage(url=dup_url)

    class _Ctx:
        pages = [primary, duplicate]

    rows = [
        {
            "id": "tab-primary",
            "url": stored,
            "web_socket_debugger_url": "ws://fake/primary",
        },
        {
            "id": "tab-dup",
            "url": dup_url,
            "web_socket_debugger_url": "ws://fake/dup",
        },
    ]

    class _Port:
        def list_tabs(self, *, cdp_url: str) -> dict:
            return {"pages": rows, "reachable": True, "error": None}

    def _created(*, cdp_url: str, target_id: str, **kwargs: object) -> bool:
        return target_id == "tab-dup"

    async def _pre(*_a: object, **_k: object) -> dict:
        return {"ok": True}

    close_calls: list[str] = []
    guard_calls: list[str] = []

    def _close(cdp_url: str, target_id: str) -> None:
        close_calls.append(target_id)

    async def _recheck_tracked(*_a: object, **_k: object) -> dict:
        guard_calls.append("recheck")
        return {
            "ok": True,
            "probe": {
                "document_has_focus": True,
                "document_visibility": "visible",
                "operator_active": False,
            },
        }

    monkeypatch.setattr("web_tab.cdp_actuation.default_actuation_port", lambda: _Port())
    monkeypatch.setattr(
        "web_tab.write_ops._created_by_agent_for_target", _created
    )
    monkeypatch.setattr(
        "web_tab.operator_guard.wait_operator_idle_before_lock", _pre
    )
    monkeypatch.setattr(
        "web_tab.operator_guard.operator_recheck_under_lock", _recheck_tracked
    )
    monkeypatch.setattr("web_tab.cdp_actuation._close_target", _close)

    out = await sweep_orion_duplicate_tabs(
        _Ctx(),
        stored_endpoint=stored,
        primary_page=primary,
        primary_target_id="tab-primary",
    )
    assert guard_calls == ["recheck"]
    assert out["closed"] is False
    assert close_calls == []


@pytest.mark.asyncio
async def test_synthesized_claim_url_refuses_unless_row_stored_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pytest.TempPathFactory
) -> None:
    """Claim URL built from a bare UUID must not open; a stored /c/ URL still may."""
    import os

    from agent_bus_store.db.connection import connect, init_db
    from web_chat_relay.orion_wake_owner import InMemoryWakeDeliveryOwner

    os.environ["AGENT_BUS_DB_PATH"] = str(tmp_path / "bus.sqlite")
    init_db()
    bare = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    synthesized = f"https://chatgpt.com/c/{bare}"
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO agent_endpoint_subscriptions "
            "(agent, thread_scope, state, surface, display, title, agent_id) "
            "VALUES (?,?,?,?,?,?,?)",
            ("orion", "21207", "subscribed", "cursor", ":8", None, bare),
        )
        bare_id = str(cur.lastrowid)
        pinned = (
            "https://chatgpt.com/g/g-p-6ae3debfee8481918ff3fa45b7d18d3a-ulg-orion"
            "/c/6ae44d27-06cc-83e8-8b8a-df9b819755bd"
        )
        cur = conn.execute(
            "INSERT INTO agent_endpoint_subscriptions "
            "(agent, thread_scope, state, surface, display, title, agent_id) "
            "VALUES (?,?,?,?,?,?,?)",
            ("orion", "21207", "subscribed", "cursor", ":8", pinned + "?foo=1", None),
        )
        good_id = str(cur.lastrowid)
        conn.commit()

    opened: list[str] = []

    class _Port:
        def list_tabs(self, *, cdp_url: str) -> dict:
            return {"pages": [], "reachable": True, "error": None}

        def create_background_tab(
            self, *, cdp_url: str, url: str, **kwargs: object
        ) -> dict:
            opened.append(url)
            return {
                "ok": True,
                "target_id": "tab-new",
                "url": url,
                "web_socket_debugger_url": "ws://fake/tab-new",
            }

    class _Pw:
        async def stop(self) -> None:
            return None

    async def _connect(_cdp: str):
        return _Pw(), object(), type("Ctx", (), {"pages": []})(), None

    async def _poll(_ctx, conversation_id: str, **kwargs: object):
        return _FakePage(url=pinned)

    monkeypatch.setattr("web_tab.cdp_actuation.default_actuation_port", lambda: _Port())
    monkeypatch.setattr("web_tab.tab_binding_store.save_binding", lambda *a, **k: None)
    monkeypatch.setattr(
        "web_chat_relay.orion_wake._poll_playwright_page_for_conversation", _poll
    )
    owner = InMemoryWakeDeliveryOwner()

    def _opts(wake_id: str, url: str, binding_id: str) -> dict:
        return {
            "wake_id": wake_id,
            "thread": "21207",
            "turn": 12,
            "turn_id": 912,
            "turn_ids": [912],
            "turn_numbers": [12],
            "agent": "orion",
            "conversation_url": url,
            "binding_reference": binding_id,
        }

    from agent_bus_store.db.orion_wake_owner_adapter import wake_claim_from_options

    bad_opts = _opts("wake-bare", synthesized, bare_id)
    owner.register_claim(wake_claim_from_options(bad_opts))
    refused = await deliver_orion_chat_wake(
        thread="21207",
        turn=12,
        from_agent="maestro",
        pipeline_options=bad_opts,
        delivery_owner=owner,
    )
    assert refused["reason"] == "bad_conversation_url"
    assert refused["stored_endpoint"] == bare
    assert refused["opened_url"] == synthesized
    assert opened == []

    good_opts = _opts("wake-good", pinned, good_id)
    owner.register_claim(wake_claim_from_options(good_opts))
    opened.clear()
    allowed = await deliver_orion_chat_wake(
        thread="21207",
        turn=12,
        from_agent="maestro",
        connect_cdp=_connect,
        pipeline_options=good_opts,
        delivery_owner=owner,
    )
    assert allowed["ok"] is True, allowed
    assert opened == [pinned]


@pytest.mark.asyncio
async def test_operator_tab_not_brought_to_front_when_agent_duplicate_listed_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = _FakePage(url=PINNED_CONVERSATION_URL, visibility_state="visible")
    page.target_id = "tab-operator"
    rows = [
        {
            "id": "tab-agent",
            "url": PINNED_CONVERSATION_URL,
            "web_socket_debugger_url": "ws://a",
        },
        {
            "id": "tab-operator",
            "url": PINNED_CONVERSATION_URL,
            "web_socket_debugger_url": "ws://b",
        },
    ]

    class _Port:
        def list_tabs(self, *, cdp_url: str) -> dict:
            return {"pages": rows, "reachable": True, "error": None}

    monkeypatch.setattr("web_tab.cdp_actuation.default_actuation_port", lambda: _Port())
    monkeypatch.setattr(
        "web_tab.write_ops._created_by_agent_for_target",
        lambda *, target_id, **kwargs: target_id == "tab-agent",
    )
    out = await deliver_orion_wake(
        thread="21207",
        turn=1,
        from_agent="maestro",
        page=page,
        conversation_url=PINNED_CONVERSATION_URL,
    )
    assert page.brought_to_front is False
    assert out.get("brought_to_front") is False
    assert out.get("target_id") != "tab-agent"


@pytest.mark.asyncio
async def test_unreachable_cdp_does_not_bring_to_front(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = _FakePage(url=PINNED_CONVERSATION_URL, visibility_state="hidden")

    class _Port:
        def list_tabs(self, *, cdp_url: str) -> dict:
            return {"pages": [], "reachable": False, "error": "down"}

    monkeypatch.setattr("web_tab.cdp_actuation.default_actuation_port", lambda: _Port())
    out = await deliver_orion_wake(
        thread="21207",
        turn=1,
        from_agent="maestro",
        page=page,
        conversation_url=PINNED_CONVERSATION_URL,
    )
    assert page.brought_to_front is False
    assert out.get("brought_to_front") is False


@pytest.mark.asyncio
async def test_just_opened_tab_is_not_brought_to_front(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stored = (
        "https://chatgpt.com/g/g-p-test-orion/c/"
        "6ae44d27-06cc-83e8-8b8a-df9b819755bd"
    )
    page = _FakePage(url=stored, visibility_state="hidden")

    class _Ctx:
        pages: list = []

    class _Port:
        def list_tabs(self, *, cdp_url: str) -> dict:
            return {"pages": [], "reachable": True, "error": None}

        def create_background_tab(self, *, cdp_url: str, url: str, **kwargs: object):
            return {
                "ok": True,
                "target_id": "tab-new",
                "url": "about:blank",
                "web_socket_debugger_url": "ws://fake/tab-new",
            }

    class _Pw:
        async def stop(self) -> None:
            return None

    async def _connect(_cdp: str):
        return _Pw(), object(), _Ctx(), None

    async def _poll(_ctx, conversation_id: str, **kwargs: object):
        return page

    monkeypatch.setattr("web_tab.cdp_actuation.default_actuation_port", lambda: _Port())
    monkeypatch.setattr("web_tab.tab_binding_store.save_binding", lambda *a, **k: None)
    monkeypatch.setattr(
        "web_chat_relay.orion_wake._poll_playwright_page_for_conversation", _poll
    )
    monkeypatch.setattr(
        "web_tab.write_ops._created_by_agent_for_target", lambda **kwargs: True
    )
    out = await deliver_orion_wake(
        thread="21207",
        turn=1,
        from_agent="maestro",
        connect_cdp=_connect,
        binding_supplied=True,
        stored_endpoint=stored,
    )
    assert out["ok"] is True, out
    assert page.brought_to_front is False
    assert out.get("brought_to_front") is False
    assert page.reloaded == 0


@pytest.mark.asyncio
async def test_about_blank_create_saves_provenance_and_matches_conversation_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stored = (
        "https://chatgpt.com/g/g-p-test-orion/c/"
        "6ae44d27-06cc-83e8-8b8a-df9b819755bd"
    )
    saved: list[dict] = []

    class _Ctx:
        pages: list = []

    class _Port:
        def list_tabs(self, *, cdp_url: str) -> dict:
            return {"pages": [], "reachable": True, "error": None}

        def create_background_tab(self, *, cdp_url: str, url: str, **kwargs: object):
            return {
                "ok": True,
                "target_id": "tab-new",
                "url": "about:blank",
                "web_socket_debugger_url": "ws://fake/tab-new",
            }

    class _Pw:
        async def stop(self) -> None:
            return None

    async def _connect(_cdp: str):
        return _Pw(), object(), _Ctx(), None

    async def _poll(_ctx, conversation_id: str, **kwargs: object):
        return _FakePage(url=stored)

    def _save(session, path=None):
        saved.append(
            {
                "target_id": session.target_id,
                "created_by_agent": session.created_by_agent,
                "conversation_url": session.conversation_url,
            }
        )

    monkeypatch.setattr("web_tab.cdp_actuation.default_actuation_port", lambda: _Port())
    monkeypatch.setattr("web_tab.tab_binding_store.save_binding", _save)
    monkeypatch.setattr(
        "web_chat_relay.orion_wake._poll_playwright_page_for_conversation", _poll
    )
    out = await deliver_orion_wake(
        thread="21207",
        turn=1,
        from_agent="maestro",
        connect_cdp=_connect,
        binding_supplied=True,
        stored_endpoint=stored,
    )
    assert out.get("reason") != "endpoint_url_mismatch"
    assert out["ok"] is True, out
    assert saved and saved[0]["created_by_agent"] is True
    assert saved[0]["target_id"] == "tab-new"
    assert saved[0]["conversation_url"].split("?", 1)[0] == stored


@pytest.mark.asyncio
async def test_same_url_agent_duplicate_closes_when_focus_clear(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stored = PINNED_CONVERSATION_URL
    primary = _FakePage(url=stored)
    duplicate = _FakePage(url=stored)
    rows = [
        {"id": "tab-primary", "url": stored, "web_socket_debugger_url": "ws://p"},
        {"id": "tab-dup", "url": stored, "web_socket_debugger_url": "ws://d"},
    ]
    guard_calls: list[str] = []

    class _Port:
        def list_tabs(self, *, cdp_url: str) -> dict:
            return {"pages": rows, "reachable": True, "error": None}

    async def _pre(*_a: object, **_k: object) -> dict:
        return {"ok": True}

    async def _recheck(*_a: object, **_k: object) -> dict:
        guard_calls.append("recheck")
        return {
            "ok": True,
            "probe": {
                "document_has_focus": False,
                "document_visibility": "hidden",
                "operator_active": False,
            },
        }

    closed: list[str] = []

    monkeypatch.setattr("web_tab.cdp_actuation.default_actuation_port", lambda: _Port())
    monkeypatch.setattr(
        "web_tab.write_ops._created_by_agent_for_target",
        lambda *, target_id, **kwargs: target_id == "tab-dup",
    )
    monkeypatch.setattr("web_tab.operator_guard.wait_operator_idle_before_lock", _pre)
    monkeypatch.setattr("web_tab.operator_guard.operator_recheck_under_lock", _recheck)
    monkeypatch.setattr(
        "web_tab.cdp_actuation._close_target",
        lambda cdp_url, target_id: closed.append(target_id),
    )
    out = await sweep_orion_duplicate_tabs(
        type("Ctx", (), {"pages": [primary, duplicate]})(),
        stored_endpoint=stored,
        primary_page=primary,
        primary_target_id="tab-primary",
    )
    assert guard_calls == ["recheck"]
    assert closed == ["tab-dup"]
    assert out["closed_target_ids"] == ["tab-dup"]


@pytest.mark.asyncio
async def test_close_target_runs_inside_lock_and_failure_is_refusal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    held = {"inside": False}

    class _Lock:
        async def __aenter__(self):
            held["inside"] = True

        async def __aexit__(self, *args):
            held["inside"] = False
            return False

    def _lock(*_a: object, **_k: object):
        return _Lock()

    seen: dict[str, bool] = {}

    def _close(cdp_url: str, target_id: str) -> None:
        seen["inside"] = held["inside"]
        raise RuntimeError("ws down")

    class _Port:
        def list_tabs(self, *, cdp_url: str) -> dict:
            return {
                "pages": [
                    {
                        "id": "tab-dup",
                        "url": "https://chatgpt.com/c/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                        "web_socket_debugger_url": "ws://d",
                    }
                ],
                "reachable": True,
                "error": None,
            }

    async def _pre(*_a: object, **_k: object) -> dict:
        return {"ok": True}

    async def _recheck(*_a: object, **_k: object) -> dict:
        return {
            "ok": True,
            "probe": {
                "document_has_focus": False,
                "document_visibility": "hidden",
                "operator_active": False,
            },
        }

    monkeypatch.setattr("web_tab.cdp_actuation.default_actuation_port", lambda: _Port())
    monkeypatch.setattr("web_tab.write_ops._created_by_agent_for_target", lambda **kwargs: True)
    monkeypatch.setattr("web_tab.operator_guard.wait_operator_idle_before_lock", _pre)
    monkeypatch.setattr("web_tab.operator_guard.operator_recheck_under_lock", _recheck)
    monkeypatch.setattr("web_tab.target_lock.target_write_lock", _lock)
    monkeypatch.setattr("web_tab.cdp_actuation._close_target", _close)
    out = await _guarded_close_orion_tab(
        cdp_url="http://127.0.0.1:9342",
        target_id="tab-dup",
        conversation_url="https://chatgpt.com/c/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
    )
    assert seen["inside"] is True
    assert out["ok"] is False
    assert out["reason"] == "cdp_unreachable"
    assert out["error"] == "RuntimeError"


@pytest.mark.asyncio
async def test_create_background_tab_exception_is_cdp_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import urllib.error

    stored = (
        "https://chatgpt.com/g/g-p-test-orion/c/"
        "6ae44d27-06cc-83e8-8b8a-df9b819755bd"
    )
    stopped = {"n": 0}

    class _Ctx:
        pages: list = []

    class _Port:
        def list_tabs(self, *, cdp_url: str) -> dict:
            return {"pages": [], "reachable": True, "error": None}

        def create_background_tab(self, *, cdp_url: str, url: str, **kwargs: object):
            raise urllib.error.URLError("timed out")

    class _Pw:
        async def stop(self) -> None:
            stopped["n"] += 1

    async def _connect(_cdp: str):
        return _Pw(), object(), _Ctx(), None

    monkeypatch.setattr("web_tab.cdp_actuation.default_actuation_port", lambda: _Port())
    out = await deliver_orion_wake(
        thread="21207",
        turn=1,
        from_agent="maestro",
        connect_cdp=_connect,
        binding_supplied=True,
        stored_endpoint=stored,
    )
    assert out["reason"] == "cdp_unreachable"
    assert out["error"] == "URLError"
    assert stopped["n"] == 1


@pytest.mark.asyncio
async def test_no_default_pinned_without_binding_supplied() -> None:
    out = await deliver_orion_wake(
        thread="21207",
        turn=1,
        from_agent="maestro",
    )
    assert out["reason"] == "bad_conversation_url"


_SAMPLE_DOM_CENSUS = {
    "root_tag": "DIV",
    "root_outer_html": "<div id='composer'></div>",
    "buttons": [{"text": "Send"}],
    "buttons_total": 1,
    "document_visibility_state": "visible",
    "document_has_focus": True,
    "elapsed_ms": 42,
}


def _dom_deliver_no_send_stubs(
    monkeypatch: pytest.MonkeyPatch,
    *,
    census: dict,
    page: _FakePage,
    tab_id: str = "tab-orion",
) -> None:
    class _Port:
        def list_tabs(self, *, cdp_url: str) -> dict:
            return {
                "pages": [
                    {
                        "id": tab_id,
                        "url": PINNED,
                        "web_socket_debugger_url": "ws://127.0.0.1/devtools/page/orion",
                    }
                ],
                "reachable": True,
                "error": None,
            }

    async def _insert(ws: str, *, selector: str, line: str, **_kwargs: object) -> dict:
        return {
            "ok": False,
            "reason": "no_send_button",
            "census": census,
            "pasted": True,
            "delivered": False,
            "inserted": True,
            "phase": "inserted",
            "send_clicked": False,
        }

    async def _baseline(*_a: object, **_k: object) -> dict:
        return {"user_count": 0}

    async def _pre(*_a: object, **_k: object) -> dict:
        return {"ok": True}

    async def _recheck(*_a: object, **_k: object) -> dict:
        return {"ok": True, "probe": {}}

    @asynccontextmanager
    async def _lock(_target_id: str, *, timeout_s: float = 120, op: str = ""):
        yield

    monkeypatch.setattr("web_tab.cdp_actuation.default_actuation_port", lambda: _Port())
    monkeypatch.setattr(
        "cursor_bridge.dom_composer_paste.dom_insert_and_send", _insert
    )
    monkeypatch.setattr("web_tab.commit_verify.capture_dom_baseline", _baseline)
    monkeypatch.setattr(
        "web_tab.operator_guard.wait_operator_idle_before_lock", _pre
    )
    monkeypatch.setattr(
        "web_tab.operator_guard.operator_recheck_under_lock", _recheck
    )
    monkeypatch.setattr("web_tab.target_lock.target_write_lock", _lock)
    monkeypatch.setattr(
        "web_tab.write_ops._created_by_agent_for_target", lambda **_k: True
    )


@pytest.mark.asyncio
@pytest.mark.no_dom_stub
async def test_dom_deliver_forwards_census_on_no_send_button(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = _FakePage(url=PINNED)
    census = dict(_SAMPLE_DOM_CENSUS)
    _dom_deliver_no_send_stubs(monkeypatch, census=census, page=page)
    out = await _dom_deliver(page, "wake line")
    assert out["ok"] is False
    assert out["reason"] == "no_send_button"
    assert out["pasted"] is True
    assert out["delivered"] is False
    assert out["census"] == census


@pytest.mark.asyncio
@pytest.mark.no_dom_stub
async def test_wake_inserted_no_send_preserves_pasted_and_census(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Inserted but unsent wake keeps pasted=true and forwards census metadata."""
    page = _FakePage(url=PINNED)
    page.target_id = "tab-orion"
    census = dict(_SAMPLE_DOM_CENSUS)
    _dom_deliver_no_send_stubs(monkeypatch, census=census, page=page)
    out = await deliver_orion_wake(
        thread="21207",
        turn=2,
        from_agent="maestro",
        page=page,
    )
    assert out["reason"] == "no_send_button"
    assert out.get("error") == "no_send_button"
    assert out["pasted"] is True
    assert out["delivered"] is False
    assert out["census"] == census


@pytest.mark.asyncio
@pytest.mark.no_dom_stub
async def test_dom_deliver_no_send_without_insert_metadata_is_paste_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bare no_send_button from the DOM layer must not infer insertion."""
    page = _FakePage(url=PINNED)
    census: dict = {}
    _dom_deliver_no_send_stubs(monkeypatch, census=census, page=page)

    async def _bare_no_send(
        *_a: object, **_k: object
    ) -> dict:
        return {"ok": False, "reason": "no_send_button"}

    monkeypatch.setattr(
        "cursor_bridge.dom_composer_paste.dom_insert_and_send", _bare_no_send
    )
    out = await _dom_deliver(page, "wake line")
    assert out["reason"] == "no_send_button"
    assert out.get("pasted") is not True
    assert out.get("pasted", False) is False


@pytest.mark.asyncio
@pytest.mark.no_dom_stub
async def test_dom_deliver_insertion_failure_pasted_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pre-send insertion failure must surface pasted=false through _dom_deliver."""
    page = _FakePage(url=PINNED)
    _dom_deliver_no_send_stubs(monkeypatch, census={}, page=page)

    async def _insert_fail(*_a: object, **_k: object) -> dict:
        return {
            "ok": False,
            "reason": "composer_text_mismatch",
            "pasted": False,
            "inserted": False,
        }

    monkeypatch.setattr(
        "cursor_bridge.dom_composer_paste.dom_insert_and_send", _insert_fail
    )
    out = await _dom_deliver(page, "wake line")
    assert out["pasted"] is False
    assert out["reason"] == "composer_text_mismatch"


@pytest.mark.asyncio
@pytest.mark.no_dom_stub
async def test_wake_no_send_without_insert_metadata_paste_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bare no_send_button without insert metadata must classify as paste_failed."""
    page = _FakePage(url=PINNED)
    page.target_id = "tab-orion"

    async def _bare(*_a: object, **_k: object) -> dict:
        return {"ok": False, "reason": "no_send_button"}

    _dom_deliver_no_send_stubs(monkeypatch, census={}, page=page)
    monkeypatch.setattr(
        "cursor_bridge.dom_composer_paste.dom_insert_and_send", _bare
    )
    out = await deliver_orion_wake(
        thread="21207", turn=2, from_agent="maestro", page=page
    )
    assert out["pasted"] is False
    assert out["reason"] in {"paste_failed", "no_send_button"}


def test_ledger_owner_records_unknown_for_inserted_no_send(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC4: real receipt owner maps pasted=true, delivered=false to outcome unknown."""
    from agent_bus_store.db.orion_wake_owner_adapter import (
        LedgerWakeDeliveryOwner,
        WakeClaimContext,
    )

    recorded: list[tuple[str, str]] = []

    def _record_wake_result(
        wake_id: str,
        outcome: str,
        reason: str,
        *_rest: object,
    ) -> None:
        recorded.append((outcome, reason))

    monkeypatch.setattr(
        "agent_bus_store.db.orion_wake_owner_adapter.record_wake_result",
        _record_wake_result,
    )
    owner = LedgerWakeDeliveryOwner()
    claim = WakeClaimContext(
        wake_id="wake-test-unknown",
        agent="ai_agent:orion",
        thread_id="22388",
        turn_ids=(1,),
        turn_numbers=(2,),
        endpoint_url="https://chatgpt.com/c/test",
    )
    owner.record_paste_receipt(
        claim,
        {
            "ok": False,
            "reason": "no_send_button",
            "pasted": True,
            "delivered": False,
        },
    )
    assert recorded == [("unknown", "no_send_button")]


@pytest.mark.asyncio
@pytest.mark.no_dom_stub
async def test_wake_deliver_work_insertion_refusal_paste_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Insertion failure through deliver_orion_wake stays pasted=false (not unsent send)."""
    page = _FakePage(url=PINNED)
    page.target_id = "tab-orion"

    class _Port:
        def list_tabs(self, *, cdp_url: str) -> dict:
            return {
                "pages": [
                    {
                        "id": "tab-orion",
                        "url": PINNED,
                        "web_socket_debugger_url": "ws://127.0.0.1/devtools/page/orion",
                    }
                ]
            }

    async def _insert(ws: str, *, selector: str, line: str, **_kwargs: object) -> dict:
        return {
            "ok": False,
            "reason": "work_native_unsupported",
            "pasted": False,
            "inserted": False,
            "phase": "insertion_failed",
        }

    async def _baseline(*_a: object, **_k: object) -> dict:
        return {"user_count": 0}

    async def _pre(*_a: object, **_k: object) -> dict:
        return {"ok": True}

    async def _recheck(*_a: object, **_k: object) -> dict:
        return {"ok": True, "probe": {}}

    @asynccontextmanager
    async def _lock(_target_id: str, *, timeout_s: float = 120, op: str = ""):
        yield

    monkeypatch.setattr("web_tab.cdp_actuation.default_actuation_port", lambda: _Port())
    monkeypatch.setattr(
        "cursor_bridge.dom_composer_paste.dom_insert_and_send", _insert
    )
    monkeypatch.setattr("web_tab.commit_verify.capture_dom_baseline", _baseline)
    monkeypatch.setattr(
        "web_tab.operator_guard.wait_operator_idle_before_lock", _pre
    )
    monkeypatch.setattr(
        "web_tab.operator_guard.operator_recheck_under_lock", _recheck
    )
    monkeypatch.setattr("web_tab.target_lock.target_write_lock", _lock)
    monkeypatch.setattr(
        "web_tab.write_ops._created_by_agent_for_target", lambda **_k: "agent-1"
    )

    out = await deliver_orion_wake(
        thread="21207",
        turn=2,
        from_agent="maestro",
        page=page,
    )
    assert out.get("pasted") is False
    assert out.get("reason") in {"paste_failed", "work_native_unsupported"}


def test_owner_records_insertion_unknown_before_refused_branch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Uncertain native insert must ledger unknown even when ok is false."""
    recorded: list[tuple[str, str]] = []

    def _record_wake_result(
        wake_id: str,
        outcome: str,
        reason: str,
        *_rest: object,
    ) -> None:
        recorded.append((outcome, reason))

    monkeypatch.setattr(
        "agent_bus_store.db.orion_wake_owner_adapter.record_wake_result",
        _record_wake_result,
    )
    from agent_bus_store.db.orion_wake_owner_adapter import (
        LedgerWakeDeliveryOwner,
        WakeClaimContext,
    )

    owner = LedgerWakeDeliveryOwner()
    claim = WakeClaimContext(
        wake_id="wake-insert-unknown",
        agent="ai_agent:orion",
        thread_id="22388",
        turn_ids=(1,),
        turn_numbers=(2,),
        endpoint_url="https://chatgpt.com/c/test",
    )
    owner.record_paste_receipt(
        claim,
        {
            "ok": False,
            "reason": "native_insert_timeout",
            "insertion_unknown": True,
            "pasted": False,
            "delivered": False,
            "retry_eligible": False,
        },
    )
    assert recorded == [("unknown", "native_insert_timeout")]


def _clear_binding_tab_attach_misses() -> None:
    _attach_misses.clear()


@asynccontextmanager
async def _noop_cdp_open_bind_lock(_key: str):
    yield


@pytest.mark.asyncio
async def test_open_binding_tab_no_second_create_when_cdp_row_exists_poll_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_binding_tab_attach_misses()
    stored = (
        "https://chatgpt.com/g/g-p-test-orion/c/"
        "6ae44d27-06cc-83e8-8b8a-df9b819755bd"
    )
    row = {
        "id": "tab-partial",
        "url": stored.split("?", 1)[0],
        "web_socket_debugger_url": "ws://fake/tab-partial",
    }
    create_calls = {"n": 0}

    class _Ctx:
        pages: list = []

    class _Port:
        def __init__(self) -> None:
            self._listed = False

        def list_tabs(self, *, cdp_url: str) -> dict:
            if self._listed:
                return {"pages": [row], "reachable": True, "error": None}
            return {"pages": [], "reachable": True, "error": None}

        def create_background_tab(
            self, *, cdp_url: str, url: str, **kwargs: object
        ) -> dict:
            create_calls["n"] += 1
            self._listed = True
            return {
                "ok": True,
                "target_id": "tab-partial",
                "url": url,
                "web_socket_debugger_url": "ws://fake/tab-partial",
            }

    port = _Port()

    async def _poll(_ctx: object, conversation_id: str, **kwargs: object) -> None:
        return None

    monkeypatch.setattr("web_tab.cdp_actuation.default_actuation_port", lambda: port)
    monkeypatch.setattr("web_tab.tab_binding_store.save_binding", lambda *a, **k: None)
    monkeypatch.setattr(
        "web_chat_relay.orion_wake._poll_playwright_page_for_conversation", _poll
    )

    ctx = _Ctx()
    first = await _open_orion_binding_tab(ctx, stored_endpoint=stored)
    assert first["reason"] == "conversation_missing"
    assert first.get("target_id") == "tab-partial"
    assert first.get("retryable") is True
    assert create_calls["n"] == 1

    second = await _open_orion_binding_tab(ctx, stored_endpoint=stored)
    assert second["reason"] == "conversation_missing"
    assert second.get("target_id") == "tab-partial"
    assert second.get("retryable") is True
    assert create_calls["n"] == 1


@pytest.mark.asyncio
async def test_tab_row_matches_conversation_id_requires_chatgpt_host() -> None:
    cid = "6ae44d27-06cc-83e8-8b8a-df9b819755bd"
    assert (
        _tab_row_matches_conversation_id(
            {"url": f"https://example.com/c/{cid}"},
            cid,
        )
        is False
    )
    assert (
        _tab_row_matches_conversation_id(
            {"url": f"https://chatgpt.com/c/{cid}"},
            cid,
        )
        is True
    )


@pytest.mark.asyncio
async def test_open_binding_tab_relist_poll_miss_increments_attach_misses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_binding_tab_attach_misses()
    cid = "6ae44d27-06cc-83e8-8b8a-df9b819755bd"
    stored = f"https://chatgpt.com/g/g-p-test-orion/c/{cid}"
    row = {
        "id": "tab-relist",
        "url": stored.split("?", 1)[0],
        "web_socket_debugger_url": "ws://fake/tab-relist",
    }
    create_calls = {"n": 0}

    class _Ctx:
        pages: list = []

    class _Port:
        def __init__(self) -> None:
            self._list_calls = 0

        def list_tabs(self, *, cdp_url: str) -> dict:
            self._list_calls += 1
            if self._list_calls == 1:
                return {"pages": [], "reachable": True, "error": None}
            return {"pages": [row], "reachable": True, "error": None}

        def create_background_tab(
            self, *, cdp_url: str, url: str, **kwargs: object
        ) -> dict:
            create_calls["n"] += 1
            return {"ok": True, "target_id": "tab-new", "url": url}

    port = _Port()

    async def _poll(_ctx: object, conversation_id: str, **kwargs: object) -> None:
        return None

    monkeypatch.setattr("web_tab.cdp_actuation.default_actuation_port", lambda: port)
    monkeypatch.setattr("web_tab.tab_binding_store.save_binding", lambda *a, **k: None)
    monkeypatch.setattr(
        "web_chat_relay.orion_wake._poll_playwright_page_for_conversation", _poll
    )
    monkeypatch.setattr(
        "web_tab.target_lock.cdp_open_bind_lock", _noop_cdp_open_bind_lock
    )

    ctx = _Ctx()
    first = await _open_orion_binding_tab(ctx, stored_endpoint=stored)
    assert first["reason"] == "conversation_missing"
    assert first.get("retryable") is True
    assert create_calls["n"] == 0

    second = await _open_orion_binding_tab(ctx, stored_endpoint=stored)
    assert second["reason"] == "conversation_missing"
    assert second.get("retryable") is True
    assert create_calls["n"] == 0

    third = await _open_orion_binding_tab(ctx, stored_endpoint=stored)
    assert third["ok"] is False
    assert third["reason"] == "conversation_tab_unattachable"
    assert third.get("retryable") is False
    assert create_calls["n"] == 0


@pytest.mark.asyncio
async def test_open_binding_tab_attach_miss_counter_clears_on_poll_hit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_binding_tab_attach_misses()
    cid = "6ae44d27-06cc-83e8-8b8a-df9b819755bd"
    stored = f"https://chatgpt.com/g/g-p-test-orion/c/{cid}"
    row = {
        "id": "tab-cycle",
        "url": stored.split("?", 1)[0],
        "web_socket_debugger_url": "ws://fake/tab-cycle",
    }
    poll_hits = {"n": 0}

    class _Ctx:
        pages: list = []

    class _Port:
        def list_tabs(self, *, cdp_url: str) -> dict:
            return {"pages": [row], "reachable": True, "error": None}

    async def _poll(_ctx: object, conversation_id: str, **kwargs: object) -> object:
        poll_hits["n"] += 1
        if poll_hits["n"] == 4:
            return type("Page", (), {"url": stored, "target_id": "tab-cycle"})()
        return None

    monkeypatch.setattr(
        "web_tab.cdp_actuation.default_actuation_port", lambda: _Port()
    )
    monkeypatch.setattr(
        "web_chat_relay.orion_wake._poll_playwright_page_for_conversation", _poll
    )

    ctx = _Ctx()
    for i in range(3):
        out = await _open_orion_binding_tab(ctx, stored_endpoint=stored)
        if i < 2:
            assert out["reason"] == "conversation_missing"
            assert out.get("retryable") is True
        else:
            assert out["reason"] == "conversation_tab_unattachable"

    attach = await _open_orion_binding_tab(ctx, stored_endpoint=stored)
    assert attach["ok"] is True

    for i in range(2):
        out = await _open_orion_binding_tab(ctx, stored_endpoint=stored)
        assert out["reason"] == "conversation_missing"
        assert out.get("retryable") is True

    final = await _open_orion_binding_tab(ctx, stored_endpoint=stored)
    assert final["reason"] == "conversation_tab_unattachable"
    assert final.get("retryable") is False


@pytest.mark.asyncio
async def test_open_binding_tab_non_chatgpt_cdp_row_triggers_create_not_unattachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_binding_tab_attach_misses()
    cid = "6ae44d27-06cc-83e8-8b8a-df9b819755bd"
    stored = f"https://chatgpt.com/g/g-p-test-orion/c/{cid}"
    foreign_row = {
        "id": "tab-foreign",
        "url": f"https://example.com/c/{cid}",
        "web_socket_debugger_url": "ws://fake/tab-foreign",
    }
    create_calls = {"n": 0}

    class _Ctx:
        pages: list = []

    class _Port:
        def list_tabs(self, *, cdp_url: str) -> dict:
            return {"pages": [foreign_row], "reachable": True, "error": None}

        def create_background_tab(
            self, *, cdp_url: str, url: str, **kwargs: object
        ) -> dict:
            create_calls["n"] += 1
            return {
                "ok": True,
                "target_id": "tab-created",
                "url": url,
                "web_socket_debugger_url": "ws://fake/tab-created",
            }

    async def _poll(_ctx: object, conversation_id: str, **kwargs: object) -> None:
        return None

    monkeypatch.setattr("web_tab.cdp_actuation.default_actuation_port", lambda: _Port())
    monkeypatch.setattr("web_tab.tab_binding_store.save_binding", lambda *a, **k: None)
    monkeypatch.setattr(
        "web_chat_relay.orion_wake._poll_playwright_page_for_conversation", _poll
    )
    monkeypatch.setattr(
        "web_tab.target_lock.cdp_open_bind_lock", _noop_cdp_open_bind_lock
    )

    ctx = _Ctx()
    out = await _open_orion_binding_tab(ctx, stored_endpoint=stored)
    assert out["reason"] == "conversation_missing"
    assert out.get("retryable") is True
    assert create_calls["n"] == 1
    assert out["reason"] != "conversation_tab_unattachable"
