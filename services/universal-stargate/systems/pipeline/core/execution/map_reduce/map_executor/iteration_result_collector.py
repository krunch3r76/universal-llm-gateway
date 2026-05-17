"""Iteration result collector for map execution modes.

Converts the sets of done and cancelled asyncio tasks (after wait/timeout)
into structured IterationResult records and a dict of successful StepOutput
values. All truncation-error side effects (lazy import, diagnostic file
dumps under /tmp/pipeline-truncated) live here so the execution modes stay
focused on control flow.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

from ..iteration_state import IterationResult, IterationStatus

if TYPE_CHECKING:
    from ....schemas import StepConfig, StepOutput

from universal_logging import get_logger

logger = get_logger(__name__)


class IterationResultCollector:
    """Transforms raw task outcomes into IterationResult + successful outputs.

    The collector is stateful only through the StepConfig it receives at
    construction time (used for step-name-qualified log messages and for
    the truncation-dump filename). All collection is performed by the
    public collect_iteration_results method; the instance may be reused
    across calls for the same map step.
    """

    def __init__(self, step: StepConfig) -> None:
        """Capture the step whose name will appear in logs and dump files."""
        self._step = step

    def collect_iteration_results(
        self,
        done: set[asyncio.Task[Any]],
        cancelled: set[asyncio.Task[Any]],
        tasks: dict[asyncio.Task[Any], int],
        iteration_context: dict[int, dict[str, Any]],
        timeout_status: IterationStatus,
        timeout_duration: float | None,
    ) -> tuple[list[IterationResult], dict[int, StepOutput]]:
        """
        Collect IterationResult and output index from completed and cancelled tasks.

        cancelled tasks are assigned timeout_status (TIMEOUT or CANCELLED).
        Tasks that the inference timeout monitor cancelled appear inside the
        done set and receive the supplied timeout_status while producing the
        specific "cancelled by inference timeout monitor" warning.
        """
        iteration_results: list[IterationResult] = []
        results_by_index: dict[int, StepOutput] = {}

        for task in done:
            idx = tasks[task]
            ctx = iteration_context.get(idx, {})
            started_at = ctx.get("started_at")
            completion_time = ctx.get("completed_at", time.monotonic())
            duration = (completion_time - started_at) if started_at else None

            if task.cancelled():
                # Inference timeout monitor cancelled this task mid-flight
                iteration_results.append(
                    IterationResult(
                        index=idx,
                        status=timeout_status,
                        model_id=ctx.get("model_id"),
                        gateway_id=ctx.get("gateway_id"),
                        duration_seconds=duration,
                        started_at=started_at,
                    )
                )
                logger.warning(
                    "[%s] Iteration %d cancelled by inference timeout monitor",
                    self._step.name,
                    idx,
                )
            elif task.exception() is not None:
                exc = task.exception()
                from ....dag import ResponseTruncatedError

                truncated_response = None
                truncation_tokens = None
                if isinstance(exc, ResponseTruncatedError):
                    from pathlib import Path

                    truncation_tokens = exc.completion_tokens
                    dump_dir = Path("/tmp/pipeline-truncated")
                    dump_dir.mkdir(parents=True, exist_ok=True)
                    dump_file = dump_dir / (
                        f"{self._step.name}-iter{idx}-"
                        f"{int(time.monotonic() * 1000)}.txt"
                    )
                    try:
                        dump_file.write_text(exc.response_preview, encoding="utf-8")
                        truncated_response = str(dump_file)
                    except OSError:
                        truncated_response = exc.response_preview[:500]
                iteration_results.append(
                    IterationResult(
                        index=idx,
                        status=IterationStatus.FAILED,
                        model_id=ctx.get("model_id"),
                        gateway_id=ctx.get("gateway_id"),
                        duration_seconds=duration,
                        error_message=str(exc),
                        started_at=started_at,
                        truncated_response=truncated_response,
                        truncation_tokens=truncation_tokens,
                    )
                )
                logger.warning(
                    "[%s] Iteration %d failed: %s",
                    self._step.name,
                    idx,
                    task.exception(),
                )
            else:
                results_by_index[idx] = task.result()
                iteration_results.append(
                    IterationResult(
                        index=idx,
                        status=IterationStatus.COMPLETED,
                        model_id=ctx.get("model_id"),
                        gateway_id=ctx.get("gateway_id"),
                        duration_seconds=duration,
                        started_at=started_at,
                    )
                )

        for task in cancelled:
            idx = tasks[task]
            ctx = iteration_context.get(idx, {})
            iteration_results.append(
                IterationResult(
                    index=idx,
                    status=timeout_status,
                    model_id=ctx.get("model_id"),
                    gateway_id=ctx.get("gateway_id"),
                    duration_seconds=timeout_duration,
                    started_at=ctx.get("started_at"),
                )
            )

        return iteration_results, results_by_index
