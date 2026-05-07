"""Fire-and-forget dispatch of pipelines/predicate_extract/ on assertion writes.

v2.4 Slice 3 §6.7 peer projection: every successful assertion insert triggers
an async, non-blocking call to the `predicate-extract` pipeline (virtual
model id) on Stargate. The pipeline handler reads the assertion, calls the
T2 projector model, and writes `predicate_form` back via cortex-api.

Idempotency lives inside the pipeline (skips when `predicate_form` is
already populated). Failures here are best-effort and swallowed — the
assertion write must never be perturbed by enrichment-side issues.

Disable via `CORTEX_PREDICATE_EXTRACT_DISABLED=1` (e.g. tests).
"""

from __future__ import annotations

import logging
import os
import threading

import httpx

logger = logging.getLogger("cortex-api.predicate_extract")

STARGATE_URL = os.environ.get("STARGATE_URL", "http://localhost:9999")
_REQUEST_TIMEOUT = 90.0
_PIPELINE_ID = "predicate-extract"


def _disabled() -> bool:
    return os.environ.get("CORTEX_PREDICATE_EXTRACT_DISABLED", "").strip() in {
        "1",
        "true",
        "yes",
    }


def _post(assertion_id: int, claim: str, entity_id: str | None) -> None:
    payload = {
        "model": _PIPELINE_ID,
        "messages": [{"role": "user", "content": "extract"}],
        "pipeline_options": {
            "assertion_id": assertion_id,
            "claim": claim,
            "entity_id": entity_id,
        },
    }
    try:
        with httpx.Client(timeout=_REQUEST_TIMEOUT) as client:
            client.post(f"{STARGATE_URL}/v1/chat/completions", json=payload)
    except Exception:
        logger.warning(
            "predicate_extract dispatch failed for assertion %d",
            assertion_id,
            exc_info=True,
        )


def dispatch_predicate_extract_background(
    assertion_id: int, claim: str, entity_id: str | None
) -> None:
    """Fire-and-forget background dispatch — never blocks the write path."""
    if _disabled():
        return
    threading.Thread(
        target=_post, args=(assertion_id, claim, entity_id), daemon=True
    ).start()
