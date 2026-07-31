import asyncio
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

_stargate_root = str(Path(__file__).resolve().parents[6])
if _stargate_root not in sys.path:
    sys.path.insert(0, _stargate_root)

from systems.pipeline.core.execution.map_reduce.map_executor.execution_modes import (  # noqa: E402
    MapExecutionModes,
)


class _FakeConcurrencyManager:
    def __init__(self) -> None:
        self.cancel_calls: list[set[asyncio.Task[Any]]] = []

    async def cancel_pending_iterations(
        self,
        pending: set[asyncio.Task[Any]],
        tasks: dict[asyncio.Task[Any], int],
        iteration_context: dict[int, dict[str, Any]],
    ) -> None:
        self.cancel_calls.append(pending)


@pytest.mark.asyncio
async def test_inference_timeout_cancels_federated_iteration() -> None:
    manager = _FakeConcurrencyManager()
    modes = MapExecutionModes(
        SimpleNamespace(name="extract_batch"),
        SimpleNamespace(),
        manager,
    )
    task = asyncio.create_task(asyncio.sleep(60))
    tasks = {task: 0}
    iteration_context = {
        0: {
            "inference_started_at": time.monotonic() - 1,
            "map_iteration_request_id": "cancel-group-1",
            "model_id": "qwen3-14b-q4-k-m-40960",
        }
    }
    monitor = asyncio.create_task(
        modes._inference_timeout_monitor(tasks, iteration_context, 0.01)
    )

    try:
        for _ in range(100):
            if manager.cancel_calls:
                break
            await asyncio.sleep(0.01)

        assert task.cancelled() or task.cancelling()
        assert manager.cancel_calls == [{task}]
    finally:
        monitor.cancel()
        task.cancel()
        await asyncio.gather(monitor, task, return_exceptions=True)
