"""Shared primitives for the assertions route package.

Owns the router, projection-column constants, JSON-field set, the
session-tag regex, the payload-validation exception helper, the
background-embedding kickoff, the access-log writer, and the import-time
``_ASSERTION_COMPACT_COLS`` vs ``AssertionItem`` drift check.

Why a private submodule rather than top-level: every route module needs
``_ASSERTION_COLS`` + ``_JSON_FIELDS`` + ``router``; centralising them here
avoids per-route restatement and keeps each route file well under the
[quality:sloc] 400-line budget. Downstream callers continue to import these
names from ``cortex_store.routes.assertions`` (re-exports in ``__init__``).
"""

from __future__ import annotations

import logging
import re
import threading

from fastapi import APIRouter, HTTPException, status
from pydantic import ValidationError

from ... import embeddings as cortex_embeddings
from ... import vector_store
from ...db import WRITE_LOCK, cortex_conn
from ...models import AssertionItem
from ...projection_guard import assert_projection_covers_required

logger = logging.getLogger("cortex-api.assertions")

# §boot-compact: session-id pattern embedded in `evidence` that the briefing
# renderer extracts for the "Your Notes" prefix (e.g. "[web-2026-04-30-0528]").
# Surfaced as a dedicated `session_tag` field on the compact projection so
# `evidence` itself can be dropped on the wire without losing the prefix.
_SESSION_TAG_RE = re.compile(r"(cursor|web|api|bard)-\d{4}-\d{2}-\d{2}-\d{4}")


def _log_search_access(items: list) -> None:
    """Batch-log access for entities touched by search results (TTL reset for ephemeral)."""
    entity_ids = {
        getattr(item, "entity_id", None) or item.get("entity_id")
        for item in items
        if item
    }
    entity_ids.discard(None)
    if not entity_ids:
        return
    try:
        with WRITE_LOCK, cortex_conn() as conn:
            conn.executemany(
                "INSERT INTO entity_access_log "
                "(entity_id, agent, operation, source) VALUES (?, 'system', 'search', 'search')",
                [(eid,) for eid in entity_ids],
            )
            conn.commit()
    except Exception:
        logger.debug("Batch access log insert failed for search results")


def _embed_assertion_background(assertion_id: int, assertion_row: dict) -> None:
    """Compute and upsert assertion embedding in a daemon thread.

    Non-blocking: failures are logged and swallowed. The assertion remains
    valid and FTS-searchable even if embedding fails.
    """
    if not cortex_embeddings.is_configured() or not vector_store.is_initialized():
        return

    def _run() -> None:
        try:
            text = vector_store.assertion_embedding_text(assertion_row)
            embeddings = cortex_embeddings.embed_texts([text])
            if embeddings:
                meta: dict = {}
                if assertion_row.get("entity_id"):
                    meta["entity_id"] = assertion_row["entity_id"]
                if assertion_row.get("confidence"):
                    meta["confidence"] = assertion_row["confidence"]
                if assertion_row.get("derivation_type"):
                    meta["derivation_type"] = assertion_row["derivation_type"]
                if assertion_row.get("entrenchment_score") is not None:
                    meta["entrenchment_score"] = float(
                        assertion_row["entrenchment_score"]
                    )
                if assertion_row.get("observed_at"):
                    meta["observed_at"] = assertion_row["observed_at"]
                vector_store.upsert_assertion_embedding(
                    assertion_id=assertion_id,
                    text=text,
                    embedding=embeddings[0],
                    metadata=meta,
                )
        except Exception:
            logger.warning(
                "Background embedding failed for assertion %d",
                assertion_id,
                exc_info=True,
            )

    t = threading.Thread(target=_run, daemon=True)
    t.start()


router = APIRouter(prefix="/assertions", tags=["assertions"])

_JSON_FIELDS = frozenset({"evidence_uris"})

# §boot-compact: minimal column set for boot-path consumers that render only
# `id, claim, observed_at, entity_id, seeded_by, derivation_type` (turn-12
# contract on agent-bus thread 882). `confidence` is retained because it is
# required by AssertionItem and the renderer uses it for styling; enum cost
# is trivial (~20 B/row). All heavy fields are omitted at the SQL layer.
_ASSERTION_COMPACT_COLS = (
    "id, entity_id, claim, confidence, seeded_by, derivation_type, "
    "observed_at, created_at, evidence"
)

# Fail loud at module import (i.e. at `sync_restart cortex_api`) when the
# compact projection drifts out of alignment with `AssertionItem`'s required
# fields — the drift that produced the silent zero-items regression in
# agent-bus thread 882 turn 13.
assert_projection_covers_required(
    cols=_ASSERTION_COMPACT_COLS,
    model=AssertionItem,
    const_name="_ASSERTION_COMPACT_COLS",
    source_file=__file__,
)

_VALID_CONFIDENCE = {"confirmed", "believed", "suspected", "hypothesized"}

_ASSERTION_COLS = (
    "id, entity_id, claim, confidence, confidence_score, evidence, evidence_uris, seeded_by, "
    "derivation_type, chunk_id, reasoning_summary, is_atomic, is_decontextualized, "
    "observed_at, valid_from, valid_until, superseded_by, "
    "review_status, reviewer, reviewed_at, review_notes, "
    "resolution_status, fulfillment_assertion_id, quality_score, "
    "prospective_summary, events_json, artifact_uri, artifact_storage, "
    "entrenchment_score, predicate_form, created_at"
)

_VALID_REVIEW_STATUS = {"committed", "flagged", "staged", "rejected"}


def _payload_validation_exception(exc: ValidationError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail={
            "error": "assertion_payload_invalid",
            "diagnostics": exc.errors(
                include_url=False,
                include_context=False,
                include_input=False,
            ),
        },
    )


__all__ = [
    "_ASSERTION_COLS",
    "_ASSERTION_COMPACT_COLS",
    "_JSON_FIELDS",
    "_SESSION_TAG_RE",
    "_VALID_CONFIDENCE",
    "_VALID_REVIEW_STATUS",
    "_embed_assertion_background",
    "_log_search_access",
    "_payload_validation_exception",
    "logger",
    "router",
]
