"""Offline tests for Orion pointer wakes (fake page, never live chatgpt.com)."""

from __future__ import annotations

import re

import pytest
from bus_watch.endpoint_wake_transport import classify_orion
from web_chat_relay.orion_wake import (
    PINNED_CONVERSATION_ID,
    PINNED_CONVERSATION_URL,
    _paste_and_send,
    build_wake_pointer,
    conversation_id_from_url,
    deliver_orion_wake,
    holder_conversation_url,
)

pytestmark = pytest.mark.offline

PINNED = PINNED_CONVERSATION_URL


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
        if self.keyboard.inserted:
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
    assert "21207" in page.keyboard.inserted[0]
    assert "turn 7" in page.keyboard.inserted[0]
    assert (
        "maestro" in page.keyboard.inserted[0].lower()
        or "web-anthropic" in page.keyboard.inserted[0]
    )


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
    assert page.keyboard.inserted


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
    assert not page.keyboard.inserted


@pytest.mark.asyncio
async def test_conversation_missing_wrong_tab() -> None:
    page = _FakePage(url="https://chatgpt.com/plugins")
    out = await deliver_orion_wake(
        thread="21207", turn=1, from_agent="maestro", page=page
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
async def test_hidden_tab_with_empty_composer_brings_to_front_and_reloads_before_paste() -> (
    None
):
    page = _FakePage(visibility_state="hidden", composer_draft="")
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
    assert page.keyboard.inserted


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
    assert not page.keyboard.inserted


@pytest.mark.asyncio
async def test_paste_and_send_clicks_steer_when_visible() -> None:
    page = _FakePage(streaming=True, steer_visible=True, send_visible=False)
    await _paste_and_send(page, "wake line", layout="docked")
    assert "steer" in page.clicked


@pytest.mark.asyncio
async def test_paste_and_send_ignores_steer_button_when_not_streaming() -> None:
    page = _FakePage(streaming=False, steer_visible=True, send_visible=True)
    await _paste_and_send(page, "wake line", layout="docked")
    assert "steer" not in page.clicked
    assert page.clicked


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
    assert page.keyboard.inserted


@pytest.mark.asyncio
async def test_orion_echo_drop() -> None:
    page = _FakePage()
    out = await deliver_orion_wake(
        thread="21207", turn=1, from_agent="orion", page=page
    )
    assert out["reason"] == "orion_echo_drop"
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
