"""Stand-down when reattach finds no host probes predecessor port before token."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from cdp_ask.execution_store import ExecutionStore
from cdp_ask.followup import _maybe_reattach
from cdp_ask.followup_reattach import ReattachOutcome
from cdp_ask.models import FollowupProjectAskRequest, FollowupProjectAskResponse

_CSE = "https://claude.ai/cowork/cse_test_reattach_no_host"
_PRED = "pred-reattach-no-host"
_PORT = 9233


def _isolated_registry(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
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


def _stand_down_req(**extra: object) -> FollowupProjectAskRequest:
    base = dict(
        registration_id=_PRED,
        chat_url=_CSE,
        prompt_text="TYPE: SEAT_STAND_DOWN",
        envelope="stand_down",
        reattach=True,
    )
    base.update(extra)
    return FollowupProjectAskRequest(**base)  # type: ignore[arg-type]


def _lane_err() -> FollowupProjectAskResponse:
    return FollowupProjectAskResponse(ok=False, error="lane_not_attached", url=_CSE)


def _no_host_outcome() -> ReattachOutcome:
    return ReattachOutcome(ok=False, error="reattach_no_host_available")


@pytest.mark.asyncio
async def test_stand_down_reattach_no_host_listening_down_writes_token(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from claude_bundles import cdp_registry as reg
    from claude_bundles.cdp_registry.attachment_journal import has_standdown_token

    _isolated_registry(monkeypatch, tmp_path)
    reg._store.write_active(
        {
            _PRED: {
                "registration_id": _PRED,
                "port": _PORT,
                "profile_suffix": "pred-no-host",
                "holder": "pred",
                "status": "dormant",
                "chat_url": _CSE,
            }
        }
    )
    monkeypatch.setattr(
        "claude_bundles.cdp_lane.is_listening", lambda port: port == _PORT and False
    )

    with (
        patch(
            "cdp_ask.followup.ensure_cse_attached",
            AsyncMock(return_value=_no_host_outcome()),
        ),
        patch("cdp_ask.followup.emit_followup_event", lambda _e: None),
    ):
        _outcome, resp = await _maybe_reattach(
            _stand_down_req(), ExecutionStore(), _lane_err()
        )

    assert resp is not None
    assert resp.detail == f"cdp_lane.is_listening({_PORT})=false"
    with reg._store.ports_lock():
        assert has_standdown_token(_PRED) is True


@pytest.mark.asyncio
async def test_stand_down_reattach_no_host_listening_up_writes_no_token(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from claude_bundles import cdp_registry as reg
    from claude_bundles.cdp_registry.attachment_journal import has_standdown_token

    _isolated_registry(monkeypatch, tmp_path)
    reg._store.write_active(
        {
            _PRED: {
                "registration_id": _PRED,
                "port": _PORT,
                "profile_suffix": "pred-no-host",
                "holder": "pred",
                "status": "dormant",
                "chat_url": _CSE,
            }
        }
    )
    monkeypatch.setattr(
        "claude_bundles.cdp_lane.is_listening", lambda port: port == _PORT
    )

    with (
        patch(
            "cdp_ask.followup.ensure_cse_attached",
            AsyncMock(return_value=_no_host_outcome()),
        ),
        patch("cdp_ask.followup.emit_followup_event", lambda _e: None),
    ):
        _outcome, resp = await _maybe_reattach(
            _stand_down_req(), ExecutionStore(), _lane_err()
        )

    assert resp is not None
    assert resp.detail == f"cdp_lane.is_listening({_PORT})=true"
    with reg._store.ports_lock():
        assert has_standdown_token(_PRED) is False


@pytest.mark.asyncio
async def test_stand_down_reattach_no_host_missing_row_probe_not_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from claude_bundles import cdp_registry as reg
    from claude_bundles.cdp_registry.attachment_journal import has_standdown_token

    _isolated_registry(monkeypatch, tmp_path)

    def _fail_probe(_port: int) -> bool:
        raise AssertionError("is_listening must not run without a registry row")

    monkeypatch.setattr("claude_bundles.cdp_lane.is_listening", _fail_probe)

    with (
        patch(
            "cdp_ask.followup.ensure_cse_attached",
            AsyncMock(return_value=_no_host_outcome()),
        ),
        patch("cdp_ask.followup.emit_followup_event", lambda _e: None),
    ):
        _outcome, resp = await _maybe_reattach(
            _stand_down_req(), ExecutionStore(), _lane_err()
        )

    assert resp is not None
    assert resp.detail == "cdp_lane.is_listening not_run"
    with reg._store.ports_lock():
        assert has_standdown_token(_PRED) is False
