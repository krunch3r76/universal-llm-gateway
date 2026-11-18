"""l2 r6 item 1(e): call-path proofs for claude_seat_identity_key (red-first)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = pytest.mark.offline


@pytest.fixture(autouse=True)
def _clear_followup_lane_state() -> None:
    from cdp_ask import followup as fu

    fu._lane_locks.clear()
    fu._lane_targets.clear()
    yield
    fu._lane_locks.clear()
    fu._lane_targets.clear()


_CSE = "https://claude.ai/cowork/cse_l2r6CallPath"
_CSE_WWW = "https://www.claude.ai/cowork/cse_l2r6CallPath"
_OTHER = "https://claude.ai/cowork/cse_l2r6OtherSeat"
_CHAT_UUID = "2762063d-2966-4859-a3ce-23831c6e0c86"
_CANON_CHAT = f"https://claude.ai/chat/{_CHAT_UUID}"
_WWW_UPPER_CHAT = f"https://www.claude.ai/chat/{_CHAT_UUID.upper()}"


@pytest.fixture
def isolated_registry(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    from claude_bundles import cdp_registry as reg
    from claude_bundles import cdp_registry_store as store_mod
    from claude_bundles import cse_provenance

    from services.git_integration_worker import cse_session_holders

    root = tmp_path / "cdp-registry"
    root.mkdir()
    regs = root / "registrations"
    regs.mkdir()
    paths = {
        "REGISTRY_DIR": root,
        "REGISTRY_LOG": root / "registry.jsonl",
        "ACTIVE_JSON": root / "active.json",
        "SESSIONS_JSON": root / "sessions.json",
        "SESSION_TRANSITIONS_JSONL": root / "session_transitions.jsonl",
        "PORTS_LOCK": root / "ports.lock",
        "REGISTRATIONS_DIR": regs,
    }
    monkeypatch.setenv("CDP_REGISTRY_SEAT_AUTHORITY", "1")
    for name, value in paths.items():
        monkeypatch.setattr(reg._store, name, value)
        monkeypatch.setattr(cse_provenance.store, name, value)
        monkeypatch.setattr(store_mod, name, value)
    monkeypatch.setattr(
        cse_session_holders,
        "upsert_holder_remote",
        lambda **_kw: cse_session_holders.RemoteUpsert.OK,
    )
    return root


def test_carve_out_false_when_holder_and_request_same_seat_spelling(
    isolated_registry: Path,
) -> None:
    """Spelling-only CSE difference must not arm dormant misbound carve-out (:416)."""
    from claude_bundles import cdp_registry as reg
    from claude_bundles.cdp_registry.models import STATUS_DORMANT

    from cdp_ask.followup_resolve import _dormant_misbound_holder_carve_out
    from cdp_ask.models import FollowupProjectAskRequest

    lane = "l2r6"
    with reg._store.ports_lock():
        active = reg._store.load_active()
        holder_rid = "reg-holder"
        mint_rid = "reg-mint"
        active[holder_rid] = {
            "registration_id": holder_rid,
            "port": 9223,
            "status": STATUS_DORMANT,
            "purpose": "operator-proxy",
            "parent_thread": lane,
            "chat_url": _CSE_WWW,
        }
        active[mint_rid] = {
            "registration_id": mint_rid,
            "port": 9224,
            "status": "active",
            "purpose": "operator-proxy",
            "parent_thread": lane,
            "chat_url": _CSE,
        }
        reg._store.write_active(active)

    req = FollowupProjectAskRequest(
        prompt_text="x",
        parent_thread=lane,
        chat_url=_CSE,
        registration_id=mint_rid,
        reattach=True,
        purpose="operator-proxy",
    )
    assert (
        _dormant_misbound_holder_carve_out(
            req,
            lane,
            mint_rid,
            holder_rid,
            reattach_used=True,
            outcome_reg=mint_rid,
            lane_pin={},
        )
        is False
    )


def test_restore_session_address_spelling_only_restores_canonical(
    isolated_registry: Path,
) -> None:
    """restore_session_address must not rebound on spelling-only borrow (:390)."""
    from claude_bundles import cdp_registry as reg

    host = reg.register_lane(
        holder="h",
        purpose="operator-proxy",
        launch_chrome=lambda *_a, **_k: None,
        is_listening=lambda _p: False,
    )
    rid = host.registration_id
    with reg._store.ports_lock():
        active = reg._store.load_active()
        row = dict(active[rid])
        row["chat_url"] = _CSE_WWW
        active[rid] = row
        reg._store.write_active(active)
    status = reg.restore_session_address(
        rid,
        expected_chat_url=_CSE,
        restore_to=_WWW_UPPER_CHAT,
    )
    assert status == "restored"
    stored = reg.chat_url_for_registration(rid)
    assert stored == _CANON_CHAT


@pytest.mark.asyncio
async def test_ensure_cse_attached_does_not_skip_owner_with_www_stored(
    isolated_registry: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Lane holding www spelling of target must not be skipped (:639)."""
    from claude_bundles import cdp_registry as reg

    from cdp_ask.followup_reattach import ensure_cse_attached

    host = reg.register_lane(
        holder="owner",
        purpose="operator-proxy",
        launch_chrome=lambda *_a, **_k: None,
        is_listening=lambda _p: False,
    )
    with reg._store.ports_lock():
        active = reg._store.load_active()
        row = dict(active[host.registration_id])
        row["chat_url"] = _CSE_WWW
        active[host.registration_id] = row
        reg._store.write_active(active)

    page = MagicMock()
    page.url = _CSE_WWW
    pw = MagicMock()
    pw.stop = AsyncMock()
    monkeypatch.setattr(
        "cdp_ask.followup_reattach.cdp_registry.list_active",
        lambda: [host],
    )
    monkeypatch.setattr(
        "cdp_ask.followup_reattach.find_page_on_lane",
        AsyncMock(return_value=(page, pw)),
    )
    monkeypatch.setattr(
        "cdp_ask.followup_reattach.cdp_registry.bind_chat_url_only",
        lambda *_a, **_k: True,
    )

    outcome = await ensure_cse_attached(_CSE, holder="owner", purpose="operator-proxy")
    assert outcome.ok is True
    assert outcome.registration_id == host.registration_id


def _mock_playwright_for_resolve(monkeypatch: pytest.MonkeyPatch, page_url: str) -> None:
    async def _start() -> MagicMock:
        pw = MagicMock()
        page = MagicMock()
        page.url = page_url
        ctx = MagicMock()
        ctx.pages = [page]
        browser = MagicMock()
        browser.contexts = [ctx]
        pw.chromium.connect_over_cdp = AsyncMock(return_value=browser)
        pw.stop = AsyncMock()
        return pw

    factory = MagicMock()
    factory.start = AsyncMock(side_effect=_start)
    monkeypatch.setattr("cdp_ask.followup_resolve.async_playwright", lambda: factory)


@pytest.mark.asyncio
async def test_paste_verify_accepts_www_page_for_canonical_target(
    isolated_registry: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Paste gate (:643) must accept www page URL for canonical stored target."""
    from claude_bundles import cdp_registry as reg

    from cdp_ask.execution_store import ExecutionStore
    from cdp_ask.followup import execute_followup
    from cdp_ask.models import FollowupProjectAskRequest

    host = reg.register_lane(
        holder="pv",
        purpose="operator-proxy",
        launch_chrome=lambda *_a, **_k: None,
        is_listening=lambda _p: False,
    )
    rid = "reg-pv"
    with reg._store.ports_lock():
        active = reg._store.load_active()
        row = dict(active[host.registration_id])
        row["registration_id"] = rid
        active[rid] = row
        active.pop(host.registration_id, None)
        reg._store.write_active(active)
    reg.bind_chat_url_only(rid, chat_url=_CSE)

    _mock_playwright_for_resolve(monkeypatch, _CSE)
    page = MagicMock()
    page.url = _CSE_WWW
    pw = MagicMock()
    pw.stop = AsyncMock()
    paste_mock = AsyncMock(return_value={"ok": True, "receipt": "dom_paste"})
    monkeypatch.setattr(
        "cdp_ask.followup.find_page_on_lane",
        AsyncMock(return_value=(page, pw)),
    )
    monkeypatch.setattr("cdp_ask.followup.send_followup_paste_half", paste_mock)

    store = ExecutionStore(reaper_interval_s=9999.0)
    req = FollowupProjectAskRequest(
        prompt_text="memo",
        parent_thread="lane-pv",
        chat_url=_CSE,
        registration_id=rid,
    )
    resp = await execute_followup(req, store)
    assert resp.ok is True
    paste_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_skip_borrow_restore_inflight_paste_direct() -> None:
    from cdp_ask.followup import (
        _acquire_lane,
        _release_lane,
        _skip_borrow_restore_for_inflight_paste,
    )
    from cdp_ask.followup_reattach import ReattachOutcome

    rid = "reg-l2r7-252a"
    assert await _acquire_lane(rid, target_chat_url=_CSE_WWW)
    try:
        outcome = ReattachOutcome(
            ok=True,
            registration_id=rid,
            reattach_bound_chat_url=_CSE,
        )
        assert _skip_borrow_restore_for_inflight_paste(outcome) is True
    finally:
        _release_lane(rid)


@pytest.mark.asyncio
async def test_reattach_teardown_skips_borrow_restore_when_inflight_match(
    isolated_registry: Path,
) -> None:
    from claude_bundles import cdp_registry as reg

    from cdp_ask.followup import _acquire_lane, _reattach_teardown, _release_lane
    from cdp_ask.followup_reattach import ReattachOutcome

    host = reg.register_lane(
        holder="tb",
        purpose="operator-proxy",
        launch_chrome=lambda *_a, **_k: None,
        is_listening=lambda _p: False,
    )
    rid = host.registration_id
    reg.bind_chat_url_only(rid, chat_url=_CSE)
    assert await _acquire_lane(rid, target_chat_url=_CSE_WWW)
    try:
        outcome = ReattachOutcome(
            ok=True,
            registration_id=rid,
            prior_chat_url=_OTHER,
            reattach_bound_chat_url=_CSE,
            lane_created=False,
            relaunched=False,
            page_only_borrow=False,
        )
        await _reattach_teardown(
            outcome,
            retain_lane=True,
            restore_borrowed_chat_url=True,
            skip_inflight_restore=True,
        )
        assert reg.chat_url_for_registration(rid) == _CSE
    finally:
        _release_lane(rid)


def test_holder_page_open_true_for_www_tab_of_canonical_holder(
    isolated_registry: Path,
) -> None:
    """holder_page_open (:188) when probe lists www spelling of canonical holder."""
    from cdp_ask.lane_current_cse import resolve_lane_current_cse

    class _Live:
        parent_thread = "lane-188"
        registration_id = "reg-h"
        purpose = "operator-proxy"
        seat_lane = "lane-188"
        seat_closed_at = None

    lane = "lane-188"
    body = resolve_lane_current_cse(
        lane,
        snap={
            "seat_rows": [
                {
                    "registration_id": "reg-h",
                    "parent_thread": lane,
                    "purpose": "operator-proxy",
                    "seat_bound_at": 1.0,
                    "chat_url": _CANON_CHAT,
                }
            ],
            "rows": [],
        },
        list_pages=lambda: iter([(9223, _WWW_UPPER_CHAT, None)]),
        probe_page=lambda _p, _w: ({"streaming": False}, True),
        provenance_for=lambda url: (
            {
                "parent_thread_claim": lane,
                "reason": "idle_exit",
                "registration_id": "reg-h",
            }
            if url == _CANON_CHAT
            else None
        ),
        list_active=lambda: [_Live()],
        chat_url_for_registration=lambda rid: _CANON_CHAT if rid == "reg-h" else None,
        purpose_for_registration=lambda _rid: "operator-proxy",
        now=lambda: 1_700_000_000.0,
    )
    seat = body.get("seat_holder") or {}
    assert seat.get("evidence_class") == "live_binding"


def test_release_dead_orphan_www_spelling_matches_canonical_query() -> None:
    """release_dead_chat_url_holders (:193/:201) reaps legacy www orphan."""
    from claude_bundles import cdp_lane_reaper as reaper

    canonical = _CSE
    legacy = _CSE_WWW
    active = {
        "orph-1": {
            "registration_id": "orph-1",
            "port": 9223,
            "status": "orphaned_alive",
            "chat_url": legacy,
        }
    }
    reaped = reaper.release_dead_chat_url_holders(
        active,
        lambda _port: False,
        canonical,
        pid_alive=lambda _pid: False,
        is_attached=lambda _rid: False,
    )
    assert reaped == ["orph-1"]
    assert active["orph-1"]["status"] == "released"
