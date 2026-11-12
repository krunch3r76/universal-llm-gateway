"""The edge loop: lease, run one handler, post started and the result.

One shape, repeated: ask Stargate for work on our own connection, run the
handler for the op's kind, report. Nothing here listens for an inbound
connection and nothing dials a browser in A1.

Redelivery is the op's, not the loop's. A ``never`` kind may not run until
Stargate has acknowledged its ``started`` event, so a lease lost before that
ack is safe to re-lease; a ``safe`` kind may run even when the ack's delivery
failed. What neither kind does is run on a *refused* ack: the answer is read,
not assumed, and a 409 or any status other than ``started`` means the op is
closed. Either way the loop never re-runs an op itself — the only retry on
this side is the backoff around an unreachable Stargate.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from universal_logging import get_logger

from . import config
from .client import EdgeConflictError, EdgeTransportError, StargateEdgeClient
from .registry import HANDLERS, Handler

logger = get_logger(__name__)

STARTED_EVENT_TYPE = "started"

CODE_KIND_UNSUPPORTED = "edge_kind_unsupported"
CODE_HANDLER_EXCEPTION = "edge_handler_exception"


class EdgeLoop:
    """Lease-driven executor for one edge process."""

    def __init__(
        self,
        client: StargateEdgeClient,
        *,
        kinds: tuple[str, ...] | None = None,
        handlers: dict[str, Handler] | None = None,
        lease_wait_s: float = config.DEFAULT_LEASE_WAIT_SECONDS,
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._client = client
        self._kinds = kinds if kinds is not None else config.edge_kinds()
        self._handlers = handlers if handlers is not None else HANDLERS
        self._lease_wait_s = lease_wait_s
        self._sleeper = sleeper
        self._backoff_s = config.DEFAULT_BACKOFF_INITIAL_SECONDS

    async def run_once(self) -> str | None:
        """Lease at most one op and carry it to a result. ``None`` if idle."""
        grant = await self._client.lease(kinds=self._kinds, wait_s=self._lease_wait_s)
        if grant is None:
            return None
        await self._execute(grant)
        return str(grant.get("op_id"))

    async def run_forever(
        self,
        *,
        should_stop: Callable[[], bool] = lambda: False,
    ) -> None:
        """Run until *should_stop*, backing off while Stargate is unreachable.

        A refused write is not unreachability. Stargate answered, so the
        backoff stays reset and the next lease is issued immediately; only a
        transport fault earns a wait.
        """
        while not should_stop():
            try:
                await self.run_once()
            except EdgeTransportError as exc:
                logger.warning(
                    "Stargate unreachable, backing off %.1fs: %s",
                    self._backoff_s,
                    exc,
                )
                await self._sleeper(self._backoff_s)
                self._backoff_s = min(
                    self._backoff_s * 2, config.DEFAULT_BACKOFF_MAX_SECONDS
                )
                continue
            except EdgeConflictError as exc:
                logger.warning("Stargate refused a write, dropping the op: %s", exc)
            self._backoff_s = config.DEFAULT_BACKOFF_INITIAL_SECONDS

    async def _execute(self, grant: dict[str, Any]) -> None:
        """Acknowledge, run, and report one leased op.

        The ``started`` ack goes first for every leased op, handler or not: it
        is this edge saying the op is its own and must not be redelivered,
        which is what Stargate's terminal write requires of a ``never`` op. So
        an unsupported kind acks and then fails once, rather than posting a
        result Stargate refuses and sitting leased until the fold handed it
        back to the same handler-less edge.

        A write Stargate refuses on the op's state ends the attempt here: the
        op is closed or no longer ours, so there is nowhere to post and
        nothing to retry.
        """
        op_id = str(grant.get("op_id"))
        lease_id = str(grant.get("lease_id"))
        kind = str(grant.get("kind"))
        if not await self._acknowledge(grant, op_id, lease_id, kind):
            return
        try:
            await self._dispatch(grant, op_id, lease_id, kind)
        except EdgeConflictError as exc:
            logger.warning(
                "Stargate refused a write for %s (%s), dropping the op: %s",
                op_id,
                kind,
                exc,
            )

    async def _dispatch(
        self,
        grant: dict[str, Any],
        op_id: str,
        lease_id: str,
        kind: str,
    ) -> None:
        """Run the registered handler, or report the kind as unsupported."""
        handler = self._handlers.get(kind)
        if handler is None:
            await self._client.post_result(
                op_id=op_id,
                lease_id=lease_id,
                result={
                    "ok": False,
                    "code": CODE_KIND_UNSUPPORTED,
                    "message": f"No handler registered for kind {kind!r}.",
                },
                outcome="failed",
            )
            return
        await self._report(handler, grant, op_id, lease_id, kind)

    async def _acknowledge(
        self,
        grant: dict[str, Any],
        op_id: str,
        lease_id: str,
        kind: str,
    ) -> bool:
        """Post ``started`` and read the answer. False means do not run.

        The ack is the permission, so its answer has to be read rather than
        assumed. Stargate refuses it with 409 once the op is terminal — the
        deadline passed while this process was stalled, or a lost lease folded
        it to ``unknown`` — and a 2xx carrying any status other than
        ``started`` is not permission either. Both stop every kind, ``safe``
        included: the op is closed, so the result would be refused or dropped
        and the work is waste. Only the transport fault still turns on the
        class, because there Stargate may have recorded the ack before the
        connection broke: a ``never`` kind must not run, a ``safe`` kind may.
        """
        redelivery = str(grant.get("redelivery") or "never")
        try:
            accepted = await self._client.post_event(
                op_id=op_id,
                lease_id=lease_id,
                event_type=STARTED_EVENT_TYPE,
                payload={"kind": kind},
            )
        except EdgeConflictError as exc:
            logger.warning(
                "started ack refused for %s (%s); not running: %s",
                op_id,
                kind,
                exc,
            )
            return False
        except EdgeTransportError as exc:
            if redelivery == "never":
                logger.warning(
                    "started ack failed for %s (%s); not running: %s",
                    op_id,
                    kind,
                    exc,
                )
                return False
            logger.warning(
                "started ack failed for redelivery-safe %s (%s): %s",
                op_id,
                kind,
                exc,
            )
            return True
        status = accepted.get("status")
        if status != STARTED_EVENT_TYPE:
            logger.warning(
                "started ack for %s (%s) answered status %r, not 'started'; "
                "not running",
                op_id,
                kind,
                status,
            )
            return False
        return True

    async def _report(
        self,
        handler: Handler,
        grant: dict[str, Any],
        op_id: str,
        lease_id: str,
        kind: str,
    ) -> None:
        """Run the handler and post its result verbatim, or a failure."""
        args = grant.get("args")
        try:
            result = await handler(str(grant.get("op")), args or {})
        except Exception as exc:
            logger.exception("edge handler for %s (%s) raised", op_id, kind)
            await self._client.post_result(
                op_id=op_id,
                lease_id=lease_id,
                result={
                    "ok": False,
                    "code": CODE_HANDLER_EXCEPTION,
                    "message": str(exc),
                },
                outcome="failed",
            )
            return
        await self._client.post_result(
            op_id=op_id, lease_id=lease_id, result=result, outcome="completed"
        )
