"""Impresario lifecycle event factories (sole home for ``impresario.*`` signals)."""

from __future__ import annotations

from typing import Any

from universal_event_bus import Event, event_factory


@event_factory
def ImpresarioLeaseAcquired(  # noqa: N802
    work_key: str,
    lease_epoch: int,
    dispatch_id: str,
    surface: str | None = None,
) -> Event:
    return Event(
        signal="impresario.lease.acquired",
        payload={
            "work_key": work_key,
            "lease_epoch": lease_epoch,
            "dispatch_id": dispatch_id,
            "surface": surface,
        },
        scope="node",
    )


@event_factory
def ImpresarioLeaseTransferred(  # noqa: N802
    work_key: str,
    lease_epoch: int,
    from_dispatch_id: str,
    to_dispatch_id: str,
) -> Event:
    return Event(
        signal="impresario.lease.transferred",
        payload={
            "work_key": work_key,
            "lease_epoch": lease_epoch,
            "from_dispatch_id": from_dispatch_id,
            "to_dispatch_id": to_dispatch_id,
        },
    )


@event_factory
def ImpresarioLeaseExpired(  # noqa: N802
    work_key: str,
    lease_epoch: int,
    dispatch_id: str,
) -> Event:
    return Event(
        signal="impresario.lease.expired",
        payload={
            "work_key": work_key,
            "lease_epoch": lease_epoch,
            "dispatch_id": dispatch_id,
        },
    )


@event_factory
def ImpresarioAdmitRefused(  # noqa: N802
    work_key: str | None,
    code: str,
    message: str,
    dispatch_id: str | None = None,
) -> Event:
    return Event(
        signal="impresario.admit.refused",
        payload={
            "work_key": work_key,
            "code": code,
            "message": message,
            "dispatch_id": dispatch_id,
        },
    )


@event_factory
def ImpresarioCriticResolved(  # noqa: N802
    role: str,
    requested_model: str,
    resolved_model: str,
    transport: str,
    rerouted: bool = False,
) -> Event:
    return Event(
        signal="impresario.critic.resolved",
        payload={
            "role": role,
            "requested_model": requested_model,
            "resolved_model": resolved_model,
            "transport": transport,
            "rerouted": rerouted,
        },
    )


@event_factory
def ImpresarioWitnessSealed(  # noqa: N802
    work_key: str,
    row: str,
    witness_kind: str,
    detail_sha256: str | None = None,
) -> Event:
    return Event(
        signal="impresario.witness.sealed",
        payload={
            "work_key": work_key,
            "row": row,
            "witness_kind": witness_kind,
            "detail_sha256": detail_sha256,
        },
    )


@event_factory
def ImpresarioWitnessDegraded(  # noqa: N802
    work_key: str,
    row: str,
    reason: str,
) -> Event:
    return Event(
        signal="impresario.witness.degraded",
        payload={"work_key": work_key, "row": row, "reason": reason},
    )


@event_factory
def ImpresarioWitnessRetracted(  # noqa: N802
    work_key: str,
    row: str,
    reason: str = "witness_retract",
) -> Event:
    return Event(
        signal="impresario.witness.retracted",
        payload={"work_key": work_key, "row": row, "reason": reason},
    )


@event_factory
def ImpresarioPromotionAppended(  # noqa: N802
    work_key: str,
    next_row: str,
    lease_epoch: int,
) -> Event:
    return Event(
        signal="impresario.promotion.appended",
        payload={
            "work_key": work_key,
            "next_row": next_row,
            "lease_epoch": lease_epoch,
        },
    )


@event_factory
def ImpresarioPromotionFailed(  # noqa: N802
    work_key: str,
    reason: str,
) -> Event:
    return Event(
        signal="impresario.promotion.failed",
        payload={"work_key": work_key, "reason": reason},
    )


@event_factory
def ImpresarioTodoAutoclosed(  # noqa: N802
    work_key: str,
    entity_id: str,
) -> Event:
    return Event(
        signal="impresario.todo.autoclosed",
        payload={"work_key": work_key, "entity_id": entity_id},
    )


@event_factory
def ImpresarioSurfaceContention(  # noqa: N802
    work_key: str,
    holder_dispatch_id: str,
    surface: str,
) -> Event:
    return Event(
        signal="impresario.surface.contention",
        payload={
            "work_key": work_key,
            "holder_dispatch_id": holder_dispatch_id,
            "surface": surface,
        },
    )


@event_factory
def ImpresarioHopNoticed(  # noqa: N802
    house_root: str,
    hop_name: str,
    detail: dict[str, Any] | None = None,
) -> Event:
    return Event(
        signal="impresario.hop.noticed",
        payload={
            "house_root": house_root,
            "hop_name": hop_name,
            "detail": detail or {},
        },
    )
