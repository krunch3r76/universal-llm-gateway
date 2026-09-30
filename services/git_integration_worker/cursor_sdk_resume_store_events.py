"""Observation events for resume SDK-store owner resolution.

``cursor_sdk_store_locus`` publishes here when a store path is located.
This module must not import the locus module: the edge stays one-directional
so owner selection and the event vocabulary cannot cycle. A missing store
does not emit — there is no path to resolve.
"""

from __future__ import annotations

from universal_event_bus import Event, event_factory

from services.git_integration_worker.cursor_sdk_events import emit_frontier_event


@event_factory
def ResumeStoreOwnerResolved(  # noqa: N802
    parent_id: str,
    owner_dispatch_id: str,
    store_path: str,
    mode: str,
) -> Event:
    """Observation that a located SDK store resolved to a lineage HOME owner.

    ``mode`` is ``home_contained`` when a non-empty ``state_root`` named a
    store inside a lineage HOME, ``rescanned`` when the path came from a HOME
    directory scan, or ``external`` when the store lies outside every lineage
    HOME. ``owner_dispatch_id`` is ``parent_id`` in the external case.
    """
    return Event(
        signal="giw.resume.store.owner.resolved",
        payload={
            "parent_id": parent_id,
            "owner_dispatch_id": owner_dispatch_id,
            "store_path": store_path,
            "mode": mode,
        },
        role="observation",
        scope="node",
    )


def emit_resume_store_owner_resolved(
    *,
    parent_id: str,
    owner_dispatch_id: str,
    store_path: str,
    mode: str,
) -> None:
    """Publish ``giw.resume.store.owner.resolved`` through the frontier publisher.

    Called once per resolution that names a store. Does not log; the event is
    the observable for external stores that still return ``parent_id``.
    """
    emit_frontier_event(
        ResumeStoreOwnerResolved(
            parent_id=parent_id,
            owner_dispatch_id=owner_dispatch_id,
            store_path=store_path,
            mode=mode,
        )
    )
