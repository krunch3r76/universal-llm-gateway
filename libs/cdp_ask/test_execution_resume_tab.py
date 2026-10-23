"""Resume legs for kept STATUS_TAB rows (G-E)."""

from __future__ import annotations

import pytest
from claude_bundles.cdp_registry.models import STATUS_TAB

from cdp_ask.execution_resume import _RESUMABLE_ROW_STATUSES, _dispose_host

pytestmark = pytest.mark.offline


def test_resumable_includes_tab_status() -> None:
    assert STATUS_TAB in _RESUMABLE_ROW_STATUSES


def test_dispose_host_tab_row_normal_branch() -> None:
    from claude_bundles import cdp_registry as reg
    from unittest.mock import patch

    row = {
        "registration_id": "tab-r",
        "status": STATUS_TAB,
        "port": 9225,
        "profile_suffix": "reg-t",
        "holder": "h",
        "host_registration_id": "host-1",
        "target_id": "tgt",
    }
    with (
        patch("cdp_ask.execution_resume._row_for", return_value=row),
        patch(
            "claude_bundles.cdp_registry.tab_rows.finish_tab_on_exit"
        ) as finish,
    ):
        from cdp_ask.execution_store import ExecutionRecord

        rec = ExecutionRecord(
            execution_id="e1",
            status="running",
            created_at=1.0,
            updated_at=1.0,
            registration_id="tab-r",
            holder="h",
            purpose="ask",
        )
        _dispose_host(rec, teardown=False)
        finish.assert_called_once()
