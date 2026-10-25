"""Runner account_id plumbing into tab pool (offline)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from claude_bundles.cdp_registry.tab_rows import eligible_host

from cdp_ask.models import SubmitProjectAskRequest
from cdp_ask.runner import _open_tab_lane

pytestmark = pytest.mark.offline


def test_open_tab_lane_passes_account_id_and_isolates_hosts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    active: dict[str, Any] = {
        "host-default": {
            "registration_id": "host-default",
            "status": "active",
            "purpose": "tab_host",
            "holder": "cdp-tab-pool",
            "port": 9225,
            "display": ":2",
            "account_id": "default",
        },
        "host-a2": {
            "registration_id": "host-a2",
            "status": "active",
            "purpose": "tab_host",
            "holder": "cdp-tab-pool",
            "port": 9226,
            "display": ":2",
            "account_id": "account-2",
        },
    }

    def listen(p: int) -> bool:
        return p in {9225, 9226}

    assert (
        eligible_host(active, [":2"], listen=listen, account_id=None)
        is active["host-default"]
    )
    assert (
        eligible_host(active, [":2"], listen=listen, account_id="account-2")
        is active["host-a2"]
    )

    open_calls: list[dict[str, Any]] = []

    def _fake_open_tab_row_for_ask(**kwargs):
        open_calls.append(kwargs)
        from claude_bundles.cdp_registry.models import Registration

        return Registration(
            registration_id="tab-1",
            port=9227,
            profile_suffix="account-2",
            profile=Path("/tmp/p"),
            cdp_url="http://127.0.0.1:9227",
            holder="holder-x",
            purpose="ask",
            account_id="account-2",
        )

    monkeypatch.setattr(
        "claude_bundles.cdp_registry.tab_rows.open_tab_row_for_ask",
        _fake_open_tab_row_for_ask,
    )

    req = SubmitProjectAskRequest(
        prompt_text="hi",
        account_id="account-2",
        purpose="ask",
    )
    _open_tab_lane(req, holder="holder-x")
    assert open_calls
    assert open_calls[-1]["account_id"] == "account-2"
