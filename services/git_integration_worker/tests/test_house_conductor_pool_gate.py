"""Conductor pool admit gate on continuity houses."""

from __future__ import annotations

from pathlib import Path

import pytest

from services.git_integration_worker.routes.cursor_sdk import _conductor_pool_refusal


@pytest.fixture
def house_card(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    card = tmp_path / "notes/system/threads/10223-continuity.md"
    card.parent.mkdir(parents=True)
    card.write_text(
        """<!-- pools v1 -->
## Pools
| pool | executor | status | must_load | must_read | closeout | forbidden |
|---|---|---|---|---|---|---|
| conductor | cursor-sdk | blocked · bridge Timeout TypeError · since 2026-09-08 | conductor | this card | worker CLOSEOUT | admit while status≠open |
""",
        encoding="utf-8",
    )


def test_conductor_pool_refusal_when_blocked(house_card: None) -> None:
    from services.git_integration_worker.models.cursor_api import CursorDispatchRequest

    req = CursorDispatchRequest(
        dispatch_id="d1",
        execution_id="e1",
        thread_id="10308",
        model="cursor/composer-2.5",
        continuity_root_thread_id="10223",
        handoff_contract="conductor",
        message="Use the conductor skill.",
    )
    reason = _conductor_pool_refusal(req, contract="conductor", packet_text="")
    assert reason is not None
    assert "pool_blocked" in reason
    assert "blocked · bridge Timeout TypeError" in reason


def test_conductor_pool_refusal_skips_non_conductor(house_card: None) -> None:
    from services.git_integration_worker.models.cursor_api import CursorDispatchRequest

    req = CursorDispatchRequest(
        dispatch_id="d1",
        execution_id="e1",
        thread_id="10308",
        model="cursor/composer-2.5",
        continuity_root_thread_id="10223",
        handoff_contract="implement",
        message="implement now",
    )
    assert _conductor_pool_refusal(req, contract="implement", packet_text="") is None
