"""Stand-down when dormant relaunch fails still appends standdown_unreachable token."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from cdp_ask.execution_store import ExecutionStore
from cdp_ask.followup import _maybe_reattach
from cdp_ask.followup_reattach import ReattachOutcome
from cdp_ask.models import FollowupProjectAskRequest, FollowupProjectAskResponse


@pytest.mark.asyncio
async def test_stand_down_dormant_relaunch_failed_has_standdown_token(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from claude_bundles import cdp_registry as reg
    from claude_bundles.cdp_registry.attachment_journal import has_standdown_token

    root = tmp_path / "cdp-registry"
    regs = root / "registrations"
    regs.mkdir(parents=True)
    monkeypatch.setenv("CDP_REGISTRY_SEAT_AUTHORITY", "1")
    monkeypatch.setattr(reg._store, "REGISTRY_DIR", root)
    monkeypatch.setattr(reg._store, "REGISTRY_LOG", root / "registry.jsonl")
    monkeypatch.setattr(reg._store, "ACTIVE_JSON", root / "active.json")
    monkeypatch.setattr(reg._store, "PORTS_LOCK", root / "ports.lock")
    monkeypatch.setattr(reg._store, "REGISTRATIONS_DIR", regs)

    pred = "pred-relaunch-fail"
    chat = "https://claude.ai/cowork/cse_test_relaunch_fail"
    reg._store.write_active(
        {
            pred: {
                "registration_id": pred,
                "port": 9222,
                "profile_suffix": "pred-fail",
                "holder": "pred",
                "status": "dormant",
                "chat_url": chat,
            }
        }
    )

    req = FollowupProjectAskRequest(
        registration_id=pred,
        chat_url=chat,
        prompt_text="TYPE: SEAT_STAND_DOWN",
        envelope="stand_down",
        reattach=True,
    )
    err = FollowupProjectAskResponse(
        ok=False,
        error="lane_not_attached",
        url=chat,
    )
    failed = ReattachOutcome(
        ok=False,
        error="dormant_relaunch_failed",
        registration_id=pred,
    )
    with (
        patch(
            "cdp_ask.followup.ensure_cse_attached",
            AsyncMock(return_value=failed),
        ),
        patch("cdp_ask.followup.emit_followup_event", lambda _e: None),
    ):
        await _maybe_reattach(req, ExecutionStore(), err)
    with reg._store.ports_lock():
        assert has_standdown_token(pred) is True
