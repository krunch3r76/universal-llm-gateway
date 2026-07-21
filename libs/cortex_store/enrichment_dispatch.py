"""Fire-and-forget dispatch of pipelines/assertion_enrichment/ on assertion writes.

Post-write enrichment (prospective summary + event extraction) runs as an
async, non-blocking call to the `assertion-enrichment` pipeline on Stargate.
Each step is idempotent (skips when its target column is already populated).

Disable via `CORTEX_ASSERTION_ENRICHMENT_DISABLED=1` (e.g. tests).
Kinds still honor `CORTEX_ENRICHMENT_ENABLED` when set (comma-separated:
prospective, events).
"""

from __future__ import annotations

import os
import threading

import httpx
from universal_logging import get_logger

logger = get_logger("cortex-api.enrichment_dispatch")

STARGATE_URL = os.environ.get("STARGATE_URL", "http://localhost:9999")
_REQUEST_TIMEOUT = 120.0
_PIPELINE_ID = "assertion-enrichment"

_ENRICHMENT_ENABLED: set[str] = set()
_raw = os.environ.get("CORTEX_ENRICHMENT_ENABLED", "")
if _raw.strip():
    _ENRICHMENT_ENABLED = {s.strip() for s in _raw.split(",") if s.strip()}


def _disabled() -> bool:
    return os.environ.get("CORTEX_ASSERTION_ENRICHMENT_DISABLED", "").strip() in {
        "1",
        "true",
        "yes",
    }


def _resolve_kinds(kinds: set[str] | None) -> list[str]:
    if kinds is not None:
        return sorted(kinds)
    if not _ENRICHMENT_ENABLED:
        return []
    return sorted(_ENRICHMENT_ENABLED)


def _post(
    assertion_id: int,
    claim: str,
    entity_id: str | None,
    confidence: str,
    kinds: set[str] | None,
) -> None:
    kind_list = _resolve_kinds(kinds)
    if not kind_list:
        return
    payload = {
        "model": _PIPELINE_ID,
        "messages": [{"role": "user", "content": "enrich"}],
        "pipeline_options": {
            "assertion_id": assertion_id,
            "claim": claim,
            "entity_id": entity_id,
            "confidence": confidence,
            "kinds": kind_list,
        },
    }
    try:
        with httpx.Client(timeout=_REQUEST_TIMEOUT) as client:
            resp = client.post(f"{STARGATE_URL}/v1/chat/completions", json=payload)
            resp.raise_for_status()
    except Exception:
        logger.warning(
            "assertion_enrichment dispatch failed for assertion %d",
            assertion_id,
            exc_info=True,
        )


def dispatch_assertion_enrichment_background(
    assertion_id: int,
    claim: str,
    entity_id: str | None,
    confidence: str,
    *,
    kinds: set[str] | None = None,
) -> None:
    """Fire-and-forget background dispatch — never blocks the write path."""
    if _disabled():
        return
    threading.Thread(
        target=_post,
        args=(assertion_id, claim, entity_id, confidence, kinds),
        daemon=True,
    ).start()
