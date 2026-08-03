"""Offline tests for opt-in warm-followup reattach (no Chrome, no network)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from cdp_ask.execution_store import LANE_HARD_LIMIT, ExecutionStore
from cdp_ask.followup import execute_followup
from cdp_ask.followup_reattach import ensure_cse_attached
from cdp_ask.models import FollowupProjectAskRequest

pytestmark = pytest.mark.offline

CSE_A = "https://claude.ai/cowork/cse_abc123"


@dataclass(frozen=True)
class _FakeReg:
    registration_id: str
    port: int
    profile_suffix: str
    profile: Path
    cdp_url: str
    holder: str
    purpose: str | None = None


def _reg(
    reg_id: str,
    *,
    purpose: str = "operator-proxy",
    cdp: str = "http://127.0.0.1:9223",
) -> _FakeReg:
    return _FakeReg(
        registration_id=reg_id,
        port=9223,
        profile_suffix="s",
        profile=Path("/tmp/p"),
        cdp_url=cdp,
        holder="holder-a",
        purpose=purpose,
    )


class _FakePage:
    def __init__(self, *, bad: bool = False) -> None:
        self.url = ""
        self.bad = bad
        self.closed = False

    async def goto(self, url: str, **_: Any) -> None:
        if self.bad:
            self.url = "https://claude.ai/new"
        else:
            self.url = url

    async def close(self) -> None:
        self.closed = True


class _FakeCtx:
    def __init__(self, *, bad: bool = False) -> None:
        self.bad = bad
        self.pages: list[_FakePage] = []

    async def new_page(self) -> _FakePage:
        page = _FakePage(bad=self.bad)
        self.pages.append(page)
        return page


class _FakePw:
    def __init__(self) -> None:
        self.stopped = False

    async def stop(self) -> None:
        self.stopped = True


def _connect_factory(*, fail: bool = False, bad_url: bool = False):
    async def _connect(_cdp_url: str) -> tuple[Any, Any, _FakeCtx, Any]:
        if fail:
            raise RuntimeError("connect failed")
        ctx = _FakeCtx(bad=bad_url)
        return _FakePw(), MagicMock(), ctx, MagicMock()

    return _connect


def _patch_list_active(
    monkeypatch: pytest.MonkeyPatch,
    *,
    reg: _FakeReg | None = None,
    reattach_empty: bool = False,
) -> None:
    """Patch shared registry ``list_active`` without clobbering resolve vs reattach."""
    sequence: list[list[_FakeReg]] = []
    if reg is not None:
        sequence.append([reg])
    if reattach_empty:
        sequence.append([])
        sequence.append([reg] if reg is not None else [])
    elif reg is not None:
        sequence.append([reg])

    if len(sequence) == 1:
        monkeypatch.setattr(
            "claude_bundles.cdp_registry.list_active",
            lambda: sequence[0],
        )
    else:
        monkeypatch.setattr(
            "claude_bundles.cdp_registry.list_active",
            MagicMock(side_effect=sequence),
        )


@pytest.mark.asyncio
async def test_reattach_false_leaves_cse_not_found_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ExecutionStore()
    ensure = AsyncMock()
    monkeypatch.setattr("cdp_ask.followup.ensure_cse_attached", ensure)
    monkeypatch.setattr(
        "cdp_ask.followup_resolve.cdp_registry.list_active",
        lambda: [_reg("reg-1")],
    )
    monkeypatch.setattr(
        "cdp_ask.followup_resolve.scan_lane_cse_urls",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr("cdp_ask.followup.emit_followup_event", lambda _e: None)

    resp = await execute_followup(
        FollowupProjectAskRequest(chat_url=CSE_A, prompt_text="x"),
        store,
    )
    assert resp.ok is False
    assert resp.error == "cse_not_found_on_lane"
    assert resp.reattach_used is False
    ensure.assert_not_called()


@pytest.mark.asyncio
async def test_reattach_true_without_chat_url() -> None:
    store = ExecutionStore()
    resp = await execute_followup(
        FollowupProjectAskRequest(reattach=True, prompt_text="x", registration_id="r1"),
        store,
    )
    assert resp.ok is False
    assert resp.error == "reattach_requires_chat_url"


@pytest.mark.asyncio
async def test_reuse_path_lane_created_false(monkeypatch: pytest.MonkeyPatch) -> None:
    reg = _reg("reg-1")
    monkeypatch.setattr(
        "claude_bundles.cdp_registry.list_active",
        lambda: [reg],
    )
    register = MagicMock()
    monkeypatch.setattr(
        "cdp_ask.followup_reattach.cdp_registry.register_lane", register
    )
    monkeypatch.setattr(
        "cdp_ask.followup_reattach.connect_cdp",
        _connect_factory(),
    )

    outcome = await ensure_cse_attached(CSE_A, holder="h", purpose="operator-proxy")
    assert outcome.ok is True
    assert outcome.lane_created is False
    assert outcome.registration_id == "reg-1"
    register.assert_not_called()


@pytest.mark.asyncio
async def test_launch_path_registers_lane(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("claude_bundles.cdp_registry.list_active", lambda: [])
    fake_reg = _reg("reg-new")
    register = MagicMock(return_value=fake_reg)
    monkeypatch.setattr(
        "cdp_ask.followup_reattach.cdp_registry.register_lane", register
    )
    monkeypatch.setattr(
        "cdp_ask.followup_reattach.connect_cdp",
        _connect_factory(),
    )

    outcome = await ensure_cse_attached(CSE_A, holder="h")
    assert outcome.ok is True
    assert outcome.lane_created is True
    register.assert_called_once_with(holder="h", purpose=None)


@pytest.mark.asyncio
async def test_hard_limit_skips_register(monkeypatch: pytest.MonkeyPatch) -> None:
    lanes = [_reg(f"reg-{i}") for i in range(LANE_HARD_LIMIT)]
    monkeypatch.setattr(
        "claude_bundles.cdp_registry.list_active",
        lambda: lanes,
    )
    monkeypatch.setattr(
        "claude_bundles.cdp_registry.count_capacity_lanes",
        lambda: LANE_HARD_LIMIT,
    )
    register = MagicMock()
    monkeypatch.setattr(
        "cdp_ask.followup_reattach.cdp_registry.register_lane", register
    )
    monkeypatch.setattr(
        "cdp_ask.followup_reattach.connect_cdp",
        _connect_factory(fail=True),
    )

    outcome = await ensure_cse_attached(CSE_A, holder="h")
    assert outcome.ok is False
    assert outcome.error == "lane_capacity_exhausted"
    register.assert_not_called()


@pytest.mark.asyncio
async def test_orphan_only_discovery_does_not_exhaust_capacity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lanes = [_reg(f"orphan-{i}") for i in range(LANE_HARD_LIMIT)]
    monkeypatch.setattr(
        "claude_bundles.cdp_registry.list_active",
        lambda: lanes,
    )
    monkeypatch.setattr(
        "claude_bundles.cdp_registry.count_capacity_lanes",
        lambda: 0,
    )
    fake_reg = _reg("reg-new")
    register = MagicMock(return_value=fake_reg)
    monkeypatch.setattr(
        "cdp_ask.followup_reattach.cdp_registry.register_lane", register
    )
    monkeypatch.setattr(
        "cdp_ask.followup_reattach.cdp_registry.deregister_lane", MagicMock()
    )
    monkeypatch.setattr(
        "cdp_ask.followup_reattach.connect_cdp",
        _connect_factory(fail=True),
    )

    outcome = await ensure_cse_attached(CSE_A, holder="h")
    assert outcome.error != "lane_capacity_exhausted"
    assert outcome.error == "reattach_navigate_failed"
    register.assert_called_once_with(holder="h", purpose=None)


@pytest.mark.asyncio
async def test_verify_failure_teardown_and_no_paste(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ExecutionStore()
    reg = _reg("reg-new")
    _patch_list_active(monkeypatch, reg=reg, reattach_empty=True)
    monkeypatch.setattr(
        "cdp_ask.followup_resolve.scan_lane_cse_urls",
        AsyncMock(return_value=[]),
    )
    deregister = MagicMock()
    monkeypatch.setattr(
        "cdp_ask.followup_reattach.cdp_registry.register_lane",
        MagicMock(return_value=reg),
    )
    monkeypatch.setattr(
        "cdp_ask.followup_reattach.cdp_registry.deregister_lane",
        deregister,
    )
    monkeypatch.setattr(
        "cdp_ask.followup_reattach.connect_cdp",
        _connect_factory(bad_url=True),
    )
    paste = AsyncMock()
    monkeypatch.setattr("cdp_ask.followup.send_followup_paste_half", paste)
    monkeypatch.setattr("cdp_ask.followup.emit_followup_event", lambda _e: None)

    resp = await execute_followup(
        FollowupProjectAskRequest(chat_url=CSE_A, prompt_text="x", reattach=True),
        store,
    )
    assert resp.ok is False
    assert resp.error == "reattach_navigate_failed"
    paste.assert_not_called()
    deregister.assert_called_once_with("reg-new")


@pytest.mark.asyncio
async def test_created_lane_deregistered_when_not_retained(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ExecutionStore()
    reg = _reg("reg-new")
    _patch_list_active(monkeypatch, reg=reg, reattach_empty=True)
    monkeypatch.setattr(
        "cdp_ask.followup_resolve.scan_lane_cse_urls",
        AsyncMock(side_effect=[[], [CSE_A]]),
    )
    monkeypatch.setattr(
        "cdp_ask.followup_reattach.cdp_registry.register_lane",
        MagicMock(return_value=reg),
    )
    monkeypatch.setattr(
        "cdp_ask.followup_reattach.connect_cdp",
        _connect_factory(),
    )
    deregister = MagicMock()
    monkeypatch.setattr("cdp_ask.followup.cdp_registry.deregister_lane", deregister)
    page = MagicMock()
    page.url = CSE_A
    pw = AsyncMock()
    pw.stop = AsyncMock()
    monkeypatch.setattr(
        "cdp_ask.followup._find_page_on_lane",
        AsyncMock(return_value=(page, pw)),
    )
    monkeypatch.setattr(
        "cdp_ask.followup.send_followup_paste_half",
        AsyncMock(
            return_value={
                "send_verified": True,
                "streaming_at_paste": False,
                "url": CSE_A,
                "pasted_at": 1.0,
            }
        ),
    )
    monkeypatch.setattr("cdp_ask.followup.emit_followup_event", lambda _e: None)

    resp = await execute_followup(
        FollowupProjectAskRequest(chat_url=CSE_A, prompt_text="x", reattach=True),
        store,
    )
    assert resp.ok is True
    assert resp.lane_created is True
    assert resp.reattach_used is True
    deregister.assert_called_once_with("reg-new")


@pytest.mark.asyncio
async def test_created_lane_retained_when_retain_lane_true(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ExecutionStore()
    reg = _reg("reg-new")
    _patch_list_active(monkeypatch, reg=reg, reattach_empty=True)
    monkeypatch.setattr(
        "cdp_ask.followup_resolve.scan_lane_cse_urls",
        AsyncMock(side_effect=[[], [CSE_A]]),
    )
    monkeypatch.setattr(
        "cdp_ask.followup_reattach.cdp_registry.register_lane",
        MagicMock(return_value=reg),
    )
    monkeypatch.setattr(
        "cdp_ask.followup_reattach.connect_cdp",
        _connect_factory(),
    )
    deregister = MagicMock()
    monkeypatch.setattr("cdp_ask.followup.cdp_registry.deregister_lane", deregister)
    page = MagicMock()
    page.url = CSE_A
    pw = AsyncMock()
    pw.stop = AsyncMock()
    monkeypatch.setattr(
        "cdp_ask.followup._find_page_on_lane",
        AsyncMock(return_value=(page, pw)),
    )
    monkeypatch.setattr(
        "cdp_ask.followup.send_followup_paste_half",
        AsyncMock(
            return_value={
                "send_verified": True,
                "streaming_at_paste": False,
                "url": CSE_A,
                "pasted_at": 1.0,
            }
        ),
    )
    monkeypatch.setattr("cdp_ask.followup.emit_followup_event", lambda _e: None)

    resp = await execute_followup(
        FollowupProjectAskRequest(
            chat_url=CSE_A,
            prompt_text="x",
            reattach=True,
            retain_lane=True,
        ),
        store,
    )
    assert resp.ok is True
    deregister.assert_not_called()


@pytest.mark.asyncio
async def test_reused_lane_never_deregistered(monkeypatch: pytest.MonkeyPatch) -> None:
    store = ExecutionStore()
    reg = _reg("reg-1")
    monkeypatch.setattr(
        "claude_bundles.cdp_registry.list_active",
        MagicMock(side_effect=[[reg], [reg], [reg]]),
    )
    monkeypatch.setattr(
        "cdp_ask.followup_resolve.scan_lane_cse_urls",
        AsyncMock(side_effect=[[], [CSE_A]]),
    )
    monkeypatch.setattr(
        "cdp_ask.followup_reattach.connect_cdp",
        _connect_factory(),
    )
    deregister = MagicMock()
    monkeypatch.setattr("cdp_ask.followup.cdp_registry.deregister_lane", deregister)
    page = MagicMock()
    page.url = CSE_A
    pw = AsyncMock()
    pw.stop = AsyncMock()
    monkeypatch.setattr(
        "cdp_ask.followup._find_page_on_lane",
        AsyncMock(return_value=(page, pw)),
    )
    monkeypatch.setattr(
        "cdp_ask.followup.send_followup_paste_half",
        AsyncMock(
            return_value={
                "send_verified": True,
                "streaming_at_paste": False,
                "url": CSE_A,
                "pasted_at": 1.0,
            }
        ),
    )
    monkeypatch.setattr("cdp_ask.followup.emit_followup_event", lambda _e: None)

    resp = await execute_followup(
        FollowupProjectAskRequest(chat_url=CSE_A, prompt_text="x", reattach=True),
        store,
    )
    assert resp.ok is True
    assert resp.lane_created is False
    deregister.assert_not_called()
