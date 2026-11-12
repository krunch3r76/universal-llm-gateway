"""The edge loop: lease, run one handler, post started and the result.

One shape, repeated: ask Stargate for work on our own connection, run the
handler for the op's kind, report. Nothing here listens for an inbound
connection and nothing dials a browser in A1.

Redelivery is the op's, not the loop's. A ``never`` kind may not run until
Stargate has acknowledged its ``started`` event, so a lease lost before that
ack is safe to re-lease; a ``safe`` kind runs regardless. Either way the loop
never re-runs an op itself — the only retry on this side is the backoff around
an unreachable Stargate.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from universal_logging import get_logger

from . import config
from .client import EdgeTransportError, StargateEdgeClient
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
        kinds: tuple[str, ...] = config.DEFAULT_KINDS,
        handlers: dict[str, Handler] | None = None,
        lease_wait_s: float = config.DEFAULT_LEASE_WAIT_SECONDS,
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._client = client
        self._kinds = kinds
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
        """Run until *should_stop*, backing off while Stargate is unreachable."""
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
            self._backoff_s = config.DEFAULT_BACKOFF_INITIAL_SECONDS

    async def _execute(self, grant: dict[str, Any]) -> None:
        """Acknowledge, run, and report one leased op.

        The ``started`` ack goes first for every leased op, handler or not: it
        is this edge saying the op is its own and must not be redelivered,
        which is what Stargate's terminal write requires of a ``never`` op. So
        an unsupported kind acks and then fails once, rather than posting a
        result Stargate refuses and sitting leased until the fold handed it
        back to the same handler-less edge.
        """
        op_id = str(grant.get("op_id"))
        kind = str(grant.get("kind"))
        if not await self._acknowledge(grant, op_id, kind):
            return
        handler = self._handlers.get(kind)
        if handler is None:
            await self._client.post_result(
                op_id=op_id,
                result={
                    "ok": False,
                    "code": CODE_KIND_UNSUPPORTED,
                    "message": f"No handler registered for kind {kind!r}.",
                },
                outcome="failed",
            )
            return
        await self._report(handler, grant, op_id, kind)

    async def _acknowledge(
        self,
        grant: dict[str, Any],
        op_id: str,
        kind: str,
    ) -> bool:
        """Post ``started``. False means a ``never`` kind must not run."""
        redelivery = str(grant.get("redelivery") or "never")
        try:
            await self._client.post_event(
                op_id=op_id, event_type=STARTED_EVENT_TYPE, payload={"kind": kind}
            )
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

    async def _report(
        self,
        handler: Handler,
        grant: dict[str, Any],
        op_id: str,
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
                result={
                    "ok": False,
                    "code": CODE_HANDLER_EXCEPTION,
                    "message": str(exc),
                },
                outcome="failed",
            )
            return
        await self._client.post_result(op_id=op_id, result=result, outcome="completed")
