"""Throwaway-session falsifier — URL record → close attachment → reopen → continuity.

Purpose-made session (no live operator tab). Continuity is the marker present
after reopen, not mere reachability of a blank page (arc 6885 AC3).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from claude_bundles import cdp_registry as reg

from cdp_ask.followup_reattach import ensure_cse_attached

pytestmark = pytest.mark.offline

THROW_URL = "https://claude.ai/cowork/cse_THROW6885falsifier001"
CONTINUITY_MARKER = "6885-falsifier-continuity-token"


@pytest.fixture
def isolated_registry(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
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
    monkeypatch.setattr(reg, "PORT_RANGE", range(9223, 9226))
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    monkeypatch.setattr(
        reg.cdp_lane,
        "profile_for",
        lambda suffix: profiles / f"claude-ai-chrome-profile-{suffix}",
    )
    return root


def _noop_launch(port: int, profile: Path) -> int:
    profile.mkdir(parents=True, exist_ok=True)
    return 1


@pytest.mark.asyncio
async def test_throwaway_session_url_close_reopen_continuity(
    isolated_registry: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Falsifier: birth URL → detach → goto(chat_url) → continuity marker.

    Simulates product CSE as a purpose-made page whose content carries a
    continuity token. Closing the attachment drops the open page; reopen is
    ``ensure_cse_attached`` navigating by recorded URL. Pass = marker survives.
    """
    birth = reg.register_lane(
        holder="falsifier-6885",
        purpose="throwaway",
        launch_chrome=_noop_launch,
        is_listening=lambda _p: False,
    )
    assert reg.bind_session_address(
        birth.registration_id,
        chat_url=THROW_URL,
        execution_id="exec-throw-6885",
    )
    assert reg.chat_url_for_registration(birth.registration_id) == THROW_URL

    # Close attachment: release the host while preserving the durable bookmark.
    recorded_url = reg.chat_url_for_registration(birth.registration_id)
    assert recorded_url == THROW_URL
    reg.deregister_lane(birth.registration_id, kill=True)
    active_after_close = reg._store.load_active()
    released = active_after_close.get(birth.registration_id)
    assert released is not None
    assert released.get("status") == "released"
    assert released.get("chat_url") == THROW_URL  # address survived detach
    assert reg.list_active() == []  # no open attachment

    # Fresh host + reopen from bookmark.
    reopen_host = reg.register_lane(
        holder="falsifier-6885-reopen",
        purpose="throwaway",
        launch_chrome=_noop_launch,
        is_listening=lambda _p: False,
    )
    page = MagicMock()
    page.url = THROW_URL
    page.content = AsyncMock(
        return_value=f"<html><body>{CONTINUITY_MARKER}</body></html>"
    )
    page.goto = AsyncMock()
    page.close = AsyncMock()
    pw = AsyncMock()
    pw.stop = AsyncMock()
    ctx = MagicMock()
    ctx.new_page = AsyncMock(return_value=page)

    async def _connect(_cdp_url: str) -> tuple[Any, Any, Any, Any]:
        return pw, MagicMock(), ctx, MagicMock()

    monkeypatch.setattr("cdp_ask.followup_reattach.connect_cdp", _connect)
    monkeypatch.setattr(
        "cdp_ask.followup_reattach.cdp_registry.list_active",
        lambda: [reopen_host],
    )
    monkeypatch.setattr(
        "cdp_ask.followup_reattach.cdp_registry.count_capacity_lanes",
        lambda: 1,
    )

    outcome = await ensure_cse_attached(
        THROW_URL, holder="falsifier-6885-reopen", purpose="throwaway"
    )
    assert outcome.ok is True, outcome.error
    assert outcome.page is not None
    page.goto.assert_awaited()
    goto_url = page.goto.await_args.args[0]
    assert goto_url == THROW_URL

    # Continuity — not mere reachability of an empty shell.
    body = await outcome.page.content()
    assert CONTINUITY_MARKER in body
    assert reg.chat_url_for_registration(reopen_host.registration_id) == THROW_URL

    # Open fork remains: product-side CSE TTL/expiry vs tab-close is NOT tested
    # by a same-turn reopen (see sidecar).
