"""AC4 settle experiment — bind→register_live_run window (observation, not doctrine).

Reproduces same-thread second request after ``bind_dispatch`` and before
``register_live_run``. Records whether the first ``auto-*`` can still obtain a
CancelRun handle afterward and whether the supersede emit named a stop.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from services.git_integration_worker.cursor_auto import supersede as auto_supersede
from services.git_integration_worker.cursor_auto.queue import AutoJobQueue
from services.git_integration_worker.cursor_auto.supersede import (
    supersede_same_thread_inflight,
)
from services.git_integration_worker.cursor_sdk_supersede import (
    live_run_for_thread,
    register_live_run,
    unregister_live_run,
)


def _enqueue(queue: AutoJobQueue, *, thread_id: str, turn_number: int):
    return queue.enqueue(
        thread_id=thread_id,
        turn_number=turn_number,
        subject=f"turn {turn_number}",
        body="## Scope\nAC4 settle\n",
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="auto",
        desired_effort="medium",
        contract="implement",
    )


def test_a2_second_request_in_bind_before_register_live_run_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Observation: displacement marks honestly; CancelRun handle still absent.

    After supersede in the pre-``register_live_run`` window:
    - method is ``pre_register_live_run`` (not queued_only / not run_cancel)
    - no ``run.cancel()`` occurs (nothing live to cancel)
    - the bound ``auto-*`` can still ``register_live_run`` afterward — i.e. the
      first job was not process-stopped by the second request.
    """
    auto_id = "auto-ac4settle01"
    thread_id = "ac4-scratch-pre-live"
    monkeypatch.setattr(
        auto_supersede,
        "_bound_auto_dispatch_id",
        lambda _job_id: auto_id,
    )
    emitted: list[dict] = []
    monkeypatch.setattr(
        auto_supersede,
        "emit_sdk_worker_cancelled",
        lambda **kwargs: emitted.append(kwargs),
    )

    queue = AutoJobQueue(durable=False)
    old = _enqueue(queue, thread_id=thread_id, turn_number=1)
    queue.claim_next()
    assert old.job_id

    new = _enqueue(queue, thread_id=thread_id, turn_number=2)
    evidence = asyncio.run(supersede_same_thread_inflight(new, queue=queue))
    auto_supersede._PENDING.clear()

    assert evidence is not None
    assert evidence["method"] == auto_supersede.PRE_REGISTER_LIVE_RUN
    assert evidence.get("dispatch_id") == auto_id
    assert queue.is_superseded(old.job_id)
    assert live_run_for_thread(thread_id) is None
    assert emitted and emitted[0]["method"] == "pre_register_live_run"
    assert emitted[0]["dispatch_id"] == auto_id
    # No CancelRun: signal_supersede was not entered (live was None).
    assert "run_cancel" not in {e.get("method") for e in emitted}

    # First auto-* can still publish a CancelRun handle — nothing stopped it.
    run = MagicMock()
    register_live_run(
        dispatch_id=auto_id,
        thread_id=thread_id,
        source_repo=str(tmp_path),
        run=run,
    )
    try:
        live = live_run_for_thread(thread_id)
        assert live is not None
        assert live.dispatch_id == auto_id
        run.cancel.assert_not_called()
    finally:
        unregister_live_run(dispatch_id=auto_id)
