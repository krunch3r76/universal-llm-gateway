"""Main asyncio loop for the decoupled extraction worker."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

import httpx

from services.rag.events.extraction_admission import rag_extraction_admission_timeout
from services.rag.events.extraction_queue import (
    rag_extraction_source_claimed,
    rag_extraction_source_completed,
)
from services.rag.extraction.capacity_envelope import is_capacity_class_envelope
from services.rag.extraction.chroma_source import extract_source
from services.rag.extraction.record_failure import record_source_failure
from services.rag.extraction_admission import ExtractionAdmissionGate
from services.rag.knowledge_extractor import (
    configure_timeouts,
    wait_until_extraction_ready,
)

if TYPE_CHECKING:
    from universal_event_bus import EventBus

    from services.rag.config import RagConfig
    from services.rag.property_index import PropertyIndex

logger = logging.getLogger(__name__)

_POLL_INTERVAL_S = 15.0
_IDLE_INTERVAL_S = 60.0
_ERROR_BACKOFF_S = 30.0
_MAX_QUEUE_ATTEMPTS = 5
_ADMISSION_WAIT_TIMEOUT_S: float = 60.0


async def _sleep_or_shutdown(
    event: asyncio.Event,
    seconds: float,
    *,
    wake: asyncio.Event | None = None,
) -> None:
    """Sleep for up to seconds, returning early on shutdown or wake signal."""
    if wake is None:
        try:
            await asyncio.wait_for(event.wait(), timeout=seconds)
        except TimeoutError:
            pass
        return
    shutdown_task = asyncio.create_task(event.wait())
    wake_task = asyncio.create_task(wake.wait())
    try:
        _, pending = await asyncio.wait(
            [shutdown_task, wake_task],
            timeout=seconds,
            return_when=asyncio.FIRST_COMPLETED,
        )
        for t in pending:
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass
    except asyncio.CancelledError:
        shutdown_task.cancel()
        wake_task.cancel()
        raise
    if wake.is_set():
        wake.clear()


async def run_extraction_worker(
    *,
    config: RagConfig,
    collection_fn: Any,
    property_index: PropertyIndex,
    event_bus: EventBus | None,
    shutdown_event: asyncio.Event,
    admission_gate: ExtractionAdmissionGate | None = None,
    wake_event: asyncio.Event | None = None,
) -> None:
    """Main extraction worker loop. Runs until shutdown_event is set.

    Waits for the extraction pipeline to register, then processes the
    extraction queue. When ``admission_gate`` is supplied, blocks at most
    ``_ADMISSION_WAIT_TIMEOUT_S`` before each dequeue; the gate is
    advisory and the loop proceeds on timeout (Phase 1's classification
    prevents budget bleed if the gate was closed for a real reason).
    """
    ke = config.knowledge_extraction
    configure_timeouts(ke)

    if not ke.pipeline:
        logger.info("Extraction worker: no pipeline configured, exiting")
        return

    try:
        await wait_until_extraction_ready(ke.pipeline)
    except TimeoutError:
        logger.error(
            "Extraction worker: pipeline '%s' not available, will retry in loop",
            ke.pipeline,
        )

    logger.info(
        "Extraction worker started (pipeline=%s, admission_gate=%s)",
        ke.pipeline,
        "enabled" if admission_gate is not None else "disabled",
    )

    while not shutdown_event.is_set():
        if admission_gate is not None and admission_gate.is_closed():
            logger.info(
                "Extraction worker: admission CLOSED (reasons=%s); waiting up to %.0fs",
                admission_gate.active_reasons(),
                _ADMISSION_WAIT_TIMEOUT_S,
            )
            wait_start = time.monotonic()
            opened = await admission_gate.wait_for_admission(_ADMISSION_WAIT_TIMEOUT_S)
            waited = time.monotonic() - wait_start
            if not opened:
                if event_bus is not None:
                    await event_bus.publish_nowait(
                        rag_extraction_admission_timeout(
                            pipeline_id=ke.pipeline,
                            waited_seconds=waited,
                            active_reasons=admission_gate.active_reasons(),
                        )
                    )
                if shutdown_event.is_set():
                    break

        try:
            claims = await property_index.dequeue_extraction(
                limit=1, max_attempts=_MAX_QUEUE_ATTEMPTS
            )
        except Exception:
            logger.error("Extraction worker: queue claim failed", exc_info=True)
            await _sleep_or_shutdown(shutdown_event, _ERROR_BACKOFF_S)
            continue

        if not claims:
            await _sleep_or_shutdown(shutdown_event, _IDLE_INTERVAL_S, wake=wake_event)
            continue

        claim = claims[0]
        source = claim.source
        if event_bus is not None:
            await event_bus.publish_nowait(
                rag_extraction_source_claimed(
                    source=claim.source,
                    attempts=claim.attempts,
                    queued_at=claim.queued_at,
                    claimed_at=claim.claimed_at,
                )
            )
        source_started = time.monotonic()
        try:
            collection = collection_fn()

            async def _store_execution_id(execution_id: str) -> None:
                await property_index.record_extraction_execution_id(
                    source, execution_id
                )

            try:
                (
                    all_done,
                    increment_attempt,
                    failure_category,
                    error,
                    error_type,
                ) = await extract_source(
                    source,
                    collection=collection,
                    config=ke,
                    rag_config=config,
                    property_index=property_index,
                    on_execution_id=_store_execution_id,
                )
            finally:
                await property_index.clear_extraction_execution_id(source)
            if all_done:
                await property_index.complete_extraction(source)
                if event_bus is not None:
                    await event_bus.publish_nowait(
                        rag_extraction_source_completed(
                            source=source,
                            duration_seconds=time.monotonic() - source_started,
                        )
                    )
            else:
                await record_source_failure(
                    property_index=property_index,
                    event_bus=event_bus,
                    source=source,
                    increment_attempt=increment_attempt,
                    failure_category=failure_category,
                    error=error,
                    error_type=error_type,
                )
                await _sleep_or_shutdown(
                    shutdown_event, _POLL_INTERVAL_S, wake=wake_event
                )

        except httpx.TimeoutException as exc:
            logger.warning(
                "Extraction worker: timeout for %s, will retry later "
                "(capacity-class; budget held)",
                source,
            )
            await record_source_failure(
                property_index=property_index,
                event_bus=event_bus,
                source=source,
                increment_attempt=False,
                failure_category="capacity",
                error=str(exc) or "timeout",
                error_type=type(exc).__qualname__,
            )
            await _sleep_or_shutdown(shutdown_event, _ERROR_BACKOFF_S, wake=wake_event)

        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status in (503, 429):
                logger.info(
                    "Extraction worker: %d for %s — model busy, backing off",
                    status,
                    source,
                )
                await record_source_failure(
                    property_index=property_index,
                    event_bus=event_bus,
                    source=source,
                    increment_attempt=False,
                    failure_category="capacity",
                    error=f"HTTP {status}",
                    error_type=type(exc).__qualname__,
                )
                await _sleep_or_shutdown(
                    shutdown_event, _ERROR_BACKOFF_S, wake=wake_event
                )
            else:
                capacity_class = is_capacity_class_envelope(exc)
                logger.warning(
                    "Extraction worker: HTTP %d for %s (capacity_class=%s)",
                    status,
                    source,
                    capacity_class,
                    exc_info=True,
                )
                await record_source_failure(
                    property_index=property_index,
                    event_bus=event_bus,
                    source=source,
                    increment_attempt=not capacity_class,
                    failure_category="capacity" if capacity_class else "http",
                    error=str(exc),
                    error_type=type(exc).__qualname__,
                )
                await _sleep_or_shutdown(
                    shutdown_event, _POLL_INTERVAL_S, wake=wake_event
                )

        except Exception as exc:
            logger.error(
                "Extraction worker: unexpected error for %s "
                "(treated as capacity-class; budget held)",
                source,
                exc_info=True,
            )
            await record_source_failure(
                property_index=property_index,
                event_bus=event_bus,
                source=source,
                increment_attempt=False,
                failure_category="unexpected",
                error=str(exc) or type(exc).__qualname__,
                error_type=type(exc).__qualname__,
            )
            await _sleep_or_shutdown(shutdown_event, _ERROR_BACKOFF_S, wake=wake_event)

    logger.info("Extraction worker shutting down")
