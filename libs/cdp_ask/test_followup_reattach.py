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
    if reg is not None:
        monkeypatch.setattr(
            "cdp_ask.followup_resolve.cdp_registry.chat_url_for_registration",
            lambda rid, _reg=reg: CSE_A if rid == _reg.registration_id else None,
        )
        monkeypatch.setattr(
            "cdp_ask.followup_dormant.cdp_registry.chat_url_for_registration",
            lambda rid, _reg=reg: CSE_A if rid == _reg.registration_id else None,
        )
        monkeypatch.setattr(
            "claude_bundles.cdp_registry.chat_url_for_registration",
            lambda rid, _reg=reg: CSE_A if rid == _reg.registration_id else None,
        )
        monkeypatch.setattr(
            "cdp_ask.followup_reattach.cdp_registry.chat_url_for_registration",
            lambda rid, _reg=reg: CSE_A if rid == _reg.registration_id else None,
        )

    if reg is None:
        monkeypatch.setattr("claude_bundles.cdp_registry.list_active", lambda: [])
        monkeypatch.setattr(
            "cdp_ask.followup_dormant.cdp_registry.list_active", lambda: []
        )
        return

    calls = {"n": 0}

    def _list_active() -> list[_FakeReg]:
        calls["n"] += 1
        if reattach_empty:
            # Resolve may call list_active multiple times before reattach runs.
            if calls["n"] == 4:
                return []
            return [reg]
        return [reg]

    monkeypatch.setattr("claude_bundles.cdp_registry.list_active", _list_active)
    monkeypatch.setattr(
        "cdp_ask.followup_reattach.cdp_registry.list_active", _list_active
    )
    monkeypatch.setattr(
        "cdp_ask.followup_resolve.cdp_registry.list_active", _list_active
    )
    monkeypatch.setattr(
        "cdp_ask.followup_dormant.cdp_registry.list_active", _list_active
    )


@pytest.mark.asyncio
async def test_unbound_chat_url_miss_echoes_url_and_skipped_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Named URL with no bound/dormant seat is an honest miss, not session-gone."""
    store = ExecutionStore()
    ensure = AsyncMock()
    monkeypatch.setattr("cdp_ask.followup.ensure_cse_attached", ensure)
    other = _reg("reg-other")
    monkeypatch.setattr(
        "cdp_ask.followup_resolve.cdp_registry.list_active", lambda: [other]
    )
    monkeypatch.setattr(
        "cdp_ask.followup_dormant.cdp_registry.list_active", lambda: [other]
    )
    monkeypatch.setattr(
        "cdp_ask.followup_resolve.cdp_registry.chat_url_for_registration",
        lambda _rid: "https://claude.ai/cowork/cse_other",
    )
    monkeypatch.setattr(
        "cdp_ask.followup_dormant.cdp_registry.chat_url_for_registration",
        lambda _rid: "https://claude.ai/cowork/cse_other",
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
    assert resp.url == CSE_A
    assert resp.reattach_skipped_reason == "no_bound_or_dormant_seat"
    assert resp.reattach_used is False
    ensure.assert_not_called()


@pytest.mark.asyncio
async def test_bound_seat_auto_resumes_without_reattach_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unique active bind + tab not in scan resumes that host; no mint."""
    store = ExecutionStore()
    reg = _reg("reg-1")
    _patch_list_active(monkeypatch, reg=reg)
    monkeypatch.setattr(
        "cdp_ask.followup_resolve.scan_lane_cse_urls",
        AsyncMock(side_effect=[[], [CSE_A]]),
    )
    monkeypatch.setattr("cdp_ask.followup_reattach.connect_cdp", _connect_factory())
    monkeypatch.setattr(
        "cdp_ask.followup_reattach.cdp_registry.bind_session_address",
        MagicMock(),
    )
    page = MagicMock()
    page.url = CSE_A
    pw = AsyncMock()
    pw.stop = AsyncMock()
    monkeypatch.setattr(
        "cdp_ask.followup._find_page_on_lane", AsyncMock(return_value=(page, pw))
    )
    monkeypatch.setattr(
        "cdp_ask.followup.send_followup_paste_half",
        AsyncMock(
            return_value={
                "send_verified": True,
                "receipt": "dom_paste",
                "streaming_at_paste": False,
                "url": CSE_A,
                "pasted_at": 1.0,
            }
        ),
    )
    monkeypatch.setattr("cdp_ask.followup.emit_followup_event", lambda _e: None)

    resp = await execute_followup(
        FollowupProjectAskRequest(chat_url=CSE_A, prompt_text="x"),
        store,
    )
    assert resp.ok is True
    assert resp.reattach_used is True
    assert resp.lane_created is False


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
    register.assert_not_called()


@pytest.mark.asyncio
async def test_restricted_resume_does_not_navigate_other_cse_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Auto-resume must not goto a host that already holds a different CSE."""
    bound = _reg("reg-bound")
    other = _reg("reg-other", cdp="http://127.0.0.1:9224")
    monkeypatch.setattr(
        "cdp_ask.followup_reattach.cdp_registry.list_active",
        lambda: [other, bound],
    )
    navigated: list[str] = []

    async def _nav(lane: _FakeReg, _chat_url: str) -> tuple[Any, Any]:
        navigated.append(lane.registration_id)
        page = _FakePage()
        page.url = CSE_A
        return page, _FakePw()

    monkeypatch.setattr("cdp_ask.followup_reattach._navigate_new_page", _nav)
    monkeypatch.setattr(
        "cdp_ask.followup_reattach.cdp_registry.bind_session_address",
        MagicMock(),
    )

    outcome = await ensure_cse_attached(
        CSE_A,
        holder="h",
        allow_mint=False,
        restrict_to_registration_id="reg-bound",
    )
    assert outcome.ok is True
    assert navigated == ["reg-bound"]
    assert outcome.registration_id == "reg-bound"


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
                "receipt": "dom_paste",
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
                "receipt": "dom_paste",
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
    _patch_list_active(monkeypatch, reg=reg)
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
                "receipt": "dom_paste",
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


@pytest.mark.asyncio
async def test_unbound_binding_caps_human_visible_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC5: unbound + send_verified cannot satisfy human_visible gate."""
    from cdp_ask.followup_receipts import paste_response

    req = FollowupProjectAskRequest(prompt_text="x", min_receipt="human_visible")
    resp = paste_response(
        req=req,
        target_registration_id="reg-1",
        url=CSE_A,
        pasted_at=1.0,
        streaming=False,
        receipt="dom_paste",
        lane_created=False,
        reattach_used=False,
        target_binding="unbound",
    )
    assert resp.target_binding == "unbound"
    assert resp.send_verified is True
    assert resp.ok is False
    assert resp.error == "send_unverified"


@pytest.mark.asyncio
async def test_human_visible_fails_before_lane_mint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ExecutionStore()
    register = MagicMock()
    monkeypatch.setattr(
        "cdp_ask.followup_reattach.cdp_registry.register_lane", register
    )
    monkeypatch.setattr("cdp_ask.followup.ensure_cse_attached", AsyncMock())
    monkeypatch.setattr("cdp_ask.followup.emit_followup_event", lambda _e: None)

    resp = await execute_followup(
        FollowupProjectAskRequest(
            chat_url=CSE_A,
            prompt_text="x",
            min_receipt="human_visible",
        ),
        store,
    )
    assert resp.ok is False
    assert resp.error == "human_visible_receipt_unavailable"
    assert resp.receipt is None
    register.assert_not_called()


@pytest.mark.asyncio
async def test_wake_not_emitted_when_lane_created(
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
    monkeypatch.setattr("cdp_ask.followup.cdp_registry.deregister_lane", MagicMock())
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
                "receipt": "dom_committed",
                "streaming_at_paste": False,
                "url": CSE_A,
                "pasted_at": 1.0,
            }
        ),
    )
    wake = MagicMock()
    monkeypatch.setattr(
        "claude_bundles.cse_session_obligations.emit_wake_delivered_transition",
        wake,
    )
    monkeypatch.setattr(
        "claude_bundles.cse_session_obligations.resolve_wake_obligation_for_receipt",
        MagicMock(return_value=("thread-1", "obl-1")),
    )
    monkeypatch.setattr("cdp_ask.followup.emit_followup_event", lambda _e: None)

    resp = await execute_followup(
        FollowupProjectAskRequest(
            chat_url=CSE_A,
            prompt_text="x",
            reattach=True,
            min_receipt="dom_committed",
        ),
        store,
    )
    assert resp.ok is True
    assert resp.lane_created is True
    assert resp.receipt == "dom_committed"
    wake.assert_not_called()


@pytest.mark.asyncio
async def test_retain_lane_keeps_page_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ExecutionStore()
    reg = _reg("reg-new")
    _patch_list_active(monkeypatch, reg=reg, reattach_empty=True)
    monkeypatch.setattr(
        "cdp_ask.followup_resolve.scan_lane_cse_urls",
        AsyncMock(side_effect=[[], [CSE_A]]),
    )
    fake_page = _FakePage()
    fake_page.url = CSE_A
    fake_pw = _FakePw()

    async def _navigate(_lane: Any, _url: str) -> tuple[Any, Any]:
        return fake_page, fake_pw

    monkeypatch.setattr(
        "cdp_ask.followup_reattach.cdp_registry.register_lane",
        MagicMock(return_value=reg),
    )
    monkeypatch.setattr(
        "cdp_ask.followup_reattach._navigate_new_page",
        _navigate,
    )
    deregister = MagicMock()
    monkeypatch.setattr("cdp_ask.followup.cdp_registry.deregister_lane", deregister)
    monkeypatch.setattr(
        "cdp_ask.followup._find_page_on_lane",
        AsyncMock(return_value=(MagicMock(url=CSE_A), AsyncMock())),
    )
    monkeypatch.setattr(
        "cdp_ask.followup.send_followup_paste_half",
        AsyncMock(
            return_value={
                "send_verified": True,
                "receipt": "dom_paste",
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
    assert fake_page.closed is False
    assert fake_pw.stopped is True


@pytest.mark.asyncio
async def test_dom_committed_gate_fails_when_only_dom_paste(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ExecutionStore()
    reg = _reg("reg-1")
    monkeypatch.setattr(
        "claude_bundles.cdp_registry.list_active",
        lambda: [reg],
    )
    monkeypatch.setattr(
        "cdp_ask.followup_resolve.scan_lane_cse_urls",
        AsyncMock(return_value=[CSE_A]),
    )
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
                "receipt": "dom_paste",
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
            min_receipt="dom_committed",
        ),
        store,
    )
    assert resp.ok is False
    assert resp.receipt == "dom_paste"
    assert resp.send_verified is True
    assert resp.error == "send_unverified"


@pytest.fixture(autouse=True)
def _no_dormant_seats(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default every test to an empty dormant set; wake tests opt back in.

    Without this the reattach path would consult the operator's real registry and
    could relaunch a live seat during an offline test.
    """
    monkeypatch.setattr(
        "claude_bundles.cdp_registry.dormant_for_chat_url", lambda _url: None
    )
    monkeypatch.setattr(
        "cdp_ask.followup_dormant.cdp_registry.dormant_for_chat_url",
        lambda _url: None,
    )


@dataclass(frozen=True)
class _FakeSeat:
    registration_id: str
    chat_url: str
    profile_suffix: str = "s"
    purpose: str = "operator-proxy"
    dormant_at: float = 10.0


def _patch_dormant(
    monkeypatch: pytest.MonkeyPatch,
    *,
    seat: _FakeSeat | None,
    relaunch: Any = None,
) -> None:
    for target in (
        "claude_bundles.cdp_registry.dormant_for_chat_url",
        "cdp_ask.followup_reattach.cdp_registry.dormant_for_chat_url",
        "cdp_ask.followup_dormant.cdp_registry.dormant_for_chat_url",
    ):
        monkeypatch.setattr(target, lambda _url, _s=seat: _s)
    if relaunch is not None:
        monkeypatch.setattr(
            "cdp_ask.followup_reattach.cdp_registry.relaunch_dormant", relaunch
        )


@pytest.mark.asyncio
async def test_dormant_seat_is_woken_before_borrowing_another_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The seat that owns the session resumes it; other glass is not borrowed."""
    other = _reg("reg-live", cdp="http://127.0.0.1:9224")
    monkeypatch.setattr("claude_bundles.cdp_registry.list_active", lambda: [other])
    woken = _reg("reg-parked")
    relaunch = MagicMock(return_value=woken)
    _patch_dormant(monkeypatch, seat=_FakeSeat("reg-parked", CSE_A), relaunch=relaunch)
    register = MagicMock()
    monkeypatch.setattr(
        "cdp_ask.followup_reattach.cdp_registry.register_lane", register
    )
    monkeypatch.setattr("cdp_ask.followup_reattach.connect_cdp", _connect_factory())

    outcome = await ensure_cse_attached(CSE_A, holder="h", purpose="operator-proxy")

    assert outcome.ok is True
    assert outcome.relaunched is True
    assert outcome.lane_created is False
    assert outcome.registration_id == "reg-parked"
    relaunch.assert_called_once_with("reg-parked", holder="h")
    register.assert_not_called()


@pytest.mark.asyncio
async def test_dormant_relaunch_failure_is_reported_not_swallowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("claude_bundles.cdp_registry.list_active", lambda: [])
    _patch_dormant(
        monkeypatch,
        seat=_FakeSeat("reg-parked", CSE_A),
        relaunch=MagicMock(side_effect=RuntimeError("no port")),
    )

    outcome = await ensure_cse_attached(CSE_A, holder="h")

    assert outcome.ok is False
    assert outcome.error == "dormant_relaunch_failed"


@pytest.mark.asyncio
async def test_failed_navigation_after_wake_parks_the_seat_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A wake that cannot reach the CSE must not leave a live host behind."""
    monkeypatch.setattr("claude_bundles.cdp_registry.list_active", lambda: [])
    _patch_dormant(
        monkeypatch,
        seat=_FakeSeat("reg-parked", CSE_A),
        relaunch=MagicMock(return_value=_reg("reg-parked")),
    )
    parked = MagicMock()
    monkeypatch.setattr("cdp_ask.followup_reattach.cdp_registry.make_dormant", parked)
    monkeypatch.setattr(
        "cdp_ask.followup_reattach.connect_cdp", _connect_factory(bad_url=True)
    )

    outcome = await ensure_cse_attached(CSE_A, holder="h")

    assert outcome.ok is False
    assert outcome.error == "reattach_navigate_failed"
    assert parked.call_args.args[0] == "reg-parked"


@pytest.mark.asyncio
async def test_park_relaunched_host_closes_tab_and_reparks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cdp_ask.followup_dormant import park_relaunched_host
    from cdp_ask.followup_reattach import ReattachOutcome

    page = _FakePage()
    pw = _FakePw()
    parked = MagicMock()
    monkeypatch.setattr("cdp_ask.followup_dormant.cdp_registry.make_dormant", parked)

    await park_relaunched_host(
        ReattachOutcome(
            ok=True,
            registration_id="reg-parked",
            relaunched=True,
            page=page,
            pw=pw,
        )
    )

    assert page.closed is True
    assert pw.stopped is True
    parked.assert_called_once_with("reg-parked", reason="followup_complete")


def test_reattach_runs_for_a_dormant_seat_without_the_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Waking a seat the fleet parked itself is not the caller's opt-in to make."""
    from cdp_ask.followup_dormant import reattach_reason

    monkeypatch.setattr("cdp_ask.followup_dormant.cdp_registry.list_active", lambda: [])
    req = FollowupProjectAskRequest(chat_url=CSE_A, prompt_text="x")
    assert reattach_reason(req, CSE_A) is None

    _patch_dormant(monkeypatch, seat=_FakeSeat("reg-parked", CSE_A))
    assert reattach_reason(req, CSE_A) == "dormant_seat"
    assert (
        reattach_reason(
            FollowupProjectAskRequest(chat_url=CSE_A, prompt_text="x", reattach=True),
            CSE_A,
        )
        == "requested"
    )


def test_reattach_reason_bound_seat_without_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cdp_ask.followup_dormant import reattach_reason

    reg = _reg("reg-1")
    monkeypatch.setattr(
        "cdp_ask.followup_dormant.cdp_registry.list_active", lambda: [reg]
    )
    monkeypatch.setattr(
        "cdp_ask.followup_dormant.cdp_registry.chat_url_for_registration",
        lambda rid: CSE_A if rid == "reg-1" else None,
    )
    req = FollowupProjectAskRequest(chat_url=CSE_A, prompt_text="x")
    assert reattach_reason(req, CSE_A) == "bound_seat"


@pytest.mark.asyncio
async def test_woken_seat_discharges_wake_debt_and_is_parked_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A wake is a resume, not a mint: obligations discharge and the seat re-parks.

    ``lane_created`` stays false for a woken seat precisely so the wake-discharge
    gate still fires — a minted lane cannot prove delivery to the operator's page,
    but the seat that owns the session can.
    """
    store = ExecutionStore()
    reg = _reg("reg-parked")
    _patch_list_active(monkeypatch, reg=reg, reattach_empty=True)
    _patch_dormant(
        monkeypatch,
        seat=_FakeSeat("reg-parked", CSE_A),
        relaunch=MagicMock(return_value=reg),
    )
    monkeypatch.setattr(
        "cdp_ask.followup_resolve.scan_lane_cse_urls",
        AsyncMock(side_effect=[[], [CSE_A]]),
    )
    monkeypatch.setattr("cdp_ask.followup_reattach.connect_cdp", _connect_factory())
    parked = MagicMock()
    monkeypatch.setattr("cdp_ask.followup_dormant.cdp_registry.make_dormant", parked)
    deregister = MagicMock()
    monkeypatch.setattr("cdp_ask.followup.cdp_registry.deregister_lane", deregister)
    page = MagicMock()
    page.url = CSE_A
    pw = AsyncMock()
    pw.stop = AsyncMock()
    monkeypatch.setattr(
        "cdp_ask.followup._find_page_on_lane", AsyncMock(return_value=(page, pw))
    )
    monkeypatch.setattr(
        "cdp_ask.followup.send_followup_paste_half",
        AsyncMock(
            return_value={
                "send_verified": True,
                "receipt": "dom_committed",
                "streaming_at_paste": False,
                "url": CSE_A,
                "pasted_at": 1.0,
            }
        ),
    )
    monkeypatch.setattr("cdp_ask.followup.emit_followup_event", lambda _e: None)
    discharged: list[str] = []
    monkeypatch.setattr(
        "claude_bundles.cse_session_obligations.resolve_wake_obligation_for_receipt",
        lambda rid: ("6885", "obl-1"),
    )
    monkeypatch.setattr(
        "claude_bundles.cse_session_obligations.emit_wake_delivered_transition",
        lambda **kw: discharged.append(kw["registration_id"]),
    )

    resp = await execute_followup(
        FollowupProjectAskRequest(chat_url=CSE_A, prompt_text="x"),
        store,
    )

    assert resp.ok is True
    assert resp.reattach_used is True
    assert resp.lane_created is False
    assert discharged == ["reg-parked"]
    parked.assert_called_once_with("reg-parked", reason="followup_complete")
    deregister.assert_not_called()


@pytest.mark.asyncio
async def test_woken_seat_retain_lane_does_not_park(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Wait-report: a dormant wake may resume the seat; teardown must not re-park."""
    store = ExecutionStore()
    reg = _reg("reg-parked")
    _patch_list_active(monkeypatch, reg=reg, reattach_empty=True)
    _patch_dormant(
        monkeypatch,
        seat=_FakeSeat("reg-parked", CSE_A),
        relaunch=MagicMock(return_value=reg),
    )
    monkeypatch.setattr(
        "cdp_ask.followup_resolve.scan_lane_cse_urls",
        AsyncMock(side_effect=[[], [CSE_A]]),
    )
    monkeypatch.setattr("cdp_ask.followup_reattach.connect_cdp", _connect_factory())
    parked = MagicMock()
    monkeypatch.setattr("cdp_ask.followup_dormant.cdp_registry.make_dormant", parked)
    deregister = MagicMock()
    monkeypatch.setattr("cdp_ask.followup.cdp_registry.deregister_lane", deregister)
    page = MagicMock()
    page.url = CSE_A
    pw = AsyncMock()
    pw.stop = AsyncMock()
    monkeypatch.setattr(
        "cdp_ask.followup._find_page_on_lane", AsyncMock(return_value=(page, pw))
    )
    monkeypatch.setattr(
        "cdp_ask.followup.send_followup_paste_half",
        AsyncMock(
            return_value={
                "send_verified": True,
                "receipt": "dom_committed",
                "streaming_at_paste": False,
                "url": CSE_A,
                "pasted_at": 1.0,
            }
        ),
    )
    monkeypatch.setattr("cdp_ask.followup.emit_followup_event", lambda _e: None)
    discharged: list[str] = []
    monkeypatch.setattr(
        "claude_bundles.cse_session_obligations.resolve_wake_obligation_for_receipt",
        lambda rid: ("6885", "obl-1"),
    )
    monkeypatch.setattr(
        "claude_bundles.cse_session_obligations.emit_wake_delivered_transition",
        lambda **kw: discharged.append(kw["registration_id"]),
    )

    resp = await execute_followup(
        FollowupProjectAskRequest(
            chat_url=CSE_A,
            prompt_text="x",
            retain_lane=True,
        ),
        store,
    )

    assert resp.ok is True
    assert resp.reattach_used is True
    assert resp.lane_created is False
    parked.assert_not_called()
    deregister.assert_not_called()
    assert discharged == []


@pytest.mark.asyncio
async def test_attached_followup_does_not_park(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Already-on-lane paste disconnects Playwright only — no dormant park."""
    store = ExecutionStore()
    reg = _reg("reg-live")
    _patch_list_active(monkeypatch, reg=reg)
    monkeypatch.setattr(
        "cdp_ask.followup_resolve.scan_lane_cse_urls",
        AsyncMock(return_value=[CSE_A]),
    )
    parked = MagicMock()
    monkeypatch.setattr("cdp_ask.followup_dormant.cdp_registry.make_dormant", parked)
    ensure = AsyncMock()
    monkeypatch.setattr("cdp_ask.followup.ensure_cse_attached", ensure)
    page = MagicMock()
    page.url = CSE_A
    pw = AsyncMock()
    pw.stop = AsyncMock()
    monkeypatch.setattr(
        "cdp_ask.followup._find_page_on_lane", AsyncMock(return_value=(page, pw))
    )
    monkeypatch.setattr(
        "cdp_ask.followup.send_followup_paste_half",
        AsyncMock(
            return_value={
                "send_verified": True,
                "receipt": "dom_committed",
                "streaming_at_paste": False,
                "url": CSE_A,
                "pasted_at": 1.0,
            }
        ),
    )
    monkeypatch.setattr("cdp_ask.followup.emit_followup_event", lambda _e: None)
    monkeypatch.setattr(
        "claude_bundles.cse_session_obligations.emit_wake_delivered_transition",
        MagicMock(),
    )
    monkeypatch.setattr(
        "claude_bundles.cse_session_obligations.resolve_wake_obligation_for_receipt",
        lambda rid: ("t", "o"),
    )

    resp = await execute_followup(
        FollowupProjectAskRequest(
            chat_url=CSE_A,
            prompt_text="x",
            reattach=False,
            retain_lane=True,
        ),
        store,
    )

    assert resp.ok is True
    assert resp.reattach_used is False
    ensure.assert_not_called()
    parked.assert_not_called()
    pw.stop.assert_awaited()


@pytest.mark.asyncio
async def test_identity_omitted_dormant_attendance_wakes_the_seat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An omitted identity plus a dormant attended seat resolves by waking it."""
    from cdp_ask.attended_operator import (
        AttendedResolveDormant,
        AttendedResolveSuccess,
        LivenessProbe,
    )

    store = ExecutionStore()
    reg = _reg("reg-parked")
    dormant = AttendedResolveDormant(
        registration_id="reg-parked",
        chat_url=CSE_A,
        purpose="operator-proxy",
        source="cse-session-registry",
        shadow_urls=[],
    )
    live = AttendedResolveSuccess(
        registration_id="reg-parked",
        cdp_url=reg.cdp_url,
        chat_url=CSE_A,
        purpose="operator-proxy",
        probe=LivenessProbe(live=True, checked_at=1.0),
        source="cse-session-registry",
        shadow_urls=[],
    )
    outcomes = iter([dormant, live])
    monkeypatch.setattr(
        "cdp_ask.followup_attended.resolve_attended_operator", lambda: next(outcomes)
    )
    monkeypatch.setattr("claude_bundles.cdp_registry.list_active", lambda: [reg])
    monkeypatch.setattr(
        "cdp_ask.followup_resolve.cdp_registry.list_active", lambda: [reg]
    )
    relaunch = MagicMock(return_value=reg)
    _patch_dormant(monkeypatch, seat=_FakeSeat("reg-parked", CSE_A), relaunch=relaunch)
    monkeypatch.setattr("cdp_ask.followup_reattach.connect_cdp", _connect_factory())
    monkeypatch.setattr(
        "cdp_ask.followup_dormant.cdp_registry.make_dormant", MagicMock()
    )
    page = MagicMock()
    page.url = CSE_A
    pw = AsyncMock()
    pw.stop = AsyncMock()
    monkeypatch.setattr(
        "cdp_ask.followup._find_page_on_lane", AsyncMock(return_value=(page, pw))
    )
    monkeypatch.setattr(
        "cdp_ask.followup.send_followup_paste_half",
        AsyncMock(
            return_value={
                "send_verified": True,
                "receipt": "dom_paste",
                "streaming_at_paste": False,
                "url": CSE_A,
                "pasted_at": 1.0,
            }
        ),
    )
    monkeypatch.setattr("cdp_ask.followup.emit_followup_event", lambda _e: None)

    resp = await execute_followup(
        FollowupProjectAskRequest(prompt_text="x"),
        store,
    )

    assert resp.ok is True
    assert resp.reattach_used is True
    relaunch.assert_called_once()
