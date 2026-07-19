"""Read corpus hints and scope vocabulary from the metadata SQLite database."""

from __future__ import annotations

import contextlib
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING

from universal_logging import get_logger

from services.rag.corpus_hints.constants import DEFAULT_METADATA_DB_PATH
from services.rag.events.query import (
    rag_corpus_hints_load_failed,
    rag_scope_vocabulary_load_failed,
)

if TYPE_CHECKING:
    from universal_event_bus import EventBus

logger = get_logger(__name__)

__all__ = ["load_corpus_hints", "load_scope_vocabulary"]


def load_corpus_hints(
    db_path: Path | None = None, event_bus: EventBus | None = None
) -> dict[str, str]:
    """Read corpus hints ordered by scope ASC, score DESC, term ASC."""
    resolved = db_path or DEFAULT_METADATA_DB_PATH
    if not resolved.exists():
        return {}
    try:
        with contextlib.closing(
            sqlite3.connect(f"file:{resolved}?mode=ro", uri=True)
        ) as conn:
            rows = conn.execute(
                "SELECT scope, term FROM corpus_hints "
                "ORDER BY scope ASC, score DESC, term ASC"
            ).fetchall()
    except sqlite3.Error as e:
        logger.warning("Failed to load corpus hints from DB %s: %s", resolved, e)
        if event_bus is not None:
            event_bus.publish_from_sync(
                rag_corpus_hints_load_failed(path=str(resolved), error=str(e))
            )
        return {}
    except Exception as e:
        logger.error(
            "Unexpected error loading corpus hints from DB %s: %s",
            resolved,
            e,
            exc_info=True,
        )
        return {}
    result: dict[str, list[str]] = defaultdict(list)
    for scope, term in rows:
        if isinstance(scope, str) and isinstance(term, str) and term.strip():
            result[scope].append(term.strip())
    return {scope: ", ".join(terms) for scope, terms in result.items()}


def load_scope_vocabulary(
    db_path: Path | None = None, event_bus: EventBus | None = None
) -> dict[str, dict[str, list[str]]]:
    """Load register-structured vocabulary from the metadata database."""
    resolved = db_path or DEFAULT_METADATA_DB_PATH
    if not resolved.exists():
        return {}
    try:
        with contextlib.closing(
            sqlite3.connect(f"file:{resolved}?mode=ro", uri=True)
        ) as conn:
            rows = conn.execute(
                "SELECT scope, register, term FROM scope_vocabulary "
                "ORDER BY scope ASC, register ASC, term ASC"
            ).fetchall()
    except sqlite3.Error as e:
        logger.warning("Failed to load scope vocabulary from DB %s: %s", resolved, e)
        if event_bus is not None:
            event_bus.publish_from_sync(
                rag_scope_vocabulary_load_failed(path=str(resolved), error=str(e))
            )
        return {}
    result: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for scope, register, term in rows:
        if (
            isinstance(scope, str)
            and isinstance(register, str)
            and isinstance(term, str)
            and term.strip()
        ):
            result[scope][register].append(term.strip())
    return {scope: dict(registers) for scope, registers in result.items()}
