"""AC3/AC4: non-blocking fire and retry asymmetry."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from cdp_ask.client import CdpAskClientError

from services.git_integration_worker.trigger_service.fire import (
    fire_once,
    is_retryable_submit_error,
    reconcile_row,
)
from services.git_integration_worker.trigger_service.loop import run_reconcile_pass
from services.git_integration_worker.trigger_service.store import TriggerStore


@pytest.fixture
def store(tmp_path, monkeypatch: pytest.MonkeyPatch) -> TriggerStore:
    cortex_root = tmp_path / "cortex"
    prompt_path = cortex_root / "notes/system/threads/test-prompt.md"
    prompt_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_path.write_text("TYPE: DIRECTIVE\n", encoding="utf-8")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(cortex_root))
    return TriggerStore()


def _scheduled_row(store: TriggerStore):
    return store.schedule(
        created_by="test",
        fire_at=datetime.now(UTC) - timedelta(seconds=1),
        prompt_uri="cortex://notes/system/threads/test-prompt.md",
    )


def test_is_retryable_submit_error_classifies_lane_busy() -> None:
    exc = CdpAskClientError("lane busy", status_code=503, detail="lane is busy")
    assert is_retryable_submit_error(exc)


def test_submit_failure_retries_then_fails(store: TriggerStore) -> None:
    row = _scheduled_row(store)
    claimed = store.claim_due()
    assert claimed is not None
    client = MagicMock()
    client.submit.side_effect = CdpAskClientError("503", status_code=503)
    with patch(
        "services.git_integration_worker.trigger_service.fire.lane_available",
        return_value=(True, None),
    ):
        for attempt in range(1, 3):
            updated = fire_once(store, claimed, client=client)
            assert updated.status == "scheduled"
            assert updated.attempts == attempt
            claimed = store.claim_due()
            assert claimed is not None
        updated = fire_once(store, claimed, client=client)
    assert updated.status == "failed"
    assert updated.attempts == 3


def test_submit_success_never_retried_on_episode_failure(store: TriggerStore) -> None:
    row = _scheduled_row(store)
    claimed = store.claim_due()
    assert claimed is not None
    client = MagicMock()
    client._request.return_value = {"at_hard_limit": False, "free_slots": 1}
    client.submit.return_value = {"execution_id": "exec-abc", "status": "running"}
    with patch(
        "services.git_integration_worker.trigger_service.fire.lane_available",
        return_value=(True, None),
    ):
        fired = fire_once(store, claimed, client=client)
    assert fired.status == "fired"
    assert fired.execution_id == "exec-abc"
    client.poll.return_value = {
        "status": "failed",
        "error": "episode died",
        "completion_phase": "failed",
    }
    with patch(
        "services.git_integration_worker.trigger_service.fire.publish_lib_signal",
    ):
        reconciled = reconcile_row(store, fired, client=client)
    assert reconciled is not None
    assert reconciled.terminal_status == "failed"
    assert reconciled.status == "fired"
    assert client.submit.call_count == 1


@pytest.mark.asyncio
async def test_fire_loop_returns_before_reconcile_terminal(store: TriggerStore) -> None:
    """AC3: fire pass completes without awaiting poll-to-terminal."""
    row = _scheduled_row(store)
    claimed = store.claim_due()
    assert claimed is not None

    poll_started = time.monotonic()
    poll_block_s = 2.0

    def slow_poll(_execution_id: str) -> dict:
        time.sleep(poll_block_s)
        return {"status": "completed", "archive_uri": "cortex://x/archive.md"}

    client = MagicMock()
    client._request.return_value = {"at_hard_limit": False}
    client.submit.return_value = {"execution_id": "exec-fast", "status": "running"}
    client.poll.side_effect = slow_poll

    with patch(
        "services.git_integration_worker.trigger_service.fire.lane_available",
        return_value=(True, None),
    ), patch(
        "services.git_integration_worker.trigger_service.fire.publish_lib_signal",
    ), patch(
        "services.git_integration_worker.trigger_service.loop._pager_on_fire",
    ):
        fire_start = time.monotonic()
        fired = fire_once(store, claimed, client=client)
        fire_elapsed = time.monotonic() - fire_start

    assert fired.status == "fired"
    assert fire_elapsed < poll_block_s

    with patch(
        "services.git_integration_worker.trigger_service.fire.CdpAskClient",
        return_value=client,
    ), patch(
        "services.git_integration_worker.trigger_service.fire.publish_lib_signal",
    ):
        reconcile_start = time.monotonic()
        count = await run_reconcile_pass(store)
        reconcile_elapsed = time.monotonic() - reconcile_start

    assert count == 1
    assert reconcile_elapsed >= poll_block_s - 0.1
