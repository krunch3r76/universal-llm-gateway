"""Stand-down followup mints the journal token for superseded_registration_id.

The chat URL may resolve to the successor. Detach of the predecessor still
requires ``has_standdown_token`` on that predecessor (lifecycle standdown gate).
A followup that omits ``superseded_registration_id`` keeps minting the resolved
target and leaves the new response fields unset.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_PRED = "48f4f57e000000000000000000000001"
_SUCC = "8adaa748000000000000000000000002"
_OTHER = "1a690790000000000000000000000003"
_CHAT = "https://claude.ai/cowork/cse_01AroaPcisXcHRq6xqZ17pJF"


def _isolate_registry(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from claude_bundles import cdp_registry as reg

    root = tmp_path / "cdp-registry"
    regs = root / "registrations"
    regs.mkdir(parents=True)
    monkeypatch.setenv("CDP_REGISTRY_SEAT_AUTHORITY", "1")
    monkeypatch.setattr(reg._store, "REGISTRY_DIR", root)
    monkeypatch.setattr(reg._store, "REGISTRY_LOG", root / "registry.jsonl")
    monkeypatch.setattr(reg._store, "ACTIVE_JSON", root / "active.json")
    monkeypatch.setattr(reg._store, "PORTS_LOCK", root / "ports.lock")
    monkeypatch.setattr(reg._store, "REGISTRATIONS_DIR", regs)
    monkeypatch.setattr(reg.cdp_lane, "is_listening", lambda _port: False)
    monkeypatch.setattr(
        "services.git_integration_worker.cse_session_holders.release_holder_remote",
        lambda **_k: None,
    )
    monkeypatch.setattr(
        "claude_bundles.cse_provenance.append_episode",
        lambda **_k: None,
    )


def _predecessor_row() -> dict:
    return {
        "registration_id": _PRED,
        "port": 9333,
        "profile_suffix": "pred-standdown",
        "holder": "pred",
        "status": "active",
        "purpose": "operator-proxy",
        "parent_thread": "21732",
        "seat_lane": "21732",
        "seat_closed_at": 100.0,
        "seat_bound_at": 90.0,
        "started_at": 90.0,
        "chat_url": _CHAT,
        "superseded_by": _SUCC,
    }


@pytest.mark.asyncio
async def test_stand_down_mints_for_superseded_id_and_detach_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Resolved successor is the paste target; the token and detach use the predecessor."""
    from claude_bundles import cdp_registry as reg
    from claude_bundles.cdp_registry.attachment_journal import has_standdown_token

    from cdp_ask.execution_store import ExecutionStore
    from cdp_ask.followup import execute_followup
    from cdp_ask.followup_envelope import FollowupCandidate
    from cdp_ask.models import FollowupProjectAskRequest

    _isolate_registry(monkeypatch, tmp_path)
    reg._store.write_active({_PRED: _predecessor_row()})
    page = MagicMock()
    page.url = _CHAT
    pw = MagicMock()
    pw.stop = AsyncMock()
    candidate = FollowupCandidate(
        registration_id=_SUCC,
        chat_url=_CHAT,
        holder="succ",
        purpose="operator-proxy",
        cdp_url="http://127.0.0.1:9334",
    )
    with (
        patch(
            "cdp_ask.followup.resolve_followup_prompt",
            return_value="TYPE: SEAT_STAND_DOWN\nretire predecessor",
        ),
        patch(
            "cdp_ask.followup.resolve_followup_target",
            AsyncMock(return_value=(candidate, None, "chat_url", "explicit")),
        ),
        patch(
            "cdp_ask.followup.find_page_on_lane",
            AsyncMock(return_value=(page, pw)),
        ),
        patch(
            "cdp_ask.followup.send_followup_paste_half",
            AsyncMock(
                return_value={
                    "receipt": "dom_committed",
                    "url": _CHAT,
                    "pasted_at": 1792224619,
                    "streaming_at_paste": True,
                }
            ),
        ),
        patch("cdp_ask.followup.emit_followup_event", lambda _event: None),
        patch("cdp_ask.followup._stamp_followup_in_flight", lambda *_a, **_k: None),
    ):
        result = await execute_followup(
            FollowupProjectAskRequest(
                chat_url=_CHAT,
                registration_id=_SUCC,
                prompt_text="TYPE: SEAT_STAND_DOWN\nretire predecessor",
                envelope="stand_down",
                superseded_registration_id=_PRED,
                predecessor_registration_id=_OTHER,
                min_receipt="dom_committed",
                retain_lane=True,
            ),
            ExecutionStore(),
        )
    assert result.registration_id == _SUCC
    assert result.standdown_token_for == _PRED
    assert result.superseded_count == 1
    with reg._store.ports_lock():
        assert has_standdown_token(_PRED) is True
        assert has_standdown_token(_SUCC) is False
        assert has_standdown_token(_OTHER) is False
    detached = reg.detach(_PRED, reason="hop_detach")
    assert detached["registration_id"] == _PRED
    assert _PRED not in reg._load_active()


@pytest.mark.asyncio
async def test_stand_down_without_superseded_mints_resolved_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Omitting superseded_registration_id keeps the token on the resolved seat."""
    from claude_bundles import cdp_registry as reg
    from claude_bundles.cdp_registry.attachment_journal import has_standdown_token

    from cdp_ask.execution_store import ExecutionStore
    from cdp_ask.followup import execute_followup
    from cdp_ask.followup_envelope import FollowupCandidate
    from cdp_ask.models import FollowupProjectAskRequest

    _isolate_registry(monkeypatch, tmp_path)
    reg._store.write_active({_SUCC: {**_predecessor_row(), "registration_id": _SUCC}})
    page = MagicMock()
    page.url = _CHAT
    pw = MagicMock()
    pw.stop = AsyncMock()
    candidate = FollowupCandidate(
        registration_id=_SUCC,
        chat_url=_CHAT,
        holder="succ",
        purpose="operator-proxy",
        cdp_url="http://127.0.0.1:9334",
    )
    with (
        patch(
            "cdp_ask.followup.resolve_followup_prompt",
            return_value="TYPE: SEAT_STAND_DOWN",
        ),
        patch(
            "cdp_ask.followup.resolve_followup_target",
            AsyncMock(return_value=(candidate, None, "chat_url", "explicit")),
        ),
        patch(
            "cdp_ask.followup.find_page_on_lane",
            AsyncMock(return_value=(page, pw)),
        ),
        patch(
            "cdp_ask.followup.send_followup_paste_half",
            AsyncMock(
                return_value={
                    "receipt": "dom_committed",
                    "url": _CHAT,
                    "pasted_at": 1792224619,
                    "streaming_at_paste": True,
                }
            ),
        ),
        patch("cdp_ask.followup.emit_followup_event", lambda _event: None),
        patch("cdp_ask.followup._stamp_followup_in_flight", lambda *_a, **_k: None),
    ):
        result = await execute_followup(
            FollowupProjectAskRequest(
                chat_url=_CHAT,
                prompt_text="TYPE: SEAT_STAND_DOWN",
                envelope="stand_down",
                min_receipt="dom_committed",
                retain_lane=True,
            ),
            ExecutionStore(),
        )
    assert result.registration_id == _SUCC
    assert result.standdown_token_for is None
    assert result.superseded_count is None
    with reg._store.ports_lock():
        assert has_standdown_token(_SUCC) is True
        assert has_standdown_token(_PRED) is False
