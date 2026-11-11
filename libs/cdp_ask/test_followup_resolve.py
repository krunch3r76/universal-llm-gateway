"""Unit tests for followup identity resolution (friction 4)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from cdp_ask.execution_store import ExecutionStore
from cdp_ask.followup_envelope import FollowupCandidate
from cdp_ask.followup_resolve import (
    _registry_pairs_for_chat_url,
    conflicting_keys,
    registration_chat_identity_mismatch,
    resolve_followup_target,
    stale_registration_id_conflict,
)
from cdp_ask.models import FollowupProjectAskRequest

pytestmark = pytest.mark.offline

CSE_A = "https://claude.ai/cowork/cse_abc123"
CSE_B = "https://claude.ai/cowork/cse_other456"


@dataclass(frozen=True)
class _FakeReg:
    registration_id: str
    port: int
    profile_suffix: str
    profile: Path
    cdp_url: str
    holder: str
    purpose: str | None = None


def _reg(reg_id: str, *, cdp: str = "http://127.0.0.1:9223") -> _FakeReg:
    return _FakeReg(
        registration_id=reg_id,
        port=9223,
        profile_suffix="s",
        profile=Path("/tmp/p"),
        cdp_url=cdp,
        holder="holder-a",
        purpose="operator-proxy",
    )


def _req(**kwargs: Any) -> FollowupProjectAskRequest:
    base = {"prompt_text": "x"}
    base.update(kwargs)
    return FollowupProjectAskRequest(**base)  # type: ignore[arg-type]


@pytest.fixture
def store() -> ExecutionStore:
    return ExecutionStore()


def test_registry_pairs_for_chat_url_keeps_caller_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Registry row URL must not replace the supplied chat_url in candidates."""
    reg = _reg("reg-live")
    fake_registration = MagicMock()
    fake_registration.registration_id = reg.registration_id
    fake_registration.cdp_url = reg.cdp_url
    fake_registration.holder = reg.holder
    fake_registration.purpose = reg.purpose

    monkeypatch.setattr(
        "cdp_ask.followup_resolve.attachment_for_chat_url",
        lambda _url: fake_registration,
    )
    monkeypatch.setattr(
        "cdp_ask.followup_resolve.cdp_registry.chat_url_for_registration",
        lambda _rid: CSE_B,
    )
    monkeypatch.setattr(
        "cdp_ask.followup_resolve.cdp_registry.list_active",
        lambda: [reg],
    )

    pairs = _registry_pairs_for_chat_url(CSE_A)
    assert len(pairs) == 1
    assert pairs[0].chat_url == CSE_A
    assert pairs[0].chat_url != CSE_B


def test_registration_chat_identity_mismatch_detects_differing_chats(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "cdp_ask.followup_resolve.cdp_registry.chat_url_for_registration",
        lambda rid: CSE_B if rid == "reg-stored" else None,
    )
    assert registration_chat_identity_mismatch(
        _req(chat_url=CSE_A, registration_id="reg-stored")
    )
    assert not registration_chat_identity_mismatch(
        _req(chat_url=CSE_A, registration_id="reg-stored-miss")
    )
    assert not registration_chat_identity_mismatch(_req(chat_url=CSE_A))


def test_stale_registration_waiver_removed_when_chats_differ(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    req = _req(chat_url=CSE_A, registration_id="reg-stale")
    chosen = FollowupCandidate(
        registration_id="reg-live",
        chat_url=CSE_A,
        holder="h",
        purpose=None,
        cdp_url="http://127.0.0.1:9223",
    )
    monkeypatch.setattr(
        "cdp_ask.followup_resolve.cdp_registry.chat_url_for_registration",
        lambda rid: CSE_B if rid == "reg-stale" else CSE_A,
    )
    monkeypatch.setattr(
        "cdp_ask.followup_resolve._registry_pairs_for_chat_url",
        lambda *_a, **_k: [chosen],
    )
    assert stale_registration_id_conflict(req, chosen) is False
    assert conflicting_keys(req, chosen, mapped_reg=None) is True


def test_stale_registration_waiver_when_same_chat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    req = _req(chat_url=CSE_A, registration_id="reg-stale")
    chosen = FollowupCandidate(
        registration_id="reg-live",
        chat_url=CSE_A,
        holder="h",
        purpose=None,
        cdp_url="http://127.0.0.1:9223",
    )
    monkeypatch.setattr(
        "cdp_ask.followup_resolve.cdp_registry.chat_url_for_registration",
        lambda rid: CSE_A if rid == "reg-stale" else CSE_A,
    )
    monkeypatch.setattr(
        "cdp_ask.followup_resolve._registry_pairs_for_chat_url",
        lambda *_a, **_k: [chosen],
    )
    assert stale_registration_id_conflict(req, chosen) is True
    assert conflicting_keys(req, chosen, mapped_reg=None) is False


@pytest.mark.asyncio
async def test_both_keys_differing_chats_refuse_identity_mismatch(
    monkeypatch: pytest.MonkeyPatch,
    store: ExecutionStore,
) -> None:
    monkeypatch.setattr(
        store,
        "active_work_snapshot",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "cdp_ask.followup_resolve.cdp_registry.chat_url_for_registration",
        lambda rid: CSE_B if rid == "reg-named" else None,
    )
    scan = AsyncMock()
    monkeypatch.setattr("cdp_ask.followup_resolve.scan_lane_cse_urls", scan)

    _target, err, _path, _binding = await resolve_followup_target(
        _req(chat_url=CSE_A, registration_id="reg-named"),
        store,
    )

    assert _target is None
    assert err is not None
    assert err.error == "identity_mismatch"
    scan.assert_not_called()


@pytest.mark.asyncio
async def test_matched_pair_both_keys_same_chat_resolves(
    monkeypatch: pytest.MonkeyPatch,
    store: ExecutionStore,
) -> None:
    reg = _reg("reg-live")
    fake_registration = MagicMock()
    fake_registration.registration_id = reg.registration_id
    fake_registration.cdp_url = reg.cdp_url
    fake_registration.holder = reg.holder
    fake_registration.purpose = reg.purpose

    monkeypatch.setattr(
        store,
        "active_work_snapshot",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "cdp_ask.followup_resolve.attachment_for_chat_url",
        lambda _url: fake_registration,
    )
    monkeypatch.setattr(
        "cdp_ask.followup_resolve.cdp_registry.chat_url_for_registration",
        lambda rid: CSE_A if rid == "reg-live" else None,
    )
    monkeypatch.setattr(
        "cdp_ask.followup_resolve.cdp_registry.list_active",
        lambda: [reg],
    )
    monkeypatch.setattr(
        "cdp_ask.followup_resolve.scan_lane_cse_urls",
        AsyncMock(return_value=[CSE_A]),
    )

    target, err, _path, _binding = await resolve_followup_target(
        _req(chat_url=CSE_A, registration_id="reg-live"),
        store,
    )

    assert err is None
    assert target is not None
    assert target.registration_id == "reg-live"
    assert target.chat_url == CSE_A


@pytest.mark.asyncio
async def test_chat_url_only_unchanged(
    monkeypatch: pytest.MonkeyPatch,
    store: ExecutionStore,
) -> None:
    reg = _reg("reg-live")
    fake_registration = MagicMock()
    fake_registration.registration_id = reg.registration_id
    fake_registration.cdp_url = reg.cdp_url
    fake_registration.holder = reg.holder
    fake_registration.purpose = reg.purpose

    monkeypatch.setattr(
        store,
        "active_work_snapshot",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "cdp_ask.followup_resolve.attachment_for_chat_url",
        lambda _url: fake_registration,
    )
    monkeypatch.setattr(
        "cdp_ask.followup_resolve.cdp_registry.chat_url_for_registration",
        lambda _rid: CSE_B,
    )
    monkeypatch.setattr(
        "cdp_ask.followup_resolve.cdp_registry.list_active",
        lambda: [reg],
    )
    monkeypatch.setattr(
        "cdp_ask.followup_resolve.scan_lane_cse_urls",
        AsyncMock(return_value=[CSE_A]),
    )

    target, err, _path, _binding = await resolve_followup_target(
        _req(chat_url=CSE_A),
        store,
    )

    assert err is None
    assert target is not None
    assert target.chat_url == CSE_A


@pytest.mark.asyncio
async def test_registration_id_only_unchanged(
    monkeypatch: pytest.MonkeyPatch,
    store: ExecutionStore,
) -> None:
    reg = _reg("reg-live")

    monkeypatch.setattr(
        store,
        "active_work_snapshot",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "cdp_ask.followup_resolve.cdp_registry.list_active",
        lambda: [reg],
    )
    monkeypatch.setattr(
        "cdp_ask.followup_resolve.scan_lane_cse_urls",
        AsyncMock(return_value=[CSE_A]),
    )

    target, err, _path, _binding = await resolve_followup_target(
        _req(registration_id="reg-live"),
        store,
    )

    assert err is None
    assert target is not None
    assert target.registration_id == "reg-live"
    assert target.chat_url == CSE_A
