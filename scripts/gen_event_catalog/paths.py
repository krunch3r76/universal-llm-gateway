"""Path predicates for event-catalog sync gates."""

from __future__ import annotations

from collections.abc import Iterable

from .extract import WALK_ROOTS

_EVENT_FILE_ROOTS = tuple(f"{r}/" for r in WALK_ROOTS)


def is_event_source_path(path: str) -> bool:
    if not path.endswith(".py") or not path.startswith(_EVENT_FILE_ROOTS):
        return False
    parts = path.split("/")
    return "events" in parts or parts[-1].startswith("events")


def paths_touch_event_catalog(paths: Iterable[str]) -> bool:
    return any(
        p.startswith("docs/event-contracts") or is_event_source_path(p) for p in paths
    )
