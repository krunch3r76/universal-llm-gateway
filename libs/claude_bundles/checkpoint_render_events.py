"""Events for checkpoint section-linking at hop render."""

from __future__ import annotations

import contextlib

from universal_event_bus.events.event import Event
from universal_event_bus.events.factory import event_factory


@event_factory
def CheckpointSectionsLinked(  # noqa: N802
    lane: str,
    linked_sections: tuple[str, ...],
    rendered_bytes: int,
    budget_b: int,
) -> Event:
    """Hop checkpoint shed whole sections to md_read link lines."""
    return Event(
        signal="mcp.agentbus.checkpoint_sections_linked",
        payload={
            "lane": lane,
            "linked_sections": list(linked_sections),
            "rendered_bytes": rendered_bytes,
            "budget_b": budget_b,
        },
        scope="node",
        role="observation",
    )


def publish_checkpoint_sections_linked(
    *,
    lane: str,
    linked_sections: frozenset[str],
    rendered_bytes: int,
    budget_b: int,
) -> None:
    """Best-effort publish when section linking occurs."""
    if not linked_sections:
        return
    from claude_bundles.hop_cadence_lease_events import _mirror_to_event_service

    event = CheckpointSectionsLinked(
        lane,
        tuple(sorted(linked_sections)),
        rendered_bytes,
        budget_b,
    )
    with contextlib.suppress(Exception):
        _mirror_to_event_service(event)


__all__ = [
    "CheckpointSectionsLinked",
    "publish_checkpoint_sections_linked",
]