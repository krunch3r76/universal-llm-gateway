"""Structural execution-mode predicate for concurrent admission opt-in.

Mission 9440 shipped the concurrent worker default-deny. This module is the
ONLY place a class is opted in. Claim paths must branch on
``is_concurrent_execution_mode(job.execution_mode)``, never re-derive an
equivalent check from ``job.contract``.

Lease-context for ``isolated_lane_conductor``: the Auto job does no checkout
work inside GIW — ``process_job`` admits, commissions one nested cursor-sdk
dispatch on the job's own lane-B worktree, polls it, and relays the closeout.
The write lease and operator-gate slot are taken by the nested dispatch, bounded
by ``isolated_write_ceiling()`` and the operator gate limit. Two jobs on
distinct ``thread_id`` share no checkout; same-``thread_id`` requests never
reach this class concurrently (thread-scoped supersede). Same-``work_key``
jobs serialize at ``claim_next_concurrent``. Nested implements under a running
conductor park/transfer the slot and do not consume a second one.

``implement`` and ``conductor`` join that predicate (arc agent-bus:12286).
Each matched job still gets its own lane-B worktree. The shared step is G7
land: ``master_land_guard`` (FIFO ``git_integrate`` slot plus the durable
master lease, timeout, waiter report names the holder). They do not merge
the hub checkout inside ``process_job``. ``conductor`` is not a canonical
bus contract — intake still rejects it — but a job that already carries the
token nests as ``handoff_contract=conductor`` instead of answering in-seat.
Lane A never matches. Overturning observation: one matched job writes the
hub index or HEAD before ``git_land``.

Lease-context answer for ``lease_free_propagate`` (9031-turn-80):
``contract:propagate`` is not ``nested_scope`` (``libs/contract_vocab/records.py``)
and ``process_job`` routes it to ``run_propagation_in_seat`` — manage
``sync_restart``, no ``ledger.admit`` write lease, no shared-checkout mutation
by the Auto job. Two concurrent propagates collide at manage's drain queue,
not on a worktree.
"""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_EXECUTION_MODE = "serial"
LEASE_FREE_PROPAGATE_MODE = "lease_free_propagate"
ISOLATED_LANE_CONDUCTOR_MODE = "isolated_lane_conductor"
_PROPAGATE_CONTRACT = "propagate"

LANE_CONDUCTOR_CONTRACTS = frozenset(
    {"investigate", "recon", "verify", "implement", "conductor"}
)

# Opt-in set. Do not add an entry without a cited lease-context answer.
_CONCURRENT_EXECUTION_MODES: frozenset[str] = frozenset(
    {LEASE_FREE_PROPAGATE_MODE, ISOLATED_LANE_CONDUCTOR_MODE}
)

ExecutionModeDeclareReason = (
    str  # predicate_met | predicate_unmet_requested_declined | propagate_map | default
)


@dataclass(frozen=True)
class ExecutionModeResolution:
    mode: str
    reason: ExecutionModeDeclareReason


def is_concurrent_execution_mode(execution_mode: str | None) -> bool:
    """True only for a class that has been explicitly opted in.

    Structural predicate: the input is the declared ``execution_mode``
    string, never ``contract``, never inferred from any other job field.
    """
    return bool(execution_mode) and execution_mode in _CONCURRENT_EXECUTION_MODES


def isolated_lane_conductor_predicate(
    *,
    lane: str | None,
    work_key: str | None,
    contract: str,
    continuity_hop: bool,
) -> bool:
    """Structural predicate for ``isolated_lane_conductor`` at enqueue."""
    if continuity_hop:
        return False
    if str(lane or "").strip().upper() != "B":
        return False
    if not (work_key and str(work_key).strip()):
        return False
    return str(contract or "").strip().lower() in LANE_CONDUCTOR_CONTRACTS


def resolve_execution_mode_at_enqueue(
    *,
    contract: str,
    requested: str | None = None,
    continuity_hop: bool = False,
    lane: str | None = None,
    work_key: str | None = None,
) -> ExecutionModeResolution:
    """Resolve declared mode and emit reason at enqueue. Claim paths must not call this."""
    if continuity_hop:
        return ExecutionModeResolution(DEFAULT_EXECUTION_MODE, "default")
    requested_mode = (requested or DEFAULT_EXECUTION_MODE).strip() or (
        DEFAULT_EXECUTION_MODE
    )
    if requested_mode == ISOLATED_LANE_CONDUCTOR_MODE:
        if isolated_lane_conductor_predicate(
            lane=lane,
            work_key=work_key,
            contract=contract,
            continuity_hop=continuity_hop,
        ):
            return ExecutionModeResolution(
                ISOLATED_LANE_CONDUCTOR_MODE, "predicate_met"
            )
        return ExecutionModeResolution(
            DEFAULT_EXECUTION_MODE, "predicate_unmet_requested_declined"
        )
    if requested_mode == LEASE_FREE_PROPAGATE_MODE:
        return ExecutionModeResolution(LEASE_FREE_PROPAGATE_MODE, "propagate_map")
    if str(contract or "").strip().lower() == _PROPAGATE_CONTRACT:
        return ExecutionModeResolution(LEASE_FREE_PROPAGATE_MODE, "propagate_map")
    if isolated_lane_conductor_predicate(
        lane=lane,
        work_key=work_key,
        contract=contract,
        continuity_hop=continuity_hop,
    ):
        return ExecutionModeResolution(ISOLATED_LANE_CONDUCTOR_MODE, "predicate_met")
    return ExecutionModeResolution(requested_mode, "default")


def declared_execution_mode(
    *,
    contract: str,
    requested: str | None = None,
    continuity_hop: bool = False,
    lane: str | None = None,
    work_key: str | None = None,
) -> str:
    """Resolve the declared mode at enqueue. Claim paths must not call this."""
    return resolve_execution_mode_at_enqueue(
        contract=contract,
        requested=requested,
        continuity_hop=continuity_hop,
        lane=lane,
        work_key=work_key,
    ).mode
