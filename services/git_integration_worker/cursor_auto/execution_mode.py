"""Structural execution-mode predicate for concurrent admission opt-in.

Mission 9440 shipped the concurrent worker default-deny. This module is the
ONLY place a class is opted in. Claim paths must branch on
``is_concurrent_execution_mode(job.execution_mode)``, never re-derive an
equivalent check from ``job.contract``.

Lease-context answer for ``lease_free_propagate`` (9031-turn-80):
``contract:propagate`` is not ``nested_scope`` (``libs/contract_vocab/records.py``)
and ``process_job`` routes it to ``run_propagation_in_seat`` — manage
``sync_restart``, no ``ledger.admit`` write lease, no shared-checkout mutation
by the Auto job. Two concurrent propagates collide at manage's drain queue,
not on a worktree. Nested-scope contracts stay serial.
"""

from __future__ import annotations

DEFAULT_EXECUTION_MODE = "serial"
LEASE_FREE_PROPAGATE_MODE = "lease_free_propagate"
_PROPAGATE_CONTRACT = "propagate"

# Opt-in set. Do not add an entry without a cited lease-context answer.
_CONCURRENT_EXECUTION_MODES: frozenset[str] = frozenset({LEASE_FREE_PROPAGATE_MODE})


def is_concurrent_execution_mode(execution_mode: str | None) -> bool:
    """True only for a class that has been explicitly opted in.

    Structural predicate: the input is the declared ``execution_mode``
    string, never ``contract``, never inferred from any other job field.
    """
    return bool(execution_mode) and execution_mode in _CONCURRENT_EXECUTION_MODES


def declared_execution_mode(
    *,
    contract: str,
    requested: str | None = None,
    continuity_hop: bool = False,
) -> str:
    """Resolve the declared mode at enqueue. Claim paths must not call this.

    Continuity hops keep serial — they already bypass via ``claim_job``.
    ``contract:propagate`` maps to ``lease_free_propagate`` unless the wire
    already declared a concurrent mode. Pydantic defaults ``execution_mode``
    to ``serial``, so a wire ``serial`` is treated as undeclared for this map.
    """
    if continuity_hop:
        return DEFAULT_EXECUTION_MODE
    requested_mode = (requested or DEFAULT_EXECUTION_MODE).strip() or (
        DEFAULT_EXECUTION_MODE
    )
    if is_concurrent_execution_mode(requested_mode):
        return requested_mode
    if str(contract or "").strip().lower() == _PROPAGATE_CONTRACT:
        return LEASE_FREE_PROPAGATE_MODE
    return requested_mode
