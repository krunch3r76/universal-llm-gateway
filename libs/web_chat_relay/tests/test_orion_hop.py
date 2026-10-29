"""Offline tests for Orion hop (fake page, never live chatgpt.com)."""

from __future__ import annotations

import re
import threading
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from web_chat_relay.orion_hop import (
    ORION_IDENTITY_TOKEN,
    ORION_REGISTRATION_SUBJECT,
    STANDING_HANDOFF_URI,
    OrionWakeDedupeLedger,
    OrionWakeTargetRollback,
    build_hop_attempt,
    build_hop_paste_text,
    build_identity_pointer_line,
    deliver_orion_chat_wake,
    orion_hop,
    parse_orion_seat_registration,
    preflight_standing_handoff,
    repin_orion_wake_conversation,
    resolve_orion_wake_conversation_url,
)
from web_chat_relay.orion_hop_storage import OrionHopStorage
from web_chat_relay.orion_wake import PINNED_CONVERSATION_URL

pytestmark = pytest.mark.offline

BIRTH_FIXTURE = b"Orion birth prompt fixture.\n"
HANDOFF_SHA = "413806d1e263a8d285974cd32ba445c70f090d05ed98d6254cb102a8dce26596"
NEW_CONVERSATION_ID = "11111111-2222-3333-4444-555555555555"
NEW_URL = f"https://chatgpt.com/c/{NEW_CONVERSATION_ID}"
SECOND_CONVERSATION_ID = "22222222-3333-4444-5555-666666666666"
SECOND_URL = f"https://chatgpt.com/c/{SECOND_CONVERSATION_ID}"


async def _handoff_ok() -> dict:
    return {"ok": True, "read_sha256": HANDOFF_SHA}


async def _handoff_missing() -> dict:
    return {"ok": False, "error": "File not found"}


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
            self._page.url = self._page._next_new_chat_url()
            self._page._new_chat_index += 1
            self._page._reset_approval_state_for_new_conversation()
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
        new_chat_urls: list[str] | None = None,
    ) -> None:
        self.url = url
        self.model_label = model_label
        self.effort_options = list(
            effort_options
            if effort_options is not None
            else [{"label": "Pro", "locked": False}]
        )
        self._approval_template = list(approval_dialogs or [])
        self.approval_dialogs = list(self._approval_template)
        self.preflight_dialogs = list(preflight_dialogs or [])
        self._picker_open = False
        self._new_chat_target = False
        self._picker_target = False
        self._label_target = False
        self._dialog_i = 0
        self.approval_click_index: int | None = None
        self._effort_click_index: int | None = None
        self.pasted = False
        self.clicked: list[str] = []
        self.repin_calls: list[str] = []
        self.skip_new_chat_control = skip_new_chat_control
        self._new_chat_urls = list(new_chat_urls or [NEW_URL, SECOND_URL])
        self._new_chat_index = 0
        self.approval_runs = 0
        self.keyboard = _FakeKeyboard(self)

    def _next_new_chat_url(self) -> str:
        idx = min(self._new_chat_index, len(self._new_chat_urls) - 1)
        return self._new_chat_urls[idx]

    def _reset_approval_state_for_new_conversation(self) -> None:
        self.approval_dialogs = list(self._approval_template)
        self._dialog_i = 0
        self.pasted = False
        self.approval_runs = 0

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
            self.approval_runs += 1


@pytest.mark.asyncio
async def test_pro_selectable_and_paste_includes_identity_pointer() -> None:
    page = _FakeHopPage(
        model_label="GPT-4o",
        effort_options=[{"label": "Pro", "locked": False}],
        approval_dialogs=[
            ["Deny", "Allow ulg-code for this conversation", "Always"],
        ],
    )
    out = await orion_hop(page, BIRTH_FIXTURE, read_handoff=_handoff_ok)
    assert out["ok"] is True, out
    assert out["effort"]["label_after"] == "Pro"
    assert page.url == NEW_URL
    expected = build_hop_paste_text(
        BIRTH_FIXTURE,
        old_url=PINNED_CONVERSATION_URL,
        new_url=NEW_URL,
        handoff_read_sha256=HANDOFF_SHA,
    )
    assert page.keyboard.inserted[0] == expected
    pointer = build_identity_pointer_line(
        old_url=PINNED_CONVERSATION_URL,
        new_url=NEW_URL,
        handoff_read_sha256=HANDOFF_SHA,
    )
    assert ORION_IDENTITY_TOKEN in pointer
    assert PINNED_CONVERSATION_URL in pointer
    assert NEW_URL in pointer


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
    out = await orion_hop(page, BIRTH_FIXTURE, read_handoff=_handoff_ok)
    assert out["ok"] is True, out
    assert out["effort"]["pro_locked"] is True
    assert out["effort"]["selected_label"] == "Extra high"


@pytest.mark.asyncio
async def test_two_connector_dialogs_ulg_code_then_life() -> None:
    page = _FakeHopPage(
        approval_dialogs=[
            ["Deny", "Allow ulg-code for this conversation", "Always"],
            ["Deny", "Allow life for this conversation", "Always"],
        ],
    )
    out = await orion_hop(page, BIRTH_FIXTURE, read_handoff=_handoff_ok)
    assert out["ok"] is True, out
    receipts = out["connector_receipts"]
    assert len(receipts) == 2
    assert receipts[0]["connector"] == "ulg-code"
    assert receipts[1]["connector"] == "life"


@pytest.mark.asyncio
async def test_unknown_dialog_refused_before_paste() -> None:
    page = _FakeHopPage(
        preflight_dialogs=[
            _dialog_meta(text="Delete this project?", labels=["Cancel", "OK"]),
        ],
    )
    out = await orion_hop(page, BIRTH_FIXTURE, read_handoff=_handoff_ok)
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
        out = await orion_hop(page, BIRTH_FIXTURE, read_handoff=_handoff_ok)
    assert out["ok"] is True, out
    repin_mock.assert_not_called()
    assert not page.repin_calls


@pytest.mark.asyncio
async def test_handoff_missing_refuses_before_new_chat() -> None:
    page = _FakeHopPage()
    out = await orion_hop(page, BIRTH_FIXTURE, read_handoff=_handoff_missing)
    assert out["reason"] == "handoff_missing"
    assert page.url == PINNED_CONVERSATION_URL
    assert not page.keyboard.inserted


@pytest.mark.asyncio
async def test_seat_registration_requires_identity_old_new_and_handoff_sha() -> None:
    body = (
        f"TYPE: SEAT_REGISTRATION\n"
        f"identity: {ORION_IDENTITY_TOKEN}\n"
        f"old: {PINNED_CONVERSATION_URL}\n"
        f"new: {NEW_URL}\n"
        f"fs read {STANDING_HANDOFF_URI} read_sha256 {HANDOFF_SHA}\n"
    )
    reg = {"from": "orion", "subject": "SEAT_REGISTRATION|orion", "body": body}
    parsed = parse_orion_seat_registration(reg)
    assert parsed is not None
    assert parsed["handoff_read_sha256"] == HANDOFF_SHA
    assert parsed["old_conversation_url"] == PINNED_CONVERSATION_URL
    assert parse_orion_seat_registration({**reg, "from": "maestro"}) is None
    assert parse_orion_seat_registration({**reg, "body": body.replace(HANDOFF_SHA, "")}) is None
    assert (
        parse_orion_seat_registration(
            {**reg, "subject": "SEAT_REGISTRATION|different-seat"}
        )
        is None
    )


def test_wake_target_commits_only_after_bound_smoke() -> None:
    attempt = build_hop_attempt(
        old_url=PINNED_CONVERSATION_URL,
        new_url=NEW_URL,
        handoff_read_sha256=HANDOFF_SHA,
    )
    holder = OrionWakeTargetRollback(PINNED_CONVERSATION_URL)
    fail = holder.commit_after_smoke(
        candidate_url=NEW_URL,
        smoke={"ok": True, "delivered": True},
        attempt=attempt,
    )
    assert fail["reason"] == "smoke_not_bound"
    assert holder.wake_target_url == PINNED_CONVERSATION_URL
    ok = holder.commit_after_smoke(
        candidate_url=NEW_URL,
        smoke={
            "ok": True,
            "delivered": True,
            "attempt_id": attempt.attempt_id,
            "candidate_url": NEW_URL,
        },
        attempt=attempt,
    )
    assert ok["ok"] is True
    assert holder.wake_target_url == NEW_URL


@pytest.mark.asyncio
async def test_wake_dedupe_duplicate_suppressed_returns_first_receipt(
    tmp_path: Path,
) -> None:
    store = OrionHopStorage(root=tmp_path / "orion-a")
    ledger = OrionWakeDedupeLedger(store)
    calls = 0

    async def _deliver() -> dict:
        nonlocal calls
        calls += 1
        return {"ok": True, "delivered": True, "pasted": True, "n": calls}

    first = await ledger.deliver(thread="21207", turn=9, endpoint_url=NEW_URL, deliver_fn=_deliver)
    second = await ledger.deliver(thread="21207", turn=9, endpoint_url=NEW_URL, deliver_fn=_deliver)
    assert first["n"] == 1
    assert second["reason"] == "duplicate_suppressed"
    assert second["first_receipt"]["n"] == 1
    assert calls == 1


@pytest.mark.asyncio
async def test_wake_dedupe_survives_storage_reopen(tmp_path: Path) -> None:
    root = tmp_path / "orion-reopen"
    store = OrionHopStorage(root=root)
    ledger = OrionWakeDedupeLedger(store)
    calls = 0

    async def _deliver() -> dict:
        nonlocal calls
        calls += 1
        return {"ok": True, "delivered": True, "pasted": True}

    await ledger.deliver(thread="21207", turn=3, endpoint_url=NEW_URL, deliver_fn=_deliver)
    ledger2 = OrionWakeDedupeLedger(OrionHopStorage(root=root))
    second = await ledger2.deliver(
        thread="21207", turn=3, endpoint_url=NEW_URL, deliver_fn=_deliver
    )
    assert second["reason"] == "duplicate_suppressed"
    assert calls == 1


def test_concurrent_claim_allows_one_delivery(tmp_path: Path) -> None:
    store = OrionHopStorage(root=tmp_path / "orion-conc")
    barrier = threading.Barrier(2)
    statuses: list[str] = []

    def _worker() -> None:
        barrier.wait()
        status, _first = store.claim_delivery(
            thread="21207", turn=5, endpoint_url=NEW_URL
        )
        statuses.append(status)

    t1 = threading.Thread(target=_worker)
    t2 = threading.Thread(target=_worker)
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    assert statuses.count("claim") == 1
    assert len(statuses) == 2
    assert "duplicate" in statuses or "in_flight" in statuses


@pytest.mark.asyncio
async def test_repin_without_writer_fails_closed() -> None:
    body = (
        f"TYPE: SEAT_REGISTRATION\n"
        f"identity: {ORION_IDENTITY_TOKEN}\n"
        f"old: {PINNED_CONVERSATION_URL}\n"
        f"new: {NEW_URL}\n"
        f"fs read {STANDING_HANDOFF_URI} read_sha256 {HANDOFF_SHA}\n"
    )
    reg = {"from": "orion", "subject": ORION_REGISTRATION_SUBJECT, "body": body}
    attempt = build_hop_attempt(
        old_url=PINNED_CONVERSATION_URL,
        new_url=NEW_URL,
        handoff_read_sha256=HANDOFF_SHA,
    )
    smoke = {
        "ok": True,
        "delivered": True,
        "attempt_id": attempt.attempt_id,
        "candidate_url": NEW_URL,
    }
    out = await repin_orion_wake_conversation(
        object(),
        registration=reg,
        attempt=attempt,
        smoke_receipt=smoke,
    )
    assert out["ok"] is False
    assert out["reason"] == "wake_target_writer_missing"


@pytest.mark.asyncio
async def test_repin_cas_conflict_and_readback(tmp_path: Path) -> None:
    store = OrionHopStorage(root=tmp_path / "orion-cas")
    store.compare_and_set_wake_target(
        expected_old=PINNED_CONVERSATION_URL,
        new_url=SECOND_URL,
    )
    body = (
        f"TYPE: SEAT_REGISTRATION\n"
        f"identity: {ORION_IDENTITY_TOKEN}\n"
        f"old: {PINNED_CONVERSATION_URL}\n"
        f"new: {NEW_URL}\n"
        f"fs read {STANDING_HANDOFF_URI} read_sha256 {HANDOFF_SHA}\n"
    )
    reg = {"from": "orion", "subject": ORION_REGISTRATION_SUBJECT, "body": body}
    attempt = build_hop_attempt(
        old_url=PINNED_CONVERSATION_URL,
        new_url=NEW_URL,
        handoff_read_sha256=HANDOFF_SHA,
    )
    smoke = {
        "ok": True,
        "delivered": True,
        "attempt_id": attempt.attempt_id,
        "candidate_url": NEW_URL,
    }

    class _Writer:
        async def write_wake_target(self, *, expected_old: str, new_url: str) -> dict:
            return {"ok": True, "wake_target_url": new_url}

    class _Reader:
        async def read_wake_target(self) -> str:
            return SECOND_URL

    out = await repin_orion_wake_conversation(
        object(),
        registration=reg,
        attempt=attempt,
        smoke_receipt=smoke,
        storage=store,
        writer=_Writer(),
        reader=_Reader(),
    )
    assert out["reason"] == "wake_target_stale"


@pytest.mark.asyncio
async def test_repin_commits_with_writer_and_smoke(tmp_path: Path) -> None:
    store = OrionHopStorage(root=tmp_path / "orion-repin")
    body = (
        f"TYPE: SEAT_REGISTRATION\n"
        f"identity: {ORION_IDENTITY_TOKEN}\n"
        f"old: {PINNED_CONVERSATION_URL}\n"
        f"new: {NEW_URL}\n"
        f"fs read {STANDING_HANDOFF_URI} read_sha256 {HANDOFF_SHA}\n"
    )
    reg = {"from": "orion", "subject": ORION_REGISTRATION_SUBJECT, "body": body}
    attempt = build_hop_attempt(
        old_url=PINNED_CONVERSATION_URL,
        new_url=NEW_URL,
        handoff_read_sha256=HANDOFF_SHA,
    )
    smoke = {
        "ok": True,
        "delivered": True,
        "attempt_id": attempt.attempt_id,
        "candidate_url": NEW_URL,
    }
    written: list[str] = []

    class _Writer:
        async def write_wake_target(self, *, expected_old: str, new_url: str) -> dict:
            written.append(new_url)
            return {"ok": True, "wake_target_url": new_url}

    out = await repin_orion_wake_conversation(
        object(),
        registration=reg,
        attempt=attempt,
        smoke_receipt=smoke,
        storage=store,
        writer=_Writer(),
    )
    assert out["ok"] is True, out
    assert written == [NEW_URL]
    assert resolve_orion_wake_conversation_url(None, storage=store) == NEW_URL


@pytest.mark.asyncio
async def test_deliver_orion_chat_wake_integration_uses_ledger(tmp_path: Path) -> None:
    from web_chat_relay.tests.test_orion_wake import _FakePage

    store = OrionHopStorage(root=tmp_path / "orion-prod")
    page = _FakePage(url=PINNED_CONVERSATION_URL)
    first = await deliver_orion_chat_wake(
        thread="21207",
        turn=12,
        from_agent="maestro",
        page=page,
        storage=store,
    )
    assert first["ok"] is True, first
    calls = 0

    async def _should_not_run() -> dict:
        nonlocal calls
        calls += 1
        return {"ok": True, "delivered": True}

    ledger = OrionWakeDedupeLedger(store)
    second = await ledger.deliver(
        thread="21207",
        turn=12,
        endpoint_url=PINNED_CONVERSATION_URL,
        deliver_fn=_should_not_run,
    )
    assert second["reason"] == "duplicate_suppressed"
    assert calls == 0


@pytest.mark.asyncio
async def test_orion_chat_wake_handler_calls_production_entry(tmp_path: Path) -> None:
    from pipelines.orion_chat_wake.v1.handlers.wake import OrionChatWakeHandler

    with patch(
        "pipelines.orion_chat_wake.v1.handlers.wake.deliver_orion_chat_wake",
        new_callable=AsyncMock,
    ) as mocked:
        mocked.return_value = {"ok": True, "reason": "delivered"}
        handler = OrionChatWakeHandler()
        ctx = type("Ctx", (), {"options": {"thread": "21207", "turn": 4, "from_agent": "maestro"}})()
        out = await handler.execute(None, ctx)
        mocked.assert_awaited_once()
        assert out.json["reason"] == "delivered"


@pytest.mark.asyncio
async def test_second_conversation_runs_approval_path_again() -> None:
    page = _FakeHopPage(
        approval_dialogs=[
            ["Deny", "Allow ulg-code for this conversation", "Always"],
        ],
    )
    first = await orion_hop(page, BIRTH_FIXTURE, read_handoff=_handoff_ok)
    assert first["ok"] is True, first
    assert len(first["connector_receipts"]) == 1

    page.url = PINNED_CONVERSATION_URL
    page._new_chat_index = 1
    page._picker_open = False
    page.keyboard.inserted.clear()
    second = await orion_hop(page, BIRTH_FIXTURE, read_handoff=_handoff_ok)
    assert second["ok"] is True, second
    assert second["conversation_url"] == SECOND_URL
    assert len(second["connector_receipts"]) == 1
    assert page.approval_runs >= 2


@pytest.mark.asyncio
async def test_preflight_standing_handoff_reads_at_hop_time() -> None:
    seen: list[str] = []

    async def _read() -> dict:
        seen.append("read")
        return {"ok": True, "read_sha256": "abcd" * 16}

    out = await preflight_standing_handoff(_read)
    assert out["ok"] is True
    assert seen == ["read"]
    assert out["read_sha256"] == "abcd" * 16
