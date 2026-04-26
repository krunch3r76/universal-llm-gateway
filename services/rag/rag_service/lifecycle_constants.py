"""Shared constants for RAG lifecycle, watcher startup, and dependency activation."""

from __future__ import annotations

DEPENDENCY_RETRY_BASE_S = 2.0
DEPENDENCY_RETRY_MAX_S = 30.0
RECONCILE_FILE_TIMEOUT_S = 300.0
POST_INDEX_STEPS = ("corpus_hints", "vocabulary")
STARTUP_SCOPE_REPAIR_RETRY_DELAYS_S = (15.0, 30.0, 60.0)

__all__ = [
    "DEPENDENCY_RETRY_BASE_S",
    "DEPENDENCY_RETRY_MAX_S",
    "POST_INDEX_STEPS",
    "RECONCILE_FILE_TIMEOUT_S",
    "STARTUP_SCOPE_REPAIR_RETRY_DELAYS_S",
]
