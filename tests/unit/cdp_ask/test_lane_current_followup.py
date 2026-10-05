"""Followup gate uses the live lane-current probe (a:37834 falsifier)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from cdp_ask.execution_store import ExecutionStore
from cdp_ask.followup_resolve import resolve_followup_target
from cdp_ask.models import FollowupProjectAskRequest

pytestmark = pytest.mark.offline

CSE = "https://claude.ai/cowork/cse_01XLJF9g6JFdHYnrDSePwc23"
OTHER = "https://claude.ai/cowork/cse_017cw5A7geCQNPPP7LzB78vB"


@dataclass(frozen=True)
class _FakeReg:
    registration_id: str
    port: int
    profile_suffix: str
    profile: Path
    cdp_url: str
    holder: str
    purpose: str | None = None


def _reg(reg_id: str) -> _FakeReg:
    return _FakeReg(
        registration_id=reg_id,
        port=9223,
        profile_suffix="s",
        profile=Path("/tmp/p"),
        cdp_url="http://127.0.0.1:9223",
        holder="holder-a",
        purpose="operator-proxy",
    )


def _vacant_snap() -> dict:
    return {"seat_rows": [], "observed_at": "t"}


def _held_snap(reg_id: str = "reg-held") -> dict:
    return {
        "seat_rows": [
            {
                "registration_id": reg_id,
                "parent_thread": "12286",
                "purpose": "operator-proxy",
                "seat_bound_at": 100.0,
            }
        ],
        "observed_at": "t",
    }


def _patch_chat_resolve(monkeypatch: pytest.MonkeyPatch, reg: _FakeReg) -> None:
    monkeypatch.setattr(
        "cdp_ask.followup_resolve.attachment_for_chat_url",
        lambda _url: reg,
    )
    monkeypatch.setattr(
        "cdp_ask.followup_resolve.cdp_registry.list_active",
        lambda: [reg],
    )
    monkeypatch.setattr(
        "cdp_ask.followup_resolve.cdp_registry.chat_url_for_registration",
        lambda rid: CSE if rid == reg.registration_id else None,
    )
    monkeypatch.setattr(
        "cdp_ask.followup_resolve.scan_lane_cse_urls",
        AsyncMock(return_value=[CSE]),
    )
    monkeypatch.setattr(
        "cdp_ask.followup_resolve.resolve_provenance",
        lambda **_k: {"state": "current"},
    )


@pytest.mark.asyncio
async def test_parent_thread_in_flight_sets_chat_url_and_lane_current(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ExecutionStore()
    monkeypatch.setattr(
        "cdp_ask.followup_resolve.read_cdp_lane_snapshot", lambda: _vacant_snap()
    )
    monkeypatch.setattr("cdp_ask.followup_events.emit", lambda _e: None)

    def probe(lane: str, *, snap: dict | None) -> dict:
        assert lane == "12286"
        assert isinstance(snap, dict)
        return {
            "state": "current",
            "basis": "in_flight",
            "current": {"chat_url": CSE},
            "candidates": [],
        }

    monkeypatch.setattr("cdp_ask.lane_current_cse.resolve_lane_current_cse", probe)
    _patch_chat_resolve(monkeypatch, _reg("reg-live"))
    req = FollowupProjectAskRequest(parent_thread="12286", prompt_text="x")
    target, err, path, _binding = await resolve_followup_target(req, store)
    assert err is None
    assert target is not None
    assert target.chat_url == CSE
    assert target.registration_id == "reg-live"
    assert path == "lane_current"


@pytest.mark.asyncio
async def test_seat_holder_basis_keeps_registration_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ExecutionStore()
    monkeypatch.setattr(
        "cdp_ask.followup_resolve.read_cdp_lane_snapshot", lambda: _held_snap()
    )
    monkeypatch.setattr("cdp_ask.followup_events.emit", lambda _e: None)
    monkeypatch.setattr(
        "cdp_ask.lane_current_cse.resolve_lane_current_cse",
        lambda lane, *, snap: {
            "state": "current",
            "basis": "seat_holder",
            "current": {"chat_url": CSE},
            "candidates": [],
        },
    )
    monkeypatch.setattr(
        "cdp_ask.followup_resolve.cdp_registry.list_active",
        lambda: [_reg("reg-held")],
    )
    monkeypatch.setattr(
        "cdp_ask.followup_resolve.scan_lane_cse_urls",
        AsyncMock(return_value=[CSE]),
    )
    req = FollowupProjectAskRequest(parent_thread="12286", prompt_text="x")
    target, err, path, _binding = await resolve_followup_target(req, store)
    assert err is None
    assert target is not None
    assert target.registration_id == "reg-held"
    assert path != "lane_current"


@pytest.mark.asyncio
async def test_ambiguous_without_holder_is_lane_cse_ambiguous(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ExecutionStore()
    monkeypatch.setattr(
        "cdp_ask.followup_resolve.read_cdp_lane_snapshot", lambda: _vacant_snap()
    )
    monkeypatch.setattr("cdp_ask.followup_events.emit", lambda _e: None)
    monkeypatch.setattr(
        "cdp_ask.lane_current_cse.resolve_lane_current_cse",
        lambda lane, *, snap: {
            "state": "ambiguous",
            "basis": None,
            "reason": "multiple_in_flight",
            "current": None,
            "candidates": [{"chat_url": CSE}, {"chat_url": OTHER}],
        },
    )
    req = FollowupProjectAskRequest(parent_thread="12286", prompt_text="x")
    _target, err, path, _binding = await resolve_followup_target(req, store)
    assert path == "parent_thread"
    assert err is not None
    assert err.error == "lane_cse_ambiguous"
    assert CSE in (err.detail or "")
    assert OTHER in (err.detail or "")


@pytest.mark.asyncio
async def test_probe_raise_falls_through_to_holder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ExecutionStore()
    monkeypatch.setattr(
        "cdp_ask.followup_resolve.read_cdp_lane_snapshot", lambda: _held_snap()
    )
    monkeypatch.setattr("cdp_ask.followup_events.emit", lambda _e: None)

    def boom(lane: str, *, snap: dict | None) -> dict:
        raise RuntimeError("probe down")

    monkeypatch.setattr("cdp_ask.lane_current_cse.resolve_lane_current_cse", boom)
    monkeypatch.setattr(
        "cdp_ask.followup_resolve.cdp_registry.list_active",
        lambda: [_reg("reg-held")],
    )
    monkeypatch.setattr(
        "cdp_ask.followup_resolve.scan_lane_cse_urls",
        AsyncMock(return_value=[CSE]),
    )
    req = FollowupProjectAskRequest(parent_thread="12286", prompt_text="x")
    target, err, path, _binding = await resolve_followup_target(req, store)
    assert err is None
    assert target is not None
    assert target.registration_id == "reg-held"
    assert path != "lane_current"


@pytest.mark.asyncio
async def test_identity_supplied_does_not_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ExecutionStore()
    monkeypatch.setattr(
        "cdp_ask.followup_resolve.read_cdp_lane_snapshot", lambda: _vacant_snap()
    )
    monkeypatch.setattr("cdp_ask.followup_events.emit", lambda _e: None)

    def boom(lane: str, *, snap: dict | None) -> dict:
        raise AssertionError("probe must not run")

    monkeypatch.setattr("cdp_ask.lane_current_cse.resolve_lane_current_cse", boom)
    _patch_chat_resolve(monkeypatch, _reg("reg-live"))
    req = FollowupProjectAskRequest(
        parent_thread="12286",
        chat_url=CSE,
        prompt_text="x",
    )
    target, err, path, _binding = await resolve_followup_target(req, store)
    assert err is None
    assert target is not None
    assert path != "lane_current"
