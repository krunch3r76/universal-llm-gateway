"""Edge-op lifecycle event factories (``edge.op.*``, node-scoped).

Every authority transition in the edge-op store emits one of these, so the
state machine is readable from the Event Service without touching sqlite:
admitted, leased, started, completed, failed, expired, requeued, unknown,
released, plus ``edge.op.progress`` for the open-typed events the edge posts
between ``started`` and its result.

The events are advisory. The sqlite journal under ``$DATA_DIR/edge-ops.db`` is
the authority; a consumer that missed a publish re-reads the op record.
"""

from __future__ import annotations

from universal_event_bus import Event, event_factory


@event_factory
def EdgeOpAdmitted(  # noqa: N802
    op_id: str,
    kind: str,
    op: str,
    redelivery: str,
    deadline_epoch: float,
) -> Event:
    """Emitted when an op row is first written in state ``queued``."""
    return Event(
        signal="edge.op.admitted",
        payload={
            "op_id": op_id,
            "kind": kind,
            "op": op,
            "redelivery": redelivery,
            "deadline_epoch": deadline_epoch,
        },
        role="coordination",
        scope="node",
    )


@event_factory
def EdgeOpLeased(  # noqa: N802
    op_id: str,
    kind: str,
    edge_id: str,
    lease_id: str,
    lease_expires_epoch: float,
) -> Event:
    """Emitted when an edge claims a queued op."""
    return Event(
        signal="edge.op.leased",
        payload={
            "op_id": op_id,
            "kind": kind,
            "edge_id": edge_id,
            "lease_id": lease_id,
            "lease_expires_epoch": lease_expires_epoch,
        },
        role="coordination",
        scope="node",
    )


@event_factory
def EdgeOpStarted(  # noqa: N802
    op_id: str,
    kind: str,
    edge_id: str,
) -> Event:
    """Emitted on the 2xx ``started`` ack a ``never`` handler waits for."""
    return Event(
        signal="edge.op.started",
        payload={"op_id": op_id, "kind": kind, "edge_id": edge_id},
        role="coordination",
        scope="node",
    )


@event_factory
def EdgeOpStartRefused(  # noqa: N802
    op_id: str,
    kind: str,
    edge_id: str,
    refusal_code: str,
) -> Event:
    """Emitted when a ``started`` ack is refused (terminal or stale lease)."""
    return Event(
        signal="edge.op.start_refused",
        payload={
            "op_id": op_id,
            "kind": kind,
            "edge_id": edge_id,
            "refusal_code": refusal_code,
        },
        role="coordination",
        scope="node",
    )


@event_factory
def EdgeOpProgress(  # noqa: N802
    op_id: str,
    kind: str,
    event_type: str,
    seq: int,
) -> Event:
    """Emitted for each open-typed progress event the edge posts."""
    return Event(
        signal="edge.op.progress",
        payload={"op_id": op_id, "kind": kind, "type": event_type, "seq": seq},
        scope="node",
    )


@event_factory
def EdgeOpCompleted(  # noqa: N802
    op_id: str,
    kind: str,
    edge_id: str | None,
) -> Event:
    """Emitted when a handler result is stored. ``ok:false`` lands here too."""
    return Event(
        signal="edge.op.completed",
        payload={"op_id": op_id, "kind": kind, "edge_id": edge_id},
        role="coordination",
        scope="node",
    )


@event_factory
def EdgeOpFailed(  # noqa: N802
    op_id: str,
    kind: str,
    edge_id: str | None,
) -> Event:
    """Emitted when an op ends on an exception or infrastructure error."""
    return Event(
        signal="edge.op.failed",
        payload={"op_id": op_id, "kind": kind, "edge_id": edge_id},
        role="coordination",
        scope="node",
    )


@event_factory
def EdgeOpExpired(  # noqa: N802
    op_id: str,
    kind: str,
    reason: str,
) -> Event:
    """Emitted when an op still queued at its deadline is folded ``expired``."""
    return Event(
        signal="edge.op.expired",
        payload={"op_id": op_id, "kind": kind, "reason": reason},
        role="coordination",
        scope="node",
    )


@event_factory
def EdgeOpRequeued(  # noqa: N802
    op_id: str,
    kind: str,
    reason: str,
) -> Event:
    """Emitted when a lease lost before ``started`` returns an op to the queue."""
    return Event(
        signal="edge.op.requeued",
        payload={"op_id": op_id, "kind": kind, "reason": reason},
        role="coordination",
        scope="node",
    )


@event_factory
def EdgeOpUnknown(  # noqa: N802
    op_id: str,
    kind: str,
    reason: str,
) -> Event:
    """Emitted when a lease lost after ``started`` ends an op unresolved."""
    return Event(
        signal="edge.op.unknown",
        payload={"op_id": op_id, "kind": kind, "reason": reason},
        role="coordination",
        scope="node",
    )


@event_factory
def EdgeOpReleased(  # noqa: N802
    op_id: str,
    kind: str,
    edge_id: str,
    state: str,
) -> Event:
    """Emitted when an edge releases its lease without posting a result."""
    return Event(
        signal="edge.op.released",
        payload={"op_id": op_id, "kind": kind, "edge_id": edge_id, "state": state},
        role="coordination",
        scope="node",
    )


@event_factory
def EdgeLeasePolled(  # noqa: N802
    edge_id: str,
    kinds: list[str],
) -> Event:
    """Emitted when an edge polls for leases; the presence ``admit`` reads.

    An edge that keeps polling and finding nothing is visible here, which is
    how an ``edge_unavailable`` refusal is told apart from an idle queue.
    """
    return Event(
        signal="edge.lease.polled",
        payload={"edge_id": edge_id, "kinds": kinds},
        scope="node",
    )
