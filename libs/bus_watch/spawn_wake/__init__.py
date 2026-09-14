"""Gear-3 spawn-on-wake package."""

from bus_watch.spawn_wake.fire import (
    _WORK_KEY_IN_FLIGHT,
    fire_spawn,
    tick_spawn_on_wake,
)
from bus_watch.spawn_wake.packet import (
    SUCCESSOR_MESSAGE_CAP,
    _wire_submit_body,
    build_dispatch_body,
    build_successor_message,
    successor_context_from_digest,
)
from bus_watch.spawn_wake.predicate import (
    evaluate_spawn_predicate,
    spawn_fingerprint,
)

__all__ = [
    "SUCCESSOR_MESSAGE_CAP",
    "_WORK_KEY_IN_FLIGHT",
    "_wire_submit_body",
    "build_dispatch_body",
    "build_successor_message",
    "evaluate_spawn_predicate",
    "fire_spawn",
    "spawn_fingerprint",
    "successor_context_from_digest",
    "tick_spawn_on_wake",
]
