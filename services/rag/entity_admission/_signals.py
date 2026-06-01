"""Signal application: react to cortex.entity.source.changed."""

from __future__ import annotations

from typing import TYPE_CHECKING

from universal_logging import get_logger

if TYPE_CHECKING:
    from .gate import EntityAdmissionGate

logger = get_logger(__name__)

_SOURCE_CHANGED = "cortex.entity.source.changed"


def _apply_signal(
    gate: EntityAdmissionGate, signal: str, payload: dict[str, object]
) -> None:
    """Mark the admitted set dirty when an entity's source_uri changes.

    The actual re-fetch is performed (debounced, full) by the dirty-refresh
    worker — keep this handler non-blocking and side-effect-light.
    """
    if signal != _SOURCE_CHANGED:
        return
    logger.debug(
        "EntityAdmissionGate: source.changed entity=%s change=%s — marking dirty",
        payload.get("entity_id"),
        payload.get("change"),
    )
    gate.mark_dirty()
