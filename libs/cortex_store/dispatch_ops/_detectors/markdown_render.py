"""Markdown-render detectors — stubs awaiting Phase 4 render integration.

Three detectors live here today as placeholders returning ``[]`` so the
public taxonomy stays stable; ``marker_nesting_violation`` already has graph
hooks but the fs scan is deferred. When render-diff lands, these are the
first targets to grow real implementations.
"""

from __future__ import annotations

from typing import Any


def detect_marker_nesting_violation(
    conn, subject: str | None = None
) -> list[dict[str, Any]]:
    """Nested or malformed CORTEX_GENERATED markers in markdown (fs check).

    Graph-only in current design (markers recorded as edges/attributes on
    render). Placeholder until render integration lands in Phase 4.
    """
    return []


def detect_unregistered_document_in_markdown(
    conn, subject: str | None = None
) -> list[dict[str, Any]]:
    """Documents mentioned in markdown but not registered as document: entities.

    Full implementation would walk markdown files for entity links and
    cross-check. For MVP, returns ``[]`` (expand in Phase 4 with render
    integration).
    """
    return []


def detect_markdown_section_drift(
    conn, subject: str | None = None
) -> list[dict[str, Any]]:
    """CORTEX_GENERATED markers that have drifted from rendered content.

    Requires render comparison — stub until Phase 4 render_diff.
    """
    return []


__all__ = [
    "detect_markdown_section_drift",
    "detect_marker_nesting_violation",
    "detect_unregistered_document_in_markdown",
]
