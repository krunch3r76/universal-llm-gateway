"""Edge-op vocabulary: states, redelivery classes, refusal codes, record shapes.

One authority for the words the store, the service functions, the HTTP routes
and the edge all use. ``redelivery`` rides on the lease grant because the edge
reads it to decide whether a handler may run before Stargate has acknowledged
its ``started`` event; the lease-loss fold itself is uniform across kinds
(before started re-leases, after started ends ``unknown``). Stargate holds the
other half of that contract: a ``never`` op's result is refused with
``edge_not_started`` until its ``started`` event has landed, because without
that ack the fold would have re-leased work the handler had already done. A
``safe`` op may result straight from ``leased`` — a second delivery of a safe
kind is harmless, so its handler never has to wait for the ack. The ack is
refusable in its own right: a ``started`` event for an op that already reached
a terminal state is refused with ``edge_op_terminal``, so an edge that stalled
past the deadline learns the op closed instead of being told to run it late.

Terminal status is separate from the handler result. A handler that answers
``{"ok": false, ...}`` still ends ``completed`` — ``failed`` is reserved for an
exception or an infrastructure error, and the substrate never retries an op
that already reached a terminal state.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

OpState = Literal[
    "queued",
    "leased",
    "started",
    "completed",
    "failed",
    "expired",
    "unknown",
]

TERMINAL_STATES: frozenset[str] = frozenset(
    {"completed", "failed", "expired", "unknown"}
)

Redelivery = Literal["safe", "never"]

DEFAULT_REDELIVERY: Redelivery = "never"

# Deny-by-default redelivery. ``ping`` opens no CDP connection and has no
# side effect outside its own reply, so a second delivery is harmless.
KIND_REDELIVERY: dict[str, Redelivery] = {"ping": "safe"}

Outcome = Literal["completed", "failed"]

# Refusal codes. ``edge_unavailable`` and ``edge_pending`` are the two the
# plan's binding 3 names by wire value; the rest are local to this substrate.
CODE_DISABLED = "edge_ops_disabled"
CODE_EDGE_UNAVAILABLE = "edge_unavailable"
CODE_EDGE_PENDING = "edge_pending"
CODE_NOT_FOUND = "edge_op_not_found"
CODE_FAILED = "edge_op_failed"
CODE_EXPIRED = "edge_op_expired"
CODE_UNRESOLVED = "edge_op_unknown"
CODE_LEASE_MISMATCH = "edge_lease_mismatch"  # edge holds no lease on this op
CODE_LEASE_STALE = "edge_lease_stale"  # ``lease_id`` no longer matches authority
CODE_NOT_STARTED = "edge_not_started"
CODE_TERMINAL = "edge_op_terminal"
CODE_IDENTITY_REFUSED = "edge_identity_refused"

# Reasons carried on fold transitions and on the matching lifecycle events.
REASON_DEADLINE = "deadline_while_queued"
REASON_LEASE_LOST_BEFORE_STARTED = "lease_lost_before_started"
REASON_LEASE_LOST_AFTER_STARTED = "lease_lost_after_started"

STARTED_EVENT_TYPE = "started"

# ``source`` value on every projection of this store, per
# ``[universal:state-provenance]``. Local to the substrate so the shared
# ``universal_protocol.status_basis`` constant list stays untouched.
SOURCE_EDGE_OPS_JOURNAL = "edge_ops_journal"

Clock = Callable[[], float]


def system_clock() -> float:
    """Wall-clock seconds — the default timestamp source for stored state."""
    return time.time()


def redelivery_for_kind(kind: str) -> Redelivery:
    """Redelivery class for *kind*; a kind absent from the map is ``never``."""
    return KIND_REDELIVERY.get(kind, DEFAULT_REDELIVERY)


class EdgeOpRefusedError(Exception):
    """A refusal the HTTP layer renders as ``{"error": {code, message}}``."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class OpRecord:
    """The folded authority row for one edge op."""

    op_id: str
    kind: str
    op: str
    args: dict[str, Any]
    redelivery: Redelivery
    idempotency_key: str
    state: OpState
    admitted_at_epoch: float
    deadline_epoch: float
    updated_at_epoch: float
    leased_by: str | None = None
    lease_id: str | None = None
    lease_expires_epoch: float | None = None
    started_at_epoch: float | None = None
    result: Any = None

    @property
    def is_terminal(self) -> bool:
        """True once no further transition can change this op."""
        return self.state in TERMINAL_STATES


@dataclass(frozen=True, slots=True)
class LeaseGrant:
    """What one successful lease hands the edge."""

    op_id: str
    lease_id: str
    kind: str
    op: str
    args: dict[str, Any]
    redelivery: Redelivery
    lease_ttl_s: float
    lease_expires_epoch: float
    deadline_epoch: float

    def to_dict(self) -> dict[str, Any]:
        """Wire shape for ``POST /api/v1/edge/leases``."""
        return {
            "op_id": self.op_id,
            "lease_id": self.lease_id,
            "kind": self.kind,
            "op": self.op,
            "args": self.args,
            "redelivery": self.redelivery,
            "lease_ttl_s": self.lease_ttl_s,
            "lease_expires_epoch": self.lease_expires_epoch,
            "deadline_epoch": self.deadline_epoch,
        }
