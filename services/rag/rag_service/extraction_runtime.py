"""Watcher-independent extraction worker runtime."""

from __future__ import annotations

import asyncio
import logging

from services.rag.config import RagConfig
from services.rag.events.extraction_queue import rag_extraction_claim_recovered
from services.rag.extraction_admission import ExtractionAdmissionGate
from services.rag.extraction_worker import run_extraction_worker

from . import state
from .background_tasks import track_background_task

logger = logging.getLogger(__name__)


async def start_extraction_runtime(config: RagConfig) -> None:
    """Start extraction runtime after Stargate and embeddings are reachable."""
    if state._property_index is None or state._event_bus is None:
        raise RuntimeError("Extraction runtime requires property index and event bus")
    if state._extraction_shutdown is not None:
        logger.info("Extraction runtime already started")
        return

    recovered = await state._property_index.recover_abandoned_extraction_claims()
    for claim in recovered:
        await state._event_bus.publish_nowait(
            rag_extraction_claim_recovered(
                source=claim.source,
                claimed_at=claim.claimed_at,
                claimed_age_seconds=claim.claimed_age_seconds,
            )
        )

    state._extraction_shutdown = asyncio.Event()
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

    extraction_task = asyncio.create_task(
        run_extraction_worker(
            config=config,
            collection_fn=state._get_collection,
            property_index=state._property_index,
            event_bus=state._event_bus,
            shutdown_event=state._extraction_shutdown,
            admission_gate=state._extraction_admission_gate,
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
