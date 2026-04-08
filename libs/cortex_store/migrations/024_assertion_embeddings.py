"""Migration 024: Backfill assertion vector embeddings into ChromaDB.

Reads all non-superseded assertions from SQLite, batches embedding calls
(16 at a time) to the Gateway via cortex_store.embeddings, and upserts
into the cortex_assertions ChromaDB collection via cortex_store.vector_store.

The embedding model may not be loaded at migration time — the first call
may take 30-60s as the Gateway loads the model on-demand. Total expected
runtime: <2 minutes for ~1152 assertions.

Origin: Agent bus thread 456, Phase B1 of cortex-v3-kumiho-complete.md
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger("cortex-api.migration.024")

_BATCH_SIZE = 16


def _build_embedding_text(row: dict) -> str:
    """Build composite text for embedding (matches vector_store.assertion_embedding_text)."""
    parts = [row["claim"]]
    if row.get("prospective_summary"):
        parts.append(row["prospective_summary"])
    if row.get("events_json"):
        try:
            events = json.loads(row["events_json"])
            for ev in events:
                if ev.get("event"):
                    parts.append(ev["event"])
                if ev.get("consequence"):
                    parts.append(ev["consequence"])
        except (json.JSONDecodeError, TypeError):
            pass
    return " ".join(parts)


def _build_metadata(row: dict) -> dict:
    """Build ChromaDB metadata dict for an assertion."""
    meta: dict = {}
    if row.get("entity_id"):
        meta["entity_id"] = row["entity_id"]
    if row.get("confidence"):
        meta["confidence"] = row["confidence"]
    if row.get("derivation_type"):
        meta["derivation_type"] = row["derivation_type"]
    if row.get("entrenchment_score") is not None:
        meta["entrenchment_score"] = float(row["entrenchment_score"])
    if row.get("observed_at"):
        meta["observed_at"] = row["observed_at"]
    return meta


def migrate(conn: sqlite3.Connection) -> None:
    import os

    from cortex_store.embeddings import configure, embed_texts, is_configured
    from cortex_store.vector_store import (
        init_vector_store,
        is_initialized,
        upsert_assertion_embedding,
    )

    model_id = os.environ.get("CORTEX_EMBEDDING_MODEL", "qwen3-embedding-8b-q8-0-4096")

    if not is_configured():
        configure(model_id)

    db_path = os.environ.get(
        "CORTEX_DB_PATH", str(Path.home() / ".cortex" / "cortex.db")
    )
    db_dir = Path(db_path).parent

    if not is_initialized():
        init_vector_store(db_dir)

    rows = conn.execute(
        "SELECT id, entity_id, claim, confidence, derivation_type, "
        "entrenchment_score, observed_at, prospective_summary, events_json "
        "FROM assertions WHERE superseded_by IS NULL"
    ).fetchall()

    if not rows:
        logger.info("Migration 024: no assertions to embed")
        return

    row_dicts = [dict(r) for r in rows]
    total = len(row_dicts)
    logger.info("Migration 024: embedding %d non-superseded assertions", total)

    embedded = 0
    failed = 0

    for batch_start in range(0, total, _BATCH_SIZE):
        batch = row_dicts[batch_start : batch_start + _BATCH_SIZE]
        texts = [_build_embedding_text(r) for r in batch]

        try:
            embeddings = embed_texts(texts)
        except Exception:
            logger.error(
                "Migration 024: embedding batch at offset %d failed",
                batch_start,
                exc_info=True,
            )
            failed += len(batch)
            continue

        for i, row in enumerate(batch):
            try:
                meta = _build_metadata(row)
                upsert_assertion_embedding(
                    assertion_id=row["id"],
                    text=texts[i],
                    embedding=embeddings[i],
                    metadata=meta,
                )
                embedded += 1
            except Exception:
                logger.error(
                    "Migration 024: upsert failed for assertion %d",
                    row["id"],
                    exc_info=True,
                )
                failed += 1

        if (batch_start + _BATCH_SIZE) % 100 < _BATCH_SIZE:
            logger.info(
                "Migration 024 progress: %d/%d embedded, %d failed",
                embedded,
                total,
                failed,
            )

    logger.info(
        "Migration 024 (assertion_embeddings): backfilled %d assertions "
        "(%d failed, %d total)",
        embedded,
        failed,
        total,
    )
