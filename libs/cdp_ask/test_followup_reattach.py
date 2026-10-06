"""Offline tests for opt-in warm-followup reattach (no Chrome, no network)."""

from __future__ import annotations

import inspect
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from cdp_ask.execution_store import LANE_HARD_LIMIT, ExecutionStore
from cdp_ask.followup import execute_followup
from cdp_ask.followup_reattach import ReattachOutcome, ensure_cse_attached
from cdp_ask.models import FollowupProjectAskRequest

pytestmark = pytest.mark.offline

CSE_A = "https://claude.ai/cowork/cse_abc123"
CSE_PRIOR_38282 = "https://claude.ai/cowork/cse_prior38282"
CSE_TARGET_38282 = "https://claude.ai/cowork/cse_target38282"


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


@pytest.fixture(autouse=True)
def _mock_bind_unless_isolated_registry(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    if request.node.get_closest_marker("no_bind_mock"):
        return
    if "isolated_registry" in request.fixturenames:
        return
    monkeypatch.setattr(
        "cdp_ask.followup_reattach.cdp_registry.bind_session_address",
        MagicMock(return_value=True),
    )


@pytest.fixture
def isolated_registry(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    from claude_bundles import cdp_registry as reg

    root = tmp_path / "cdp-registry"
    root.mkdir()
    regs = root / "registrations"
    regs.mkdir()
    monkeypatch.setattr(reg._store, "REGISTRY_DIR", root)
    monkeypatch.setattr(reg._store, "REGISTRY_LOG", root / "registry.jsonl")
    monkeypatch.setattr(reg._store, "ACTIVE_JSON", root / "active.json")
    monkeypatch.setattr(reg._store, "PORTS_LOCK", root / "ports.lock")
    monkeypatch.setattr(reg._store, "REGISTRATIONS_DIR", regs)
    monkeypatch.setattr(reg, "REGISTRY_DIR", root)
    monkeypatch.setattr(reg, "REGISTRY_LOG", root / "registry.jsonl")
    monkeypatch.setattr(reg, "ACTIVE_JSON", root / "active.json")
    monkeypatch.setattr(reg, "PORTS_LOCK", root / "ports.lock")
    monkeypatch.setattr(reg, "REGISTRATIONS_DIR", regs)
    monkeypatch.setattr(reg, "_HELD_LOCKS", {})
    monkeypatch.setattr(reg, "PORT_RANGE", range(9223, 9230))
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    monkeypatch.setattr(
        reg.cdp_lane,
        "profile_for",
        lambda suffix: profiles / f"claude-ai-chrome-profile-{suffix}",
    )
    monkeypatch.setattr(reg.cdp_lane, "is_listening", lambda _port: False)
    monkeypatch.setenv("CDP_REGISTRY_SEAT_AUTHORITY", "1")
    monkeypatch.setattr(
        "claude_bundles.x_display_capacity.require_chrome_headroom",
        lambda **_kwargs: {"x_exhausted": False},
    )
    return root


def _noop_launch(port: int, profile: Path) -> int:
    profile.mkdir(parents=True, exist_ok=True)
    return 1


def _registry_events(
    root: Path, *, event: str, registration_id: str | None = None
) -> list[dict[str, Any]]:
    path = root / "registry.jsonl"
    if not path.is_file():
        return []
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if record.get("event") != event:
            continue
        rid = str(record.get("registration_id") or "")
        if registration_id and rid != registration_id:
            continue
        out.append(record)
    return out


def _borrow_seat_mismatch_setup(
    isolated_registry: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    bind_prior: bool,
) -> tuple[Any, str, str, ExecutionStore]:
    """Shared 38282 flow: isolated registry, seat gate, mocked CDP navigation."""
    from claude_bundles import cdp_registry as reg

    store = ExecutionStore()
    lane = "15364"
    reg_holder_id = "reg-lane-holder-38282"
    reg_borrowed = reg.register_lane(
        holder="holder-borrow",
        purpose="operator-proxy",
        launch_chrome=_noop_launch,
        is_listening=lambda _p: False,
    )
    if bind_prior:
        assert reg.bind_session_address(
            reg_borrowed.registration_id, chat_url=CSE_PRIOR_38282
        )

    async def _snap() -> dict[str, Any]:
        return {
            "seat_rows": [
                {
                    "registration_id": reg_holder_id,
                    "parent_thread": lane,
                    "purpose": "operator-proxy",
                    "seat_bound_at": 1.0,
                }
            ],
            "rows": [],
            "observed_at": "t",
        }

    monkeypatch.setattr(store, "active_work_snapshot", _snap)
    monkeypatch.setattr(
        "cdp_ask.followup_resolve.scan_lane_cse_urls", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(
        "cdp_ask.followup_reattach.find_page_on_lane", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        "cdp_ask.followup_reattach.connect_cdp", _connect_factory()
    )
    return reg, reg_borrowed, reg_holder_id, store


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

    def _list_active() -> list[_FakeReg]:
        if reattach_empty and inspect.stack()[1].function == "ensure_cse_attached":
            # 4dc48cd7e6 matches chat_url on the attachment journal, so a
            # fixed list_active call index no longer falls on the mint probe.
            return []
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
        "cdp_ask.followup.find_page_on_lane", AsyncMock(return_value=(page, pw))
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
async def test_paste_retired_url_does_not_call_associate_cse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Followup paste into a chat URL does not write the bus CSE pointer.

    The hop successor is the current association. Breaks when the paste path
    starts calling associate_cse for the URL it pasted into.
    """
    store = ExecutionStore()
    reg = _reg("reg-retired")
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
        "cdp_ask.followup.find_page_on_lane", AsyncMock(return_value=(page, pw))
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
    associate = MagicMock()
    monkeypatch.setattr("agent_bus_store.db.cse_associations.associate_cse", associate)

    resp = await execute_followup(
        FollowupProjectAskRequest(
            chat_url=CSE_A,
            prompt_text="x",
            parent_thread="12286",
        ),
        store,
    )
    assert resp.ok is True
    associate.assert_not_called()


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
async def test_ensure_cse_attached_reuses_existing_tab_without_navigate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Operator-reopened CSE tab on a live host must not mint or open a new page."""
    reg = _reg("reg-1")
    monkeypatch.setattr(
        "claude_bundles.cdp_registry.list_active",
        lambda: [reg],
    )
    bind = MagicMock()
    monkeypatch.setattr(
        "cdp_ask.followup_reattach.cdp_registry.bind_session_address",
        bind,
    )
    navigate = AsyncMock()
    monkeypatch.setattr("cdp_ask.followup_reattach._navigate_new_page", navigate)

    async def _connect(_cdp_url: str) -> tuple[Any, Any, _FakeCtx, Any]:
        ctx = _FakeCtx()
        page = _FakePage()
        page.url = CSE_A
        ctx.pages.append(page)
        return _FakePw(), MagicMock(), ctx, MagicMock()

    monkeypatch.setattr("cdp_ask.followup_reattach.connect_cdp", _connect)

    outcome = await ensure_cse_attached(CSE_A, holder="h", purpose="operator-proxy")
    assert outcome.ok is True
    assert outcome.lane_created is False
    assert outcome.registration_id == "reg-1"
    navigate.assert_not_awaited()
    bind.assert_called_once_with("reg-1", chat_url=CSE_A)


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
    register.assert_called_once_with(
        holder="h", purpose=None, parent_thread=None, mission_kind=None
    )


@pytest.mark.asyncio
@pytest.mark.no_bind_mock
async def test_operator_mint_with_lane_yields_seat_open_after_bind(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """AC4: operator-purpose mint with parent_thread seats after bind_session_address."""
    from claude_bundles import cdp_registry as reg
    from claude_bundles.cdp_registry.models import seat_open

    root = tmp_path / "cdp-registry"
    root.mkdir()
    regs = root / "registrations"
    regs.mkdir()
    for target in (reg._store, reg):
        monkeypatch.setattr(target, "REGISTRY_DIR", root)
        monkeypatch.setattr(target, "REGISTRY_LOG", root / "registry.jsonl")
        monkeypatch.setattr(target, "ACTIVE_JSON", root / "active.json")
        monkeypatch.setattr(target, "PORTS_LOCK", root / "ports.lock")
        monkeypatch.setattr(target, "REGISTRATIONS_DIR", regs)
    monkeypatch.setattr(reg, "_HELD_LOCKS", {})
    monkeypatch.setattr(reg, "PORT_RANGE", range(9223, 9226))
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    monkeypatch.setattr(
        reg.cdp_lane,
        "profile_for",
        lambda suffix: profiles / f"claude-ai-chrome-profile-{suffix}",
    )
    monkeypatch.setattr(
        reg.cdp_lane,
        "_launch_chrome",
        lambda port, profile: profile.mkdir(parents=True, exist_ok=True) or 1,
    )
    monkeypatch.setattr(
        "cdp_ask.followup_reattach.connect_cdp",
        _connect_factory(),
    )

    outcome = await ensure_cse_attached(
        CSE_A,
        holder="h",
        purpose="operator-proxy",
        parent_thread="10479",
    )
    assert outcome.ok is True
    assert outcome.lane_created is True
    row = reg._load_active()[outcome.registration_id]
    assert seat_open(row, "10479")


@pytest.mark.asyncio
async def test_at_hard_limit_still_mints_lane(monkeypatch: pytest.MonkeyPatch) -> None:
    lanes = [_reg(f"reg-{i}") for i in range(LANE_HARD_LIMIT)]
    monkeypatch.setattr(
        "claude_bundles.cdp_registry.list_active",
        lambda: lanes,
    )
    monkeypatch.setattr(
        "claude_bundles.cdp_registry.count_capacity_lanes",
        lambda: LANE_HARD_LIMIT,
    )
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
    assert outcome.error != "lane_capacity_exhausted"


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
    register.assert_called_once_with(
        holder="h", purpose=None, parent_thread=None, mission_kind=None
    )


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
        "cdp_ask.followup.find_page_on_lane",
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
        "cdp_ask.followup.find_page_on_lane",
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
        "cdp_ask.followup.find_page_on_lane",
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
        "cdp_ask.followup.find_page_on_lane",
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
        "cdp_ask.followup.find_page_on_lane",
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
        "cdp_ask.followup.find_page_on_lane",
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
        "cdp_ask.followup.find_page_on_lane", AsyncMock(return_value=(page, pw))
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
        "cdp_ask.followup.find_page_on_lane", AsyncMock(return_value=(page, pw))
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
        "cdp_ask.followup.find_page_on_lane", AsyncMock(return_value=(page, pw))
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
        "cdp_ask.followup.find_page_on_lane", AsyncMock(return_value=(page, pw))
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


@pytest.mark.asyncio
async def test_parked_lane_stored_identity_with_reattach_reaches_paste(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Address-retry shape: parent_thread + chat_url + holder registration_id + reattach."""
    store = ExecutionStore()
    lane = "12286"
    reg = _reg("reg-stored-parked")
    snap = {
        "seat_rows": [
            {
                "registration_id": reg.registration_id,
                "parent_thread": lane,
                "purpose": "operator-proxy",
                "seat_bound_at": 1.0,
            }
        ],
        "rows": [],
        "observed_at": "t",
    }

    async def _snap() -> dict[str, Any]:
        return snap

    monkeypatch.setattr(store, "active_work_snapshot", _snap)
    _patch_list_active(monkeypatch, reg=reg)
    monkeypatch.setattr(
        "cdp_ask.followup_resolve.scan_lane_cse_urls",
        AsyncMock(side_effect=[[], [CSE_A]]),
    )
    ensure = AsyncMock(
        return_value=ReattachOutcome(
            ok=True,
            registration_id=reg.registration_id,
            cdp_url=reg.cdp_url,
            lane_created=False,
        )
    )
    monkeypatch.setattr("cdp_ask.followup.ensure_cse_attached", ensure)
    page = MagicMock()
    page.url = CSE_A
    pw = AsyncMock()
    pw.stop = AsyncMock()
    monkeypatch.setattr(
        "cdp_ask.followup.find_page_on_lane", AsyncMock(return_value=(page, pw))
    )
    paste = AsyncMock(
        return_value={
            "send_verified": True,
            "receipt": "dom_paste",
            "streaming_at_paste": False,
            "url": CSE_A,
            "pasted_at": 1.0,
        }
    )
    monkeypatch.setattr("cdp_ask.followup.send_followup_paste_half", paste)
    monkeypatch.setattr("cdp_ask.followup.emit_followup_event", lambda _e: None)

    resp = await execute_followup(
        FollowupProjectAskRequest(
            prompt_text="memo",
            parent_thread=lane,
            chat_url=CSE_A,
            registration_id=reg.registration_id,
            reattach=True,
        ),
        store,
    )
    ensure.assert_awaited_once()
    paste.assert_awaited_once()
    assert resp.ok is True
    assert resp.reattach_used is True


@pytest.mark.asyncio
async def test_reattach_mint_syncs_registration_id_before_second_resolve(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When reattach mints host M, second resolve sees M not the stored reg R."""
    from cdp_ask.followup_envelope import FollowupCandidate, fail_followup

    store = ExecutionStore()
    lane = "12286"
    reg_m = _reg("reg-minted-M")
    seen_regs: list[str | None] = []
    events: list[Any] = []
    target = FollowupCandidate(
        registration_id=reg_m.registration_id,
        chat_url=CSE_A,
        holder="h",
        purpose="operator-proxy",
        cdp_url=reg_m.cdp_url,
    )

    async def _resolve(
        req: FollowupProjectAskRequest,
        _store: ExecutionStore,
        *,
        lane_pin: dict | None = None,
    ):
        seen_regs.append(req.registration_id)
        if len(seen_regs) == 1:
            return (
                None,
                fail_followup("cse_not_found_on_lane", url=CSE_A),
                "chat_url",
                None,
            )
        return target, None, "chat_url", None

    monkeypatch.setattr("cdp_ask.followup.resolve_followup_target", _resolve)
    monkeypatch.setattr(
        "cdp_ask.followup.ensure_cse_attached",
        AsyncMock(
            return_value=ReattachOutcome(
                ok=True,
                registration_id=reg_m.registration_id,
                cdp_url=reg_m.cdp_url,
                lane_created=True,
            )
        ),
    )
    page = MagicMock()
    page.url = CSE_A
    pw = AsyncMock()
    pw.stop = AsyncMock()
    monkeypatch.setattr(
        "cdp_ask.followup.find_page_on_lane", AsyncMock(return_value=(page, pw))
    )
    paste = AsyncMock(
        return_value={
            "send_verified": True,
            "receipt": "dom_paste",
            "streaming_at_paste": False,
            "url": CSE_A,
            "pasted_at": 1.0,
        }
    )
    monkeypatch.setattr("cdp_ask.followup.send_followup_paste_half", paste)

    def _capture(ev: Any) -> None:
        events.append(ev)

    monkeypatch.setattr("cdp_ask.followup.emit_followup_event", _capture)

    resp = await execute_followup(
        FollowupProjectAskRequest(
            prompt_text="memo",
            parent_thread=lane,
            chat_url=CSE_A,
            registration_id="reg-stored-R",
            reattach=True,
        ),
        store,
    )
    assert seen_regs == ["reg-stored-R", reg_m.registration_id]
    assert resp.ok is True
    assert resp.lane_created is True
    assert resp.reattach_used is True
    paste.assert_awaited_once()
    assert not any(
        getattr(e, "signal", None) == "cdp_ask.followup.refused_seat_mismatch"
        for e in events
    )


@pytest.mark.asyncio
async def test_borrow_reattach_restores_prior_chat_url_on_seat_mismatch(
    isolated_registry: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Borrow bind to target URL must not stick when second resolve refuses seat."""
    reg, reg_borrowed, reg_holder_id, store = _borrow_seat_mismatch_setup(
        isolated_registry, monkeypatch, bind_prior=True
    )
    lane = "15364"
    events: list[Any] = []

    def _capture(ev: Any) -> None:
        events.append(ev)

    monkeypatch.setattr("cdp_ask.followup.emit_followup_event", _capture)
    monkeypatch.setattr("cdp_ask.followup_resolve.emit_followup_event", _capture)

    resp = await execute_followup(
        FollowupProjectAskRequest(
            prompt_text="memo",
            parent_thread=lane,
            chat_url=CSE_TARGET_38282,
            registration_id=reg_holder_id,
            reattach=True,
            purpose="operator-proxy",
        ),
        store,
    )
    assert resp.ok is False
    assert resp.error == "operator_seat_mismatch"
    assert resp.reattach_used is True
    assert any(
        getattr(e, "signal", None) == "cdp_ask.followup.refused_seat_mismatch"
        for e in events
    )
    rid = reg_borrowed.registration_id
    bound_events = _registry_events(
        isolated_registry, event="session_address_bound", registration_id=rid
    )
    restored_events = _registry_events(
        isolated_registry, event="session_address_restored", registration_id=rid
    )
    assert [e.get("chat_url") for e in bound_events] == [
        CSE_PRIOR_38282,
        CSE_TARGET_38282,
    ]
    assert restored_events
    assert restored_events[-1].get("restore_to") == CSE_PRIOR_38282
    assert reg.chat_url_for_registration(rid) == CSE_PRIOR_38282


@pytest.mark.asyncio
async def test_borrow_reattach_clears_unbound_host_on_seat_mismatch(
    isolated_registry: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unbound borrow must not leave the refused URL attached (a:38282 unbound case)."""
    reg, reg_borrowed, reg_holder_id, store = _borrow_seat_mismatch_setup(
        isolated_registry, monkeypatch, bind_prior=False
    )
    lane = "15364"
    monkeypatch.setattr("cdp_ask.followup.emit_followup_event", lambda _e: None)
    monkeypatch.setattr("cdp_ask.followup_resolve.emit_followup_event", lambda _e: None)

    resp = await execute_followup(
        FollowupProjectAskRequest(
            prompt_text="memo",
            parent_thread=lane,
            chat_url=CSE_TARGET_38282,
            registration_id=reg_holder_id,
            reattach=True,
            purpose="operator-proxy",
        ),
        store,
    )
    assert resp.error == "operator_seat_mismatch"
    rid = reg_borrowed.registration_id
    assert reg.chat_url_for_registration(rid) is None
    assert reg.attachment_for_chat_url(CSE_TARGET_38282) is None


@pytest.mark.asyncio
async def test_borrow_reattach_refusal_with_retain_lane_restores_binding(
    isolated_registry: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """retain_lane=True still restores registry chat_url after seat refusal."""
    reg, reg_borrowed, reg_holder_id, store = _borrow_seat_mismatch_setup(
        isolated_registry, monkeypatch, bind_prior=True
    )
    lane = "15364"
    monkeypatch.setattr("cdp_ask.followup.emit_followup_event", lambda _e: None)
    monkeypatch.setattr("cdp_ask.followup_resolve.emit_followup_event", lambda _e: None)

    resp = await execute_followup(
        FollowupProjectAskRequest(
            prompt_text="memo",
            parent_thread=lane,
            chat_url=CSE_TARGET_38282,
            registration_id=reg_holder_id,
            reattach=True,
            retain_lane=True,
            purpose="operator-proxy",
        ),
        store,
    )
    assert resp.error == "operator_seat_mismatch"
    assert reg.chat_url_for_registration(reg_borrowed.registration_id) == CSE_PRIOR_38282


@pytest.mark.asyncio
async def test_restore_session_address_rebound_skips_stale_prior(
    isolated_registry: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Atomic restore must not overwrite Q when the row rebound before the lock."""
    from claude_bundles import cdp_registry as reg
    from claude_bundles import cdp_registry_store as store_mod

    host = reg.register_lane(
        holder="h",
        purpose="operator-proxy",
        launch_chrome=_noop_launch,
        is_listening=lambda _p: False,
    )
    rid = host.registration_id
    assert reg.bind_session_address(rid, chat_url=CSE_TARGET_38282)
    newer = "https://claude.ai/cowork/cse_newer38282q"
    real_load = store_mod.load_active
    calls = {"n": 0}

    def _load_twice() -> dict[str, Any]:
        calls["n"] += 1
        active = real_load()
        if calls["n"] >= 2:
            row = dict(active[rid])
            row["chat_url"] = newer
            active[rid] = row
        return active

    monkeypatch.setattr(store_mod, "load_active", _load_twice)
    status = reg.restore_session_address(
        rid,
        expected_chat_url=CSE_TARGET_38282,
        restore_to=CSE_PRIOR_38282,
    )
    assert status == "rebound"
    assert reg.chat_url_for_registration(rid) == newer


@pytest.mark.asyncio
async def test_restore_session_address_clears_when_prior_held_elsewhere(
    isolated_registry: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When prior URL is attached elsewhere, refusal teardown clears the borrow."""
    import logging

    from claude_bundles import cdp_registry as reg

    caplog.set_level(logging.INFO)
    other = reg.register_lane(
        holder="other",
        purpose="operator-proxy",
        launch_chrome=_noop_launch,
        is_listening=lambda _p: False,
    )
    borrowed = reg.register_lane(
        holder="borrow",
        purpose="operator-proxy",
        launch_chrome=_noop_launch,
        is_listening=lambda _p: False,
    )
    assert reg.bind_session_address(other.registration_id, chat_url=CSE_PRIOR_38282)
    assert reg.bind_session_address(borrowed.registration_id, chat_url=CSE_TARGET_38282)

    from cdp_ask.followup_reattach import ReattachOutcome, restore_borrowed_chat_url_on_refusal

    restore_borrowed_chat_url_on_refusal(
        ReattachOutcome(
            ok=True,
            registration_id=borrowed.registration_id,
            prior_chat_url=CSE_PRIOR_38282,
            reattach_bound_chat_url=CSE_TARGET_38282,
        )
    )
    assert reg.chat_url_for_registration(borrowed.registration_id) is None
    assert reg.attachment_for_chat_url(CSE_TARGET_38282) is None
    assert any("session_address_restored" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_lane_busy_skips_restore_when_inflight_paste_holds_target(
    isolated_registry: Path,
) -> None:
    """lane_busy must not restore while another followup holds the lane on this URL."""
    from claude_bundles import cdp_registry as reg

    from cdp_ask.followup import _acquire_lane, _reattach_teardown, _release_lane
    from cdp_ask.followup_reattach import ReattachOutcome

    reg_borrowed = reg.register_lane(
        holder="holder-borrow",
        purpose="operator-proxy",
        launch_chrome=_noop_launch,
        is_listening=lambda _p: False,
    )
    assert reg.bind_session_address(
        reg_borrowed.registration_id, chat_url=CSE_TARGET_38282
    )
    assert await _acquire_lane(
        reg_borrowed.registration_id, target_chat_url=CSE_TARGET_38282
    )
    try:
        await _reattach_teardown(
            ReattachOutcome(
                ok=True,
                registration_id=reg_borrowed.registration_id,
                prior_chat_url=CSE_PRIOR_38282,
                reattach_bound_chat_url=CSE_TARGET_38282,
            ),
            retain_lane=True,
            restore_borrowed_chat_url=True,
            skip_inflight_restore=True,
        )
        assert (
            reg.chat_url_for_registration(reg_borrowed.registration_id)
            == CSE_TARGET_38282
        )
    finally:
        _release_lane(reg_borrowed.registration_id)


@pytest.mark.asyncio
async def test_second_resolve_exception_runs_restore_teardown(
    isolated_registry: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Snapshot failure after borrow must restore and disconnect Playwright."""
    reg, reg_borrowed, reg_holder_id, store = _borrow_seat_mismatch_setup(
        isolated_registry, monkeypatch, bind_prior=True
    )
    lane = "15364"
    pw = AsyncMock()
    pw.stop = AsyncMock()
    snap_calls = {"n": 0}

    async def _snap_twice() -> dict[str, Any]:
        snap_calls["n"] += 1
        if snap_calls["n"] >= 2:
            raise RuntimeError("snap unavailable")
        return {
            "seat_rows": [
                {
                    "registration_id": reg_holder_id,
                    "parent_thread": lane,
                    "purpose": "operator-proxy",
                    "seat_bound_at": 1.0,
                }
            ],
            "rows": [],
            "observed_at": "t",
        }

    monkeypatch.setattr("cdp_ask.followup.emit_followup_event", lambda _e: None)
    monkeypatch.setattr(
        "cdp_ask.followup_reattach.find_page_on_lane",
        AsyncMock(return_value=(MagicMock(), pw)),
    )
    real_resolve = __import__(
        "cdp_ask.followup_resolve", fromlist=["resolve_followup_target"]
    ).resolve_followup_target
    resolve_calls = {"n": 0}

    async def _resolve_raise_second(*args: Any, **kwargs: Any) -> Any:
        resolve_calls["n"] += 1
        if resolve_calls["n"] >= 2:
            raise RuntimeError("snap unavailable")
        return await real_resolve(*args, **kwargs)

    monkeypatch.setattr(
        "cdp_ask.followup.resolve_followup_target", _resolve_raise_second
    )

    with pytest.raises(RuntimeError, match="snap unavailable"):
        await execute_followup(
            FollowupProjectAskRequest(
                prompt_text="memo",
                parent_thread="15364",
                chat_url=CSE_TARGET_38282,
                registration_id=reg_holder_id,
                reattach=True,
                purpose="operator-proxy",
            ),
            store,
        )
    pw.stop.assert_awaited()
    assert reg.chat_url_for_registration(reg_borrowed.registration_id) == CSE_PRIOR_38282


@pytest.mark.asyncio
async def test_paste_miss_after_reattach_restores_borrowed_chat_url(
    isolated_registry: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Page gone after lock must restore a borrow bind (finding 6)."""
    reg, reg_borrowed, reg_holder_id, store = _borrow_seat_mismatch_setup(
        isolated_registry, monkeypatch, bind_prior=True
    )
    lane = "15364"

    async def _snap_ok() -> dict[str, Any]:
        return {
            "seat_rows": [
                {
                    "registration_id": reg_borrowed.registration_id,
                    "parent_thread": lane,
                    "purpose": "operator-proxy",
                    "seat_bound_at": 1.0,
                }
            ],
            "rows": [],
            "observed_at": "t",
        }

    monkeypatch.setattr(store, "active_work_snapshot", _snap_ok)
    monkeypatch.setattr(
        "cdp_ask.followup_resolve.scan_lane_cse_urls",
        AsyncMock(side_effect=[[], [CSE_TARGET_38282]]),
    )
    monkeypatch.setattr(
        "cdp_ask.followup.find_page_on_lane", AsyncMock(return_value=None)
    )
    monkeypatch.setattr("cdp_ask.followup.emit_followup_event", lambda _e: None)
    monkeypatch.setattr("cdp_ask.followup_resolve.emit_followup_event", lambda _e: None)

    resp = await execute_followup(
        FollowupProjectAskRequest(
            prompt_text="memo",
            parent_thread=lane,
            chat_url=CSE_TARGET_38282,
            registration_id=reg_borrowed.registration_id,
            reattach=True,
            purpose="operator-proxy",
        ),
        store,
    )
    assert resp.error == "cse_not_found_on_lane"
    assert resp.reattach_used is True
    assert reg.chat_url_for_registration(reg_borrowed.registration_id) == CSE_PRIOR_38282


@pytest.mark.asyncio
async def test_refusal_teardown_skips_restore_when_host_rebound_before_teardown(
    isolated_registry: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Compare-and-restore must not clobber a newer binding on the borrowed host."""
    from claude_bundles import cdp_registry as reg

    from cdp_ask.followup import _reattach_teardown
    from cdp_ask.followup_reattach import ReattachOutcome

    reg_borrowed = reg.register_lane(
        holder="holder-borrow",
        purpose="operator-proxy",
        launch_chrome=_noop_launch,
        is_listening=lambda _p: False,
    )
    assert reg.bind_session_address(
        reg_borrowed.registration_id, chat_url=CSE_PRIOR_38282
    )
    assert reg.bind_session_address(
        reg_borrowed.registration_id, chat_url=CSE_TARGET_38282
    )
    newer = "https://claude.ai/cowork/cse_newer38282"
    assert reg.bind_session_address(reg_borrowed.registration_id, chat_url=newer)

    outcome = ReattachOutcome(
        ok=True,
        registration_id=reg_borrowed.registration_id,
        cdp_url=reg_borrowed.cdp_url,
        lane_created=False,
        prior_chat_url=CSE_PRIOR_38282,
        reattach_bound_chat_url=CSE_TARGET_38282,
    )
    page = MagicMock()
    pw = AsyncMock()
    pw.stop = AsyncMock()
    outcome_with_pw = ReattachOutcome(
        ok=outcome.ok,
        registration_id=outcome.registration_id,
        cdp_url=outcome.cdp_url,
        lane_created=outcome.lane_created,
        prior_chat_url=outcome.prior_chat_url,
        reattach_bound_chat_url=outcome.reattach_bound_chat_url,
        page=page,
        pw=pw,
    )
    await _reattach_teardown(
        outcome_with_pw,
        retain_lane=False,
        restore_borrowed_chat_url=True,
    )
    assert reg.chat_url_for_registration(reg_borrowed.registration_id) == newer


@pytest.mark.asyncio
async def test_identity_omitted_second_resolve_mismatch_restores_borrow(
    isolated_registry: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Omitted identity: a second resolve that names another seat restores and does not paste."""
    from claude_bundles import cdp_registry as reg
    from claude_bundles.cdp_registry.dormant import dormant_for_chat_url

    from cdp_ask.followup_envelope import FollowupCandidate, fail_followup
    from cdp_ask.models import FollowupCandidateInfo

    borrowed = reg.register_lane(
        holder="holder-borrow",
        purpose="operator-proxy",
        launch_chrome=_noop_launch,
        is_listening=lambda _p: False,
    )
    assert reg.bind_session_address(
        borrowed.registration_id, chat_url=CSE_PRIOR_38282
    )
    parked = reg.register_lane(
        holder="holder-parked",
        purpose="operator-proxy",
        launch_chrome=_noop_launch,
        is_listening=lambda _p: False,
    )
    assert reg.bind_session_address(parked.registration_id, chat_url=CSE_TARGET_38282)
    reg._release_driver_lock(parked.registration_id)
    assert (
        reg.make_dormant(parked.registration_id, is_listening=lambda _port: False)
        is not None
    )

    paste = AsyncMock()
    monkeypatch.setattr("cdp_ask.followup.send_followup_paste_half", paste)
    monkeypatch.setattr("cdp_ask.followup.emit_followup_event", lambda _e: None)
    monkeypatch.setattr(
        "claude_bundles.cdp_registry.dormant_for_chat_url", dormant_for_chat_url
    )
    monkeypatch.setattr(
        "cdp_ask.followup_dormant.cdp_registry.dormant_for_chat_url",
        dormant_for_chat_url,
    )
    calls = {"n": 0}
    other_id = "reg-switched-tab"

    async def _resolve(*_args: Any, **_kwargs: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            info = FollowupCandidateInfo(
                registration_id=parked.registration_id,
                chat_url=CSE_TARGET_38282,
                holder="holder-parked",
            )
            return (
                None,
                fail_followup(
                    "attended_dormant",
                    candidates=[info],
                    url=CSE_TARGET_38282,
                ),
                None,
                None,
            )
        return (
            FollowupCandidate(
                registration_id=other_id,
                chat_url=CSE_TARGET_38282,
                holder="other",
                purpose="operator-proxy",
                cdp_url="http://127.0.0.1:9",
                target_binding="resolver",
            ),
            None,
            "attended_resolver",
            "resolver",
        )

    async def _ensure(chat_url: str, **_kwargs: Any) -> ReattachOutcome:
        assert reg.bind_session_address(borrowed.registration_id, chat_url=chat_url)
        return ReattachOutcome(
            ok=True,
            registration_id=borrowed.registration_id,
            cdp_url=borrowed.cdp_url,
            prior_chat_url=CSE_PRIOR_38282,
            reattach_bound_chat_url=chat_url,
        )

    monkeypatch.setattr("cdp_ask.followup.resolve_followup_target", _resolve)
    monkeypatch.setattr("cdp_ask.followup.ensure_cse_attached", _ensure)

    resp = await execute_followup(
        FollowupProjectAskRequest(prompt_text="memo"),
        ExecutionStore(),
    )
    assert calls["n"] == 2
    assert resp.ok is False
    assert resp.error == "reattach_seat_mismatch"
    assert resp.reattach_used is True
    paste.assert_not_awaited()
    assert reg.chat_url_for_registration(borrowed.registration_id) == CSE_PRIOR_38282


@pytest.mark.asyncio
async def test_unrelated_seat_holder_change_still_refuses_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Holder moved outside this followup's reattach still refuses at the gate."""
    store = ExecutionStore()
    lane = "12286"
    events: list[Any] = []

    async def _snap() -> dict[str, Any]:
        return {
            "seat_rows": [
                {
                    "registration_id": "reg-other-holder",
                    "parent_thread": lane,
                    "purpose": "operator-proxy",
                    "seat_bound_at": 1.0,
                }
            ],
            "rows": [],
            "observed_at": "t",
        }

    monkeypatch.setattr(store, "active_work_snapshot", _snap)
    monkeypatch.setattr(
        "cdp_ask.followup_resolve.emit_followup_event",
        lambda ev: events.append(ev),
    )

    resp = await execute_followup(
        FollowupProjectAskRequest(
            prompt_text="memo",
            parent_thread=lane,
            chat_url=CSE_A,
            registration_id="reg-stored-R",
            reattach=True,
        ),
        store,
    )
    assert resp.ok is False
    assert resp.error == "operator_seat_mismatch"
    assert any(
        getattr(e, "signal", None) == "cdp_ask.followup.refused_seat_mismatch"
        for e in events
    )
