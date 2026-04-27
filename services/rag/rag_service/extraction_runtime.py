"""Watcher-independent extraction worker runtime."""

from __future__ import annotations

import asyncio
import json
import logging
import os

import aiohttp

from services.rag.config import RagConfig
from services.rag.events.extraction_queue import (
    rag_extraction_claim_recovered,
    rag_extraction_queue_woken,
)
from services.rag.extraction_admission import ExtractionAdmissionGate
from services.rag.extraction_worker import run_extraction_worker
from services.rag.knowledge_extractor import cancel_extraction_execution

from . import state
from .background_tasks import track_background_task

logger = logging.getLogger(__name__)

_EVENT_QUERY_SOCK = os.environ.get(
    "EVENTS_QUERY_SOCK", "/tmp/universal-protocol/events-query.sock"
)
_SUBSCRIBE_PATH = "http://localhost/v1/subscribe"
_RECONNECT_DELAY_S = 5.0


async def _watch_extraction_model(
    pipeline_id: str,
    wake_event: asyncio.Event,
    shutdown_event: asyncio.Event,
) -> None:
    """Subscribe to model.available for pipeline_id; reset cooling-off items on match.

    When the extraction pipeline transitions to available (e.g. Jupiter reconnects
    and rag-extraction becomes routable), cooling-off items that failed due to model
    unavailability are reset to immediately eligible and the worker's idle sleep is
    interrupted so extraction resumes without waiting for the backoff window.

    Reconnects automatically on WebSocket failure. Resumes from last seen seq
    to avoid missing events during brief disconnects.
    """
    last_seq: int | None = None
    while not shutdown_event.is_set():
        connector = aiohttp.UnixConnector(path=_EVENT_QUERY_SOCK)
        try:
            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.ws_connect(_SUBSCRIBE_PATH) as ws:
                    sub_msg: dict[str, object] = {
                        "type": "subscribe",
                        "filter": {"signal": "model.available"},
                    }
                    if last_seq is not None:
                        sub_msg["resume_from"] = {"seq": last_seq}
                    await ws.send_json(sub_msg)
                    logger.info(
                        "Extraction model watcher subscribed (pipeline=%s, resumed_from=%s)",
                        pipeline_id,
                        last_seq,
                    )
                    async for raw in ws:
                        if shutdown_event.is_set():
                            return
                        if raw.type != aiohttp.WSMsgType.TEXT:
                            continue
                        try:
                            event = json.loads(raw.data)
                        except json.JSONDecodeError:
                            continue
                        if not isinstance(event, dict):
                            continue
                        seq = event.get("seq")
                        if isinstance(seq, int):
                            last_seq = seq
                        if event.get("signal") != "model.available":
                            continue
                        payload = event.get("payload")
                        if not isinstance(payload, dict):
                            continue
                        if payload.get("model_id") != pipeline_id:
                            continue
                        if state._property_index is not None and state._event_bus is not None:
                            reset = await state._property_index.wake_extraction_queue()
                            if reset:
                                logger.info(
                                    "Extraction pipeline %s available — woke %d cooling-off items",
                                    pipeline_id,
                                    reset,
                                )
                                await state._event_bus.publish_nowait(
                                    rag_extraction_queue_woken(
                                        pipeline_id=pipeline_id,
                                        reset_count=reset,
                                    )
                                )
                        wake_event.set()
        except asyncio.CancelledError:
            return
        except Exception as exc:
            logger.warning(
                "Extraction model watcher (%s): reconnecting in %.0fs: %s",
                pipeline_id,
                _RECONNECT_DELAY_S,
                exc,
            )
            await asyncio.sleep(_RECONNECT_DELAY_S)


async def start_extraction_runtime(config: RagConfig) -> None:
    """Start extraction runtime after Stargate and embeddings are reachable."""
    if state._property_index is None or state._event_bus is None:
        raise RuntimeError("Extraction runtime requires property index and event bus")
    if state._extraction_shutdown is not None:
        logger.info("Extraction runtime already started")
        return

    recovered = await state._property_index.recover_abandoned_extraction_claims()
    for claim in recovered:
        if claim.active_execution_id:
            await cancel_extraction_execution(claim.active_execution_id)
        await state._event_bus.publish_nowait(
            rag_extraction_claim_recovered(
                source=claim.source,
                claimed_at=claim.claimed_at,
                claimed_age_seconds=claim.claimed_age_seconds,
            )
        )

    state._extraction_shutdown = asyncio.Event()
    wake_event: asyncio.Event | None = None
    extraction_pipeline_id = config.knowledge_extraction.pipeline
    if extraction_pipeline_id:
        state._extraction_admission_gate = ExtractionAdmissionGate(
            pipeline_id=extraction_pipeline_id,
            event_bus=state._event_bus,
        )
        state._extraction_admission_gate.start()
        logger.info(
            "ExtractionAdmissionGate started (pipeline=%s)",
            extraction_pipeline_id,
        )
        wake_event = asyncio.Event()
        watch_task = asyncio.create_task(
            _watch_extraction_model(
                pipeline_id=extraction_pipeline_id,
                wake_event=wake_event,
                shutdown_event=state._extraction_shutdown,
            ),
            name=f"rag-extraction-model-watcher-{extraction_pipeline_id}",
        )
        track_background_task(watch_task)

    extraction_task = asyncio.create_task(
        run_extraction_worker(
            config=config,
            collection_fn=state._get_collection,
            property_index=state._property_index,
            event_bus=state._event_bus,
            shutdown_event=state._extraction_shutdown,
            admission_gate=state._extraction_admission_gate,
            wake_event=wake_event,
        ),
        name="rag-extraction-worker",
    )
    track_background_task(extraction_task)


async def stop_extraction_runtime() -> None:
    """Stop extraction-specific coordination resources."""
    if state._extraction_shutdown is not None:
        state._extraction_shutdown.set()
        state._extraction_shutdown = None
    if state._extraction_admission_gate is not None:
        await state._extraction_admission_gate.stop()
        state._extraction_admission_gate = None
