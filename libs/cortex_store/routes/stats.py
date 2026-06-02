from __future__ import annotations

from fastapi import APIRouter
from universal_logging import get_logger

from ..compaction import POINTER_SQL_LIKE
from ..db import cortex_conn, query
from ..status_trait_read import entity_has_trait_columns

logger = get_logger("cortex-api.stats")
router = APIRouter(prefix="/stats", tags=["stats"])


def _count_by(conn: object, table: str, column: str) -> dict[str, int]:
    """Group-count rows in *table* by *column*, returning {value: count}."""
    rows = query(
        conn,  # type: ignore[arg-type]
        f"SELECT {column}, COUNT(*) as cnt FROM {table} GROUP BY {column}",
    )
    return {str(r[column] or "null"): r["cnt"] for r in rows}


@router.get("")
def get_stats() -> dict:
    """Dashboard counts across all Cortex tables."""
    with cortex_conn() as conn:
        e_total = query(conn, "SELECT COUNT(*) as cnt FROM entities")[0]["cnt"]
        a_total = query(conn, "SELECT COUNT(*) as cnt FROM assertions")[0]["cnt"]
        # todo:cortex-aggregate-compaction-filter — split assertion total into
        # active content vs compaction-pointer bookkeeping. The two figures
        # always sum to the prior single-count `total`, preserving callers
        # that key off it.
        a_pointers = query(
            conn,
            "SELECT COUNT(*) as cnt FROM assertions WHERE claim LIKE ?",
            (POINTER_SQL_LIKE,),
        )[0]["cnt"]
        r_total = query(conn, "SELECT COUNT(*) as cnt FROM relationships")[0]["cnt"]
        sf_total = query(conn, "SELECT COUNT(*) as cnt FROM surface_forms")[0]["cnt"]

        entity_stats: dict[str, object] = {
            "total": e_total,
            "by_type": _count_by(conn, "entities", "type"),
        }
        if entity_has_trait_columns(conn):
            entity_stats["by_lifecycle"] = _count_by(conn, "entities", "lifecycle")
            entity_stats["by_confidence_band"] = _count_by(
                conn, "entities", "confidence_band"
            )

        return {
            "entities": entity_stats,
            "assertions": {
                "total": a_total,
                "active_content": a_total - a_pointers,
                "compaction_pointers": a_pointers,
                "by_confidence": _count_by(conn, "assertions", "confidence"),
                "by_review_status": _count_by(conn, "assertions", "review_status"),
                "by_derivation_type": _count_by(conn, "assertions", "derivation_type"),
            },
            "relationships": {
                "total": r_total,
                "by_type": _count_by(conn, "relationships", "type"),
            },
            "surface_forms": {
                "total": sf_total,
                "by_mention_type": _count_by(conn, "surface_forms", "mention_type"),
            },
        }


def _get_stats_impl() -> dict:
    return get_stats()
