"""Retry Stargate-backed dependency activation until watcher runtime can start."""

from __future__ import annotations

import asyncio
import logging

from services.rag.config import RagConfig
from services.rag.embeddings import (
    EmbeddingDependencyUnavailableError,
    wait_until_healthy,
)
from services.rag.events.lifecycle import (
    rag_dependencies_activated,
    rag_dependency_retry_scheduled,
    rag_embeddings_unavailable,
    rag_start_degraded,
)
from services.rag.model_availability_tracker import (
    ModelAvailabilityStartError,
    get_model_availability_tracker,
)

from . import state
from .background_tasks import track_background_task
from .extraction_runtime import start_extraction_runtime
from .lifecycle_constants import (
    DEPENDENCY_RETRY_BASE_S,
    DEPENDENCY_RETRY_MAX_S,
)
from .scope_freshness import _run_startup_scope_freshness_repair
from .watcher_runtime import _start_watcher_runtime

logger = logging.getLogger(__name__)


async def _activate_dependencies_when_ready(config: RagConfig) -> None:
    """Retry Stargate-backed dependency activation until watcher runtime can start."""
    tracker = get_model_availability_tracker()
    if tracker is None or state._event_bus is None:
        raise RuntimeError(
            "Dependency activation requires initialized tracker and event bus"
        )

    attempt = 0
    degraded_emitted = False
    deps_activated_emitted = False
    while True:
        attempt += 1
        state._dependency_activation.phase = "activating"
        state._dependency_activation.attempts = attempt
        state._dependency_activation.last_error = None
        waiting_on = "dependencies"
        error = ""
        try:
            state._dependency_activation.waiting_on = "stargate"
            await tracker.refresh_snapshot()
            tracker.start_subscription()

            state._dependency_activation.waiting_on = "embeddings"
            await wait_until_healthy()

            state._dependency_activation.waiting_on = "extraction_runtime"
            await start_extraction_runtime(config)

            if not deps_activated_emitted:
                await state._event_bus.publish(
                    rag_dependencies_activated(
                        dependencies=["stargate", "embeddings", "extraction_runtime"]
                    )
                )
                deps_activated_emitted = True

            if config.automatic_indexing_enabled and config.watch_directories:
                state._dependency_activation.waiting_on = "watcher_registration"
                await _start_watcher_runtime(config)
            else:
                logger.info(
                    "Watcher runtime not started "
                    "(automatic_indexing_enabled=%s, watch_directories=%d)",
                    config.automatic_indexing_enabled,
                    len(config.watch_directories),
                )
            state._dependency_activation.phase = "ready"
            state._dependency_activation.waiting_on = None
            # Scope freshness repair sends LLM requests to Stargate (vocabulary
            # classification). On cold restart the local model is still loading,
            # so these requests 504/timeout for several minutes. Fire it as a
            # background task now that the critical path (phase: ready) is done.
            track_background_task(
                asyncio.create_task(
                    _run_startup_scope_freshness_repair(config),
                    name="rag-startup-scope-freshness-repair",
                )
            )
            return
        except asyncio.CancelledError:
            raise
        except ModelAvailabilityStartError as exc:
            waiting_on = "stargate"
            error = str(exc)
        except EmbeddingDependencyUnavailableError as exc:
            waiting_on = "embeddings"
            error = str(exc)
            await state._event_bus.publish(rag_embeddings_unavailable(error=error))
        except TimeoutError as exc:
            waiting_on = state._dependency_activation.waiting_on or "dependencies"
            error = str(exc)
            if waiting_on == "embeddings":
                await state._event_bus.publish(rag_embeddings_unavailable(error=error))
        except Exception as exc:
            # Any unexpected exception from _start_watcher_runtime (e.g. RuntimeError
            # from a double-start on WatcherManager) must not escape the loop — that
            # would freeze the phase at "watcher_registration" with no recovery path.
            waiting_on = state._dependency_activation.waiting_on or "dependencies"
            error = str(exc)
            logger.error(
                "Unexpected error during dependency activation (attempt %d, phase %s): %s",
                attempt,
                state._dependency_activation.phase,
                error,
                exc_info=True,
            )

        delay = min(
            DEPENDENCY_RETRY_MAX_S,
            DEPENDENCY_RETRY_BASE_S ** min(attempt, 4),
        )
        state._dependency_activation.phase = "degraded"
        state._dependency_activation.waiting_on = waiting_on
        state._dependency_activation.last_error = error
        if not degraded_emitted:
            await state._event_bus.publish(
                rag_start_degraded(waiting_on=waiting_on, error=error)
            )
            degraded_emitted = True
        await state._event_bus.publish(
            rag_dependency_retry_scheduled(
                waiting_on=waiting_on,
                attempt=attempt,
                delay_seconds=delay,
                error=error,
            )
        )
        await asyncio.sleep(delay)
