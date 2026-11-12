"""Caller-facing edge-op service functions: ``admit``, ``get``, ``await_result``.

Stargate exposes resources; it never dials the edge and never dials MCP. These
are the three a caller (an MCP tool, or a Stargate pipeline step) uses, and the
HTTP routes in ``routes.py`` are thin wrappers over them.

``admit`` fails fast rather than queueing into silence: when no edge serving
the op's kind has polled inside the presence window the caller gets
``edge_unavailable`` immediately.
``await_result`` is bounded by plan binding 3 at 30 s and answers
``edge_pending`` on timeout — never ``ok: true``. Terminal status stays separate
from the handler result: a handler that answered ``ok: false`` is ``completed``,
and its dict is returned verbatim with ``op_id`` added.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

from universal_protocol.status_basis import status_basis

from . import config
from .model import (
    CODE_EDGE_PENDING,
    CODE_EDGE_UNAVAILABLE,
    CODE_EXPIRED,
    CODE_FAILED,
    CODE_NOT_FOUND,
    CODE_UNRESOLVED,
    SOURCE_EDGE_OPS_JOURNAL,
    EdgeOpRefusedError,
    OpRecord,
    redelivery_for_kind,
)
from .store import EdgeOpsJournal
from .waiter import TRANSITION_CHANNEL

_TERMINAL_CODES = {
    "failed": CODE_FAILED,
    "expired": CODE_EXPIRED,
    "unknown": CODE_UNRESOLVED,
}


def _iso(epoch: float) -> str:
    """Render a stored epoch as an ISO-8601 instant for projections."""
    return datetime.fromtimestamp(epoch, tz=UTC).isoformat().replace("+00:00", "Z")


async def admit(
    journal: EdgeOpsJournal,
    *,
    kind: str,
    op: str,
    args: dict[str, Any],
    idempotency_key: str,
    deadline_s: float | None = None,
) -> str:
    """Queue one edge op and return its ``op_id``.

    The caller owns the idempotency key; re-admitting with the same key inside
    the dedupe window returns the original ``op_id`` and queues nothing. The
    op's redelivery class comes from its kind, so one authority decides whether
    the edge may run the handler before Stargate acknowledges ``started``.

    Raises:
        EdgeOpRefusedError: 503 ``edge_unavailable`` when no edge serving
            *kind* has polled within two long-poll intervals.
    """
    if not await journal.edge_seen_recently(kind=kind):
        raise EdgeOpRefusedError(
            503,
            CODE_EDGE_UNAVAILABLE,
            f"No edge serving kind {kind!r} has polled for leases within two "
            "long-poll intervals.",
        )
    record, _created = await journal.admit(
        kind=kind,
        op=op,
        args=args,
        redelivery=redelivery_for_kind(kind),
        idempotency_key=idempotency_key,
        deadline_s=config.clamp_deadline_seconds(deadline_s),
    )
    return record.op_id


async def get(journal: EdgeOpsJournal, op_id: str) -> dict[str, Any]:
    """Return the folded projection of one op.

    Raises:
        EdgeOpRefusedError: 404 when the op id is unknown.
    """
    record = await journal.get(op_id)
    if record is None:
        raise EdgeOpRefusedError(404, CODE_NOT_FOUND, f"Unknown edge op {op_id!r}.")
    return op_projection(record)


async def await_result(
    journal: EdgeOpsJournal,
    *,
    op_id: str,
    timeout_s: float | None = None,
) -> dict[str, Any]:
    """Wait up to 30 s for an op to reach a terminal state.

    The wait parks on lifecycle transitions rather than polling, and the budget
    is an idle bound on the absence of transitions, not a verdict on the work:
    a caller that times out gets ``{"ok": false, "code": "edge_pending",
    "op_id": ...}`` and reads the result later.

    Raises:
        EdgeOpRefusedError: 404 when the op id is unknown.
    """
    budget = config.clamp_await_seconds(timeout_s)
    hold_until = time.monotonic() + budget
    while True:
        since = journal.waiter.epoch(TRANSITION_CHANNEL)
        record = await journal.get(op_id)
        if record is None:
            raise EdgeOpRefusedError(404, CODE_NOT_FOUND, f"Unknown edge op {op_id!r}.")
        if record.is_terminal:
            return terminal_envelope(record)
        remaining = hold_until - time.monotonic()
        if remaining <= 0:
            return {
                "ok": False,
                "code": CODE_EDGE_PENDING,
                "op_id": op_id,
                "status": record.state,
            }
        await journal.waiter.wait(TRANSITION_CHANNEL, since=since, timeout=remaining)


def terminal_envelope(record: OpRecord) -> dict[str, Any]:
    """Render a terminal op as the caller-facing reply.

    ``completed`` returns the handler's own dict verbatim with ``op_id`` added,
    so a relay that answered ``{"ok": false, "code": ...}`` reaches the caller
    unchanged. The other terminal states carry no handler result, so they get
    an ``ok: false`` envelope naming which one fired.
    """
    if record.state == "completed" and isinstance(record.result, dict):
        return {**record.result, "op_id": record.op_id}
    if record.state == "completed":
        return {"ok": True, "op_id": record.op_id, "result": record.result}
    return {
        "ok": False,
        "code": _TERMINAL_CODES.get(record.state, CODE_UNRESOLVED),
        "op_id": record.op_id,
        "status": record.state,
        "result": record.result,
    }


def op_projection(record: OpRecord) -> dict[str, Any]:
    """Project one op record with its state basis named.

    Per ``[universal:state-provenance]`` the projection says where the value
    came from (the sqlite journal), as of when, at what scope, and what a
    reader consults to recover it. The in-memory wake registry never appears
    here because it is not authority.
    """
    return status_basis(
        "status",
        record.state,
        as_of=_iso(record.updated_at_epoch),
        source=SOURCE_EDGE_OPS_JOURNAL,
        scope=f"edge_op:{record.op_id}",
        epoch={"writer": SOURCE_EDGE_OPS_JOURNAL},
        recovery={"consulted": [SOURCE_EDGE_OPS_JOURNAL]},
        state=record.state,
        op_id=record.op_id,
        kind=record.kind,
        op=record.op,
        args=record.args,
        redelivery=record.redelivery,
        deadline_epoch=record.deadline_epoch,
        leased_by=record.leased_by,
        lease_expires_epoch=record.lease_expires_epoch,
        started_at_epoch=record.started_at_epoch,
        result=record.result,
    )
