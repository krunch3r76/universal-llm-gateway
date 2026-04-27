"""Emit federation.link.timeout when the websockets library closes on keepalive."""

from __future__ import annotations

from typing import Any, Literal, cast

from universal_logging import get_logger
from websockets.exceptions import ConnectionClosed

logger = get_logger(__name__)

_KEEPALIVE_REASON = "keepalive ping timeout"


def _is_native_keepalive_ping_timeout(exc: BaseException) -> bool:
    if not isinstance(exc, ConnectionClosed):
        return False
    reason = (exc.reason or "").lower()
    return exc.code == 1011 and _KEEPALIVE_REASON in reason


async def emit_federation_link_timeout_if_applicable(
    *,
    event_bus: Any | None,
    exc: BaseException,
    link_role: Literal["remote_to_master", "master_to_edge"],
    peer_id: str,
) -> None:
    if event_bus is None or not _is_native_keepalive_ping_timeout(exc):
        return
    closed = cast(ConnectionClosed, exc)
    from src.scheduling.events.federation_signaling import FederationLinkTimeout

    try:
        await event_bus.publish_nowait(
            FederationLinkTimeout(
                link_role=link_role,
                peer_id=peer_id,
                close_code=closed.code,
                close_reason=closed.reason or "",
                cause="keepalive_ping",
            )
        )
    except Exception as emit_exc:
        # Caller must still raise the original disconnect (e.g. ConnectionClosed).
        logger.warning(
            "federation.link.timeout publish failed: %s",
            emit_exc,
            exc_info=emit_exc,
        )
