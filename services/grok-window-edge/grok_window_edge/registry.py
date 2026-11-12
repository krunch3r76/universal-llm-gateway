"""Handler registry for edge-op kinds. A1 registers exactly one: ``ping``.

A kind with no handler is not retried: the loop posts a ``failed`` result so
the caller learns immediately instead of waiting out the op deadline.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

Handler = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]


async def ping(op: str, args: dict[str, Any]) -> dict[str, Any]:
    """Answer a liveness op. Opens no CDP connection and takes no lock."""
    return {
        "ok": True,
        "pong": True,
        "op": op,
        "nonce": args.get("nonce"),
    }


HANDLERS: dict[str, Handler] = {"ping": ping}


def handler_for(kind: str) -> Handler | None:
    """Return the handler registered for *kind*, or ``None``."""
    return HANDLERS.get(kind)
