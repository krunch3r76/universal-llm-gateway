"""Resolver unit tests for warm CSE followup (no live Chrome)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from cdp_ask.execution_store import ExecutionStore
from cdp_ask.followup import execute_followup
from cdp_ask.followup_resolve import discover_candidates, resolve_followup_target
from cdp_ask.models import FollowupProjectAskRequest

pytestmark = pytest.mark.offline

CSE_A = "https://claude.ai/cowork/cse_abc123"
CSE_B = "https://claude.ai/cowork/cse_def456"


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
    reg_id: str, *, purpose: str = "operator-proxy", cdp: str = "http://127.0.0.1:9223"
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


@pytest.mark.asyncio
async def test_identity_omitted_resolver_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cdp_ask.attended_operator import AttendedResolveSuccess, LivenessProbe

    store = ExecutionStore()
    outcome = AttendedResolveSuccess(
        registration_id="reg-live",
        cdp_url="http://127.0.0.1:9223",
        chat_url=CSE_A,
        purpose="operator-proxy",
        probe=LivenessProbe(live=True, checked_at=1.0),
        source="cse-session-registry",
        shadow_urls=[],
    )
    monkeypatch.setattr(
        "cdp_ask.followup_resolve.resolve_attended_operator",
        lambda: outcome,
    )
    monkeypatch.setattr(
        "cdp_ask.followup_resolve.cdp_registry.list_active",
        lambda: [_reg("reg-live")],
    )
    monkeypatch.setattr("cdp_ask.followup_resolve.emit_followup_event", MagicMock())
    req = FollowupProjectAskRequest(prompt_text="hi")
    target, err, path, binding = await resolve_followup_target(req, store)
    assert err is None
    assert target is not None
    assert target.registration_id == "reg-live"
    assert path == "attended_resolver"
    assert binding == "resolver"


@pytest.mark.asyncio
async def test_identity_omitted_ambiguous_attended_no_paste(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cdp_ask.attended_operator import AttendedResolveRefused

    store = ExecutionStore()
    monkeypatch.setattr(
        "cdp_ask.followup_resolve.resolve_attended_operator",
        lambda: AttendedResolveRefused(
            code="ambiguous_attended",
            candidates=[
                {
                    "registration_id": "a",
                    "cdp_url": "http://127.0.0.1:9223",
                    "chat_url": CSE_A,
                    "purpose": "mission",
                }
            ],
            shadow_urls=[],
        ),
    )
    monkeypatch.setattr("cdp_ask.followup_resolve.emit_followup_event", MagicMock())
    paste = MagicMock()
    monkeypatch.setattr("cdp_ask.followup.send_followup_paste_half", paste)
    req = FollowupProjectAskRequest(prompt_text="hi")
    _target, err, _path, _binding = await resolve_followup_target(req, store)
    assert err is not None
    assert err.error == "ambiguous_attended"
    resp = await execute_followup(req, store)
    assert resp.ok is False
    assert resp.error == "ambiguous_attended"
    paste.assert_not_called()


@pytest.mark.asyncio
async def test_stale_registration_id_two_ports_same_chat_url_not_waived(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ExecutionStore()
    monkeypatch.setattr(
        "cdp_ask.followup_resolve.cdp_registry.list_active",
        lambda: [_reg("reg-1", cdp="http://127.0.0.1:9223"), _reg("reg-2", cdp="http://127.0.0.1:9224")],
    )
    monkeypatch.setattr(
        "cdp_ask.followup_resolve.cdp_registry.chat_url_for_registration",
        lambda rid: CSE_A,
    )
    req = FollowupProjectAskRequest(
        chat_url=CSE_A,
        registration_id="reg-stale",
        prompt_text="x",
    )
    _target, err, _path, _binding = await resolve_followup_target(req, store)
    assert err is not None
    assert err.error == "ambiguous_attended"


@pytest.mark.asyncio
async def test_chat_url_only_discovers_attached_lane(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ExecutionStore()
    reg = _reg("reg-1")
    monkeypatch.setattr(
        "cdp_ask.followup_resolve.cdp_registry.list_active",
        lambda: [reg],
    )
    monkeypatch.setattr(
        "cdp_ask.followup_resolve.cdp_registry.chat_url_for_registration",
        lambda rid: CSE_A if rid == "reg-1" else None,
    )
    monkeypatch.setattr(
        "cdp_ask.followup_resolve.scan_lane_cse_urls",
        AsyncMock(return_value=[CSE_A]),
    )
    monkeypatch.setattr(
        "cdp_ask.followup_resolve.cdp_registry.chat_url_for_registration",
        lambda rid: CSE_A if rid == "reg-1" else None,
    )
    req = FollowupProjectAskRequest(chat_url=CSE_A, prompt_text="x")
    target, err, path, _binding = await resolve_followup_target(req, store)
    assert err is None
    assert target is not None
    assert target.chat_url == CSE_A
    assert path == "chat_url"


@pytest.mark.asyncio
async def test_chat_url_only_cse_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    store = ExecutionStore()
    monkeypatch.setattr(
        "cdp_ask.followup_resolve.cdp_registry.list_active",
        lambda: [_reg("reg-1")],
    )
    monkeypatch.setattr(
        "cdp_ask.followup_resolve.cdp_registry.chat_url_for_registration",
        lambda rid: CSE_A,
    )
    monkeypatch.setattr(
        "cdp_ask.followup_resolve.scan_lane_cse_urls",
        AsyncMock(return_value=[]),
    )
    req = FollowupProjectAskRequest(chat_url=CSE_A, prompt_text="x")
    _target, err, _path, _binding = await resolve_followup_target(req, store)
    assert err is not None
    assert err.error == "cse_not_found_on_lane"


@pytest.mark.asyncio
async def test_ambiguous_identity_two_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ExecutionStore()
    monkeypatch.setattr(
        "cdp_ask.followup_resolve.cdp_registry.list_active",
        lambda: [_reg("reg-1"), _reg("reg-2", purpose="other")],
    )

    async def _scan(reg: _FakeReg) -> list[str]:
        return [CSE_A if reg.registration_id == "reg-1" else CSE_B]

    monkeypatch.setattr("cdp_ask.followup_resolve.scan_lane_cse_urls", _scan)
    req = FollowupProjectAskRequest(chat_url=CSE_A, prompt_text="x")
    # Two lanes both expose CSE when scanning all — force two matches via purpose omitted
    monkeypatch.setattr(
        "cdp_ask.followup_resolve.cdp_registry.list_active",
        lambda: [_reg("reg-1"), _reg("reg-2")],
    )

    async def _scan_both(reg: _FakeReg) -> list[str]:
        return [CSE_A]

    monkeypatch.setattr("cdp_ask.followup_resolve.scan_lane_cse_urls", _scan_both)
    _target, err, _path, _binding = await resolve_followup_target(req, store)
    assert err is not None
    assert err.error == "ambiguous_identity"
    assert err.candidates is not None
    assert len(err.candidates) >= 2


@pytest.mark.asyncio
async def test_stale_registration_id_proceeds_when_chat_url_unique(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F1 (b): arm-time registration_id rot with one live chat_url candidate."""
    store = ExecutionStore()
    monkeypatch.setattr(
        "cdp_ask.followup_resolve.cdp_registry.list_active",
        lambda: [_reg("reg-live")],
    )
    monkeypatch.setattr(
        "cdp_ask.followup_resolve.cdp_registry.chat_url_for_registration",
        lambda rid: CSE_A if rid == "reg-live" else None,
    )
    monkeypatch.setattr(
        "cdp_ask.followup_resolve.scan_lane_cse_urls",
        AsyncMock(return_value=[CSE_A]),
    )
    req = FollowupProjectAskRequest(
        chat_url=CSE_A,
        registration_id="reg-stale-arm-time",
        prompt_text="x",
    )
    target, err, path, _binding = await resolve_followup_target(req, store)
    assert err is None
    assert target is not None
    assert target.registration_id == "reg-live"
    assert target.chat_url == CSE_A
    assert path == "chat_url"


@pytest.mark.asyncio
async def test_same_lane_extra_does_not_block_explicit_holder_chat_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same-lane extras are non-holders; explicit holder chat_url still resolves."""
    store = ExecutionStore()
    monkeypatch.setattr(
        "cdp_ask.followup_resolve.cdp_registry.list_active",
        lambda: [_reg("reg-hop"), _reg("reg-old", cdp="http://127.0.0.1:9228")],
    )
    monkeypatch.setattr(
        "cdp_ask.followup_resolve.cdp_registry.chat_url_for_registration",
        lambda rid: CSE_A if rid == "reg-hop" else CSE_B,
    )
    monkeypatch.setattr(
        "cdp_ask.followup_resolve.scan_lane_cse_urls",
        AsyncMock(return_value=[CSE_A]),
    )
    req = FollowupProjectAskRequest(chat_url=CSE_A, prompt_text="x")
    target, err, path, _binding = await resolve_followup_target(req, store)
    assert err is None
    assert target is not None
    assert target.registration_id == "reg-hop"
    assert target.chat_url == CSE_A
    assert path == "chat_url"


@pytest.mark.asyncio
async def test_execution_id_registration_conflict_still_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ExecutionStore()
    rec = await store.create(holder="h", purpose="ask")
    await store.set_registration_id(rec.execution_id, "reg-mapped")
    monkeypatch.setattr(
        "cdp_ask.followup_resolve.cdp_registry.list_active",
        lambda: [_reg("reg-live")],
    )
    monkeypatch.setattr(
        "cdp_ask.followup_resolve.cdp_registry.chat_url_for_registration",
        lambda rid: CSE_A if rid == "reg-live" else None,
    )
    monkeypatch.setattr(
        "cdp_ask.followup_resolve.scan_lane_cse_urls",
        AsyncMock(return_value=[CSE_A]),
    )
    req = FollowupProjectAskRequest(
        chat_url=CSE_A,
        registration_id="reg-other",
        execution_id=rec.execution_id,
        prompt_text="x",
    )
    _target, err, _path, _binding = await resolve_followup_target(req, store)
    assert err is not None
    assert err.error == "ambiguous_identity"


@pytest.mark.asyncio
async def test_lane_not_attached_detail_mentions_cli(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ExecutionStore()
    monkeypatch.setattr("cdp_ask.followup_resolve.cdp_registry.list_active", lambda: [])
    req = FollowupProjectAskRequest(registration_id="missing", prompt_text="x")
    _target, err, _path, _binding = await resolve_followup_target(req, store)
    assert err is not None
    assert err.error == "lane_not_attached"
    assert "cowork_chat_followup.py" in (err.detail or "")


@pytest.mark.asyncio
async def test_execution_id_maps_to_lane(monkeypatch: pytest.MonkeyPatch) -> None:
    store = ExecutionStore()
    rec = await store.create(holder="h", purpose="ask")
    await store.set_registration_id(rec.execution_id, "reg-1")
    monkeypatch.setattr(
        "cdp_ask.followup_resolve.cdp_registry.list_active",
        lambda: [_reg("reg-1")],
    )
    monkeypatch.setattr(
        "cdp_ask.followup_resolve.scan_lane_cse_urls",
        AsyncMock(return_value=[CSE_A]),
    )
    req = FollowupProjectAskRequest(execution_id=rec.execution_id, prompt_text="x")
    target, err, path, _binding = await resolve_followup_target(req, store)
    assert err is None
    assert target is not None
    assert path == "execution_id"


@pytest.mark.asyncio
async def test_resolver_never_register_or_goto(monkeypatch: pytest.MonkeyPatch) -> None:
    store = ExecutionStore()
    register = MagicMock()
    goto = AsyncMock()
    monkeypatch.setattr("cdp_ask.followup_resolve.cdp_registry.register_lane", register)
    monkeypatch.setattr(
        "cdp_ask.followup_resolve.cdp_registry.list_active",
        lambda: [_reg("reg-1")],
    )
    monkeypatch.setattr(
        "cdp_ask.followup_resolve.scan_lane_cse_urls",
        AsyncMock(return_value=[CSE_A]),
    )
    req = FollowupProjectAskRequest(registration_id="reg-1", prompt_text="x")
    await resolve_followup_target(req, store)
    register.assert_not_called()
    goto.assert_not_called()


@pytest.mark.asyncio
async def test_deregister_race_before_paste(monkeypatch: pytest.MonkeyPatch) -> None:
    store = ExecutionStore()
    reg = _reg("reg-1")
    monkeypatch.setattr("cdp_ask.followup_resolve.cdp_registry.list_active", lambda: [reg])
    monkeypatch.setattr(
        "cdp_ask.followup_resolve.scan_lane_cse_urls",
        AsyncMock(return_value=[CSE_A]),
    )
    monkeypatch.setattr(
        "cdp_ask.followup._find_page_on_lane",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr("cdp_ask.followup.emit_followup_event", lambda _e: None)
    req = FollowupProjectAskRequest(registration_id="reg-1", prompt_text="wake")
    resp = await execute_followup(req, store)
    assert resp.ok is False
    assert resp.error in {"cse_not_found_on_lane", "lane_not_attached"}


@pytest.mark.asyncio
async def test_send_unverified_when_verification_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ExecutionStore()
    reg = _reg("reg-1")
    monkeypatch.setattr("cdp_ask.followup_resolve.cdp_registry.list_active", lambda: [reg])
    monkeypatch.setattr(
        "cdp_ask.followup_resolve.scan_lane_cse_urls",
        AsyncMock(return_value=[CSE_A]),
    )
    page = MagicMock()
    page.url = CSE_A
    monkeypatch.setattr(
        "cdp_ask.followup._find_page_on_lane",
        AsyncMock(return_value=(page, AsyncMock())),
    )
    monkeypatch.setattr(
        "cdp_ask.followup.send_followup_paste_half",
        AsyncMock(
            return_value={
                "send_verified": False,
                "receipt": None,
                "streaming_at_paste": False,
                "url": CSE_A,
                "pasted_at": 1.0,
                "error": "send_unverified",
            }
        ),
    )
    monkeypatch.setattr("cdp_ask.followup.emit_followup_event", lambda _e: None)

    resp = await execute_followup(
        FollowupProjectAskRequest(registration_id="reg-1", prompt_text="x"),
        store,
    )
    assert resp.ok is False
    assert resp.error == "send_unverified"
    assert resp.send_verified is False


@pytest.mark.asyncio
async def test_stale_registration_id_execute_followup_proceeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: stale arm-time registration_id still pastes on unique chat_url."""
    store = ExecutionStore()
    reg = _reg("reg-live")
    monkeypatch.setattr("cdp_ask.followup_resolve.cdp_registry.list_active", lambda: [reg])
    monkeypatch.setattr(
        "cdp_ask.followup_resolve.cdp_registry.chat_url_for_registration",
        lambda rid: CSE_A if rid == "reg-live" else None,
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
                "streaming_at_paste": True,
                "url": CSE_A,
                "pasted_at": 2.0,
                "error": None,
            }
        ),
    )
    monkeypatch.setattr("cdp_ask.followup.emit_followup_event", lambda _e: None)
    resp = await execute_followup(
        FollowupProjectAskRequest(
            chat_url=CSE_A,
            registration_id="reg-stale-arm-time",
            prompt_text="wake",
        ),
        store,
    )
    assert resp.ok is True
    assert resp.send_verified is True
    assert resp.url == CSE_A


@pytest.mark.asyncio
async def test_ok_requires_send_verified(monkeypatch: pytest.MonkeyPatch) -> None:
    store = ExecutionStore()
    reg = _reg("reg-1")
    monkeypatch.setattr("cdp_ask.followup_resolve.cdp_registry.list_active", lambda: [reg])
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
                "streaming_at_paste": True,
                "url": CSE_A,
                "pasted_at": 2.0,
                "error": None,
            }
        ),
    )
    monkeypatch.setattr("cdp_ask.followup.emit_followup_event", lambda _e: None)
    resp = await execute_followup(
        FollowupProjectAskRequest(registration_id="reg-1", prompt_text="x"),
        store,
    )
    assert resp.ok is True
    assert resp.send_verified is True
    assert resp.url == CSE_A


@pytest.mark.asyncio
async def test_purpose_narrows_candidates(monkeypatch: pytest.MonkeyPatch) -> None:
    store = ExecutionStore()
    monkeypatch.setattr(
        "cdp_ask.followup_resolve.cdp_registry.list_active",
        lambda: [
            _reg("reg-1", purpose="operator-proxy"),
            _reg("reg-2", purpose="ask"),
        ],
    )
    monkeypatch.setattr(
        "cdp_ask.followup_resolve.scan_lane_cse_urls",
        AsyncMock(return_value=[CSE_A]),
    )
    candidates, _path, _exe = await discover_candidates(
        FollowupProjectAskRequest(chat_url=CSE_A, purpose="operator-proxy"),
        store,
    )
    assert len(candidates) == 1
    assert candidates[0].registration_id == "reg-1"
