"""Offline tests for Orion hop (fake page, never live chatgpt.com)."""

from __future__ import annotations

import re
from unittest.mock import AsyncMock, patch

import pytest
from web_chat_relay.orion_hop import (
    STANDING_HANDOFF_URI,
    build_hop_paste_text,
    orion_hop,
)
from web_chat_relay.orion_wake import PINNED_CONVERSATION_URL

pytestmark = pytest.mark.offline

BIRTH_FIXTURE = b"Orion birth prompt fixture.\n"
NEW_CONVERSATION_ID = "11111111-2222-3333-4444-555555555555"
NEW_URL = f"https://chatgpt.com/c/{NEW_CONVERSATION_ID}"


def _dialog_meta(
    *,
    text: str = "",
    aria: str = "",
    labels: list[str] | None = None,
    floating: bool | None = None,
    connector: bool | None = None,
) -> dict:
    labels = list(labels or [])
    hay = f"{text} {aria}".lower()
    if floating is None:
        floating = "join ulg council" in hay
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
        "composersInDlg": 0,
    }


class _FakeKeyboard:
    def __init__(self, page: _FakeHopPage) -> None:
        self._page = page
        self.inserted: list[str] = []

    async def press(self, _key: str) -> None:
        return None

    async def insert_text(self, text: str) -> None:
        self.inserted.append(text)
        self._page.pasted = True


class _FakeLocator:
    def __init__(
        self,
        page: _FakeHopPage,
        *,
        count: int = 1,
        visible: bool = True,
        labels: list[str] | None = None,
        locked: bool = False,
        effort_index: int | None = None,
    ) -> None:
        self._page = page
        self._count = count
        self._visible = visible
        self._labels = list(labels or [])
        self._locked = locked
        self._index = 0
        self._effort_index = effort_index

    @property
    def first(self) -> _FakeLocator:
        return self

    @property
    def last(self) -> _FakeLocator:
        return self

    async def count(self) -> int:
        return self._count

    async def is_visible(self) -> bool:
        return self._visible and self._count > 0

    async def is_disabled(self) -> bool:
        return self._locked

    async def click(self) -> None:
        self._page.clicked.append("click")
        if self._page._new_chat_target:
            self._page.url = NEW_URL
            self._page._new_chat_target = False
        if self._page._picker_target:
            self._page._picker_open = True
            self._page._picker_target = False
        effort_idx = self._effort_index
        if effort_idx is None and self._page._effort_click_index is not None:
            effort_idx = self._page._effort_click_index
        if effort_idx is not None:
            opts = self._page.effort_options
            if 0 <= effort_idx < len(opts):
                self._page.model_label = opts[effort_idx]["label"]
            self._page._effort_click_index = None
        if (
            self._page.approval_click_index is not None
            and self._page._dialog_i < len(self._page.approval_dialogs)
        ):
            self._page._dialog_i += 1
            self._page.approval_click_index = None

    async def press(self, key: str) -> None:
        await self._page.keyboard.press(key)

    def locator(self, _sel: str) -> _FakeLocator:
        n = len(self._page._active_labels()) or 1
        return _FakeLocator(self._page, count=n)

    def nth(self, index: int) -> _FakeLocator:
        self._page.approval_click_index = index
        self._page._effort_click_index = index
        self._page._active_dialog_index = index
        if self._labels and index < len(self._labels):
            return _FakeLocator(
                self._page,
                count=1,
                labels=[self._labels[index]],
                locked=bool(self._page.effort_options[index].get("locked"))
                if index < len(self._page.effort_options)
                else False,
            )
        return self

    async def inner_text(self) -> str:
        if len(self._labels) == 1:
            return self._labels[0]
        if self._labels and self._page._effort_click_index is None:
            idx = self._page.approval_click_index
            if idx is not None and idx < len(self._labels):
                return self._labels[idx]
        if self._page._label_target:
            return self._page.model_label
        if self._page._picker_target or self._page._picker_open:
            return self._page.model_label
        idx = self._page.approval_click_index
        buttons = self._page._active_labels()
        if idx is not None and idx < len(buttons):
            return buttons[idx]
        return self._page.model_label


class _FakeHopEffortRoot(_FakeLocator):
    def nth(self, index: int) -> _FakeLocator:
        opts = self._page.effort_options
        label = opts[index]["label"] if index < len(opts) else ""
        locked = bool(opts[index].get("locked")) if index < len(opts) else False
        return _FakeLocator(
            self._page,
            count=1,
            labels=[label],
            locked=locked,
            effort_index=index,
        )


class _FakeHopPage:
    def __init__(
        self,
        *,
        url: str = PINNED_CONVERSATION_URL,
        model_label: str = "GPT-4o",
        effort_options: list[dict] | None = None,
        preflight_dialogs: list[dict] | None = None,
        approval_dialogs: list[list[str]] | None = None,
        skip_new_chat_control: bool = False,
    ) -> None:
        self.url = url
        self.model_label = model_label
        self.effort_options = list(
            effort_options
            if effort_options is not None
            else [{"label": "Pro", "locked": False}]
        )
        self.preflight_dialogs = list(preflight_dialogs or [])
        self.approval_dialogs = list(approval_dialogs or [])
        self._picker_open = False
        self._new_chat_target = False
        self._picker_target = False
        self._label_target = False
        self._dialog_i = 0
        self.approval_click_index: int | None = None
        self._effort_click_index: int | None = None
        self._active_dialog_index = 0
        self.pasted = False
        self.clicked: list[str] = []
        self.repin_calls: list[str] = []
        self.skip_new_chat_control = skip_new_chat_control
        self.keyboard = _FakeKeyboard(self)

    async def repin_conversation(self, url: str) -> None:
        self.repin_calls.append(url)

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
            )
            entries.append({"index": i, **meta})
        return entries

    def locator(self, selector: str) -> _FakeLocator:
        self._new_chat_target = False
        self._picker_target = False
        self._label_target = False
        if "new-chat-in-project" in selector:
            self._new_chat_target = True
            return _FakeLocator(self, count=0 if self.skip_new_chat_control else 1)
        if "model-picker-label" in selector:
            self._label_target = True
            return _FakeLocator(self, count=1)
        if "model-picker" in selector:
            self._picker_target = True
            return _FakeLocator(self, count=1)
        if "effort-option" in selector:
            if not self._picker_open:
                return _FakeLocator(self, count=0)
            labels = [o["label"] for o in self.effort_options]
            return _FakeHopEffortRoot(self, count=len(labels), labels=labels)
        if "dialog" in selector:
            n = len(self._active_labels()) or len(self.preflight_dialogs) or 1
            return _FakeLocator(self, count=max(n, 1))
        if "button" in selector and "Send" not in selector:
            n = len(self._active_labels()) or 1
            return _FakeLocator(self, count=n)
        return _FakeLocator(self)

    async def evaluate(self, expression: str) -> object:
        if "dialogs.find" in expression and "d.connector" in expression:
            for ent in self._scan_dialogs():
                if ent.get("connector"):
                    return {
                        "present": True,
                        "buttons": ent["labels"],
                        "index": ent["index"],
                    }
            return {"present": False, "buttons": []}
        if "composersInDlg" in expression:
            return {"dialogs": [{"index": i, **ent} for i, ent in enumerate(self._scan_dialogs())]}
        return {}

    async def wait_for_timeout(self, _ms: int) -> None:
        if self.keyboard.inserted:
            self.pasted = True


@pytest.mark.asyncio
async def test_pro_selectable_and_paste_includes_handoff_line() -> None:
    page = _FakeHopPage(
        model_label="GPT-4o",
        effort_options=[{"label": "Pro", "locked": False}],
        approval_dialogs=[
            ["Deny", "Allow ulg-code for this conversation", "Always"],
        ],
    )
    out = await orion_hop(page, BIRTH_FIXTURE)
    assert out["ok"] is True, out
    assert out["effort"]["label_before"] == "GPT-4o"
    assert out["effort"]["label_after"] == "Pro"
    assert page.url == NEW_URL
    assert page.url != PINNED_CONVERSATION_URL
    expected = build_hop_paste_text(BIRTH_FIXTURE)
    assert page.keyboard.inserted[0] == expected
    assert page.keyboard.inserted[0].endswith(STANDING_HANDOFF_URI)


@pytest.mark.asyncio
async def test_pro_locked_falls_back_to_highest_thinking_effort() -> None:
    page = _FakeHopPage(
        model_label="Auto",
        effort_options=[
            {"label": "Pro", "locked": True},
            {"label": "Standard", "locked": False},
            {"label": "Extra high", "locked": False},
        ],
        approval_dialogs=[],
    )
    out = await orion_hop(page, BIRTH_FIXTURE)
    assert out["ok"] is True, out
    assert out["effort"]["pro_locked"] is True
    assert out["effort"]["selected_label"] == "Extra high"
    assert out["effort"]["label_after"] == "Extra high"


@pytest.mark.asyncio
async def test_two_connector_dialogs_ulg_code_then_life() -> None:
    page = _FakeHopPage(
        approval_dialogs=[
            ["Deny", "Allow ulg-code for this conversation", "Always"],
            ["Deny", "Allow life for this conversation", "Always"],
        ],
    )
    out = await orion_hop(page, BIRTH_FIXTURE)
    assert out["ok"] is True, out
    receipts = out["connector_receipts"]
    assert len(receipts) == 2
    assert receipts[0]["connector"] == "ulg-code"
    assert "ulg-code" in receipts[0]["option_clicked"]
    assert receipts[1]["connector"] == "life"
    assert "life" in receipts[1]["option_clicked"]


@pytest.mark.asyncio
async def test_unknown_dialog_refused_before_paste() -> None:
    page = _FakeHopPage(
        preflight_dialogs=[
            _dialog_meta(text="Delete this project?", labels=["Cancel", "OK"]),
        ],
    )
    out = await orion_hop(page, BIRTH_FIXTURE)
    assert out["reason"] == "non_connector_dialog"
    assert out["pasted"] is False
    assert not page.keyboard.inserted


@pytest.mark.asyncio
async def test_hop_does_not_repin_before_registration() -> None:
    page = _FakeHopPage(approval_dialogs=[])
    repin_mock = AsyncMock()
    with patch(
        "web_chat_relay.orion_hop.repin_orion_wake_conversation",
        repin_mock,
    ):
        out = await orion_hop(page, BIRTH_FIXTURE)
    assert out["ok"] is True, out
    repin_mock.assert_not_called()
    assert not page.repin_calls

