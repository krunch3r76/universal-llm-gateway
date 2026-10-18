"""Domain events for divergent-ideation-v1 handlers."""

from __future__ import annotations

from universal_event_bus import Event, event_factory


@event_factory
def DivergentIdeationLeakHalted(*, leak_report: dict) -> Event:  # noqa: N802
    return Event(
        signal="divergent_ideation.leak_check.halted",
        payload={"leak_report": leak_report},
    )


@event_factory
def DivergentIdeationOperatorSplit(*, operator_dispersion: float, pool_size: int) -> Event:  # noqa: N802
    return Event(
        signal="divergent_ideation.operator_split.computed",
        payload={"operator_dispersion": operator_dispersion, "pool_size": pool_size},
    )


@event_factory
def DivergentIdeationAuditAssignments(*, pool_size: int, assignment_count: int) -> Event:  # noqa: N802
    return Event(
        signal="divergent_ideation.audit_assignments.built",
        payload={"pool_size": pool_size, "assignment_count": assignment_count},
    )


@event_factory
def DivergentIdeationRunCollated(  # noqa: N802
    *,
    convergence_rate: float,
    falsified: bool,
    halted_at: str | None,
) -> Event:
    return Event(
        signal="divergent_ideation.run.collated",
        payload={
            "convergence_rate": convergence_rate,
            "falsified": falsified,
            "halted_at": halted_at,
        },
    )
