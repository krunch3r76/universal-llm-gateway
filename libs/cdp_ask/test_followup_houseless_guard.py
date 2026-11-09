"""Houseless registry guard on cdp_ask followup (no operator reattach)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

pytestmark = pytest.mark.offline


@pytest.mark.asyncio
async def test_houseless_operator_proxy_refuses_reattach_and_mint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cdp_ask.execution_store import ExecutionStore
    from cdp_ask.followup import execute_followup
    from cdp_ask.models import FollowupProjectAskRequest

    reg = "583b411df34247f9a48bc044b6518be3"
    chat = "https://claude.ai/cowork/cse_houseless_closed"
    monkeypatch.setattr(
        "claude_bundles.cdp_registry_store.load_active",
        lambda: {
            reg: {
                "registration_id": reg,
                "purpose": "houseless",
                "status": "active",
            }
        },
    )

    mint_calls: list[dict] = []
    monkeypatch.setattr(
        "claude_bundles.cdp_registry.register_lane",
        lambda *a, **k: mint_calls.append({"k": k}) or "lane",
    )
    attach_calls: list[dict] = []

    async def _spy_attach(*_a, **kwargs):  # type: ignore[no-untyped-def]
        attach_calls.append(dict(kwargs))
        raise AssertionError("ensure_cse_attached must not run for houseless")

    monkeypatch.setattr("cdp_ask.followup.ensure_cse_attached", _spy_attach)
    monkeypatch.setattr(
        "cdp_ask.followup_resolve.scan_lane_cse_urls",
        AsyncMock(return_value=[]),
    )
    resp = await execute_followup(
        FollowupProjectAskRequest(
            prompt_text="wake turns 2",
            parent_thread="22760",
            chat_url=chat,
            registration_id=reg,
            purpose="operator-proxy",
            reattach=True,
        ),
        ExecutionStore(),
    )
    assert resp.ok is False
    assert resp.error == "houseless_no_attach"
    assert not mint_calls
    assert not attach_calls
