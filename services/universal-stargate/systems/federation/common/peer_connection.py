"""
Peer connection protocol for federation.

Defines interface for all federation connections (client or server-initiated).

Phase 2/3 Contract:
    - Phase 2: RemotePeerConnection implements this (wraps server-accepted connection)
    - Phase 3: RemoteWebSocketClient implements this (client-initiated connection)
"""

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class PeerConnection(Protocol):
    """
    Protocol for federation peer connections.

    Abstracts whether connection was initiated by local (client)
    or remote (server-accepted) side.

    INVARIANT: ∀ PeerConnection: send/receive semantics identical

    Implementation Requirements:
        - peer_id: Must return remote stargate_id
        - is_connected: Must reflect authenticated + socket alive
        - send_telemetry: Must be non-blocking (queue-based)
        - send_request: May raise NotImplementedError in Phase 1-3
    """

    @property
    def peer_id(self) -> str:
        """
        Remote peer's stargate_id.

        Phase 2: Returns remote.stargate_id from AuthenticatedRemote
        Phase 3: Returns master_config.stargate_id
        """
        ...

    @property
    def is_connected(self) -> bool:
        """
        Connection is established and authenticated.

        Phase 2: Returns True (only created for authenticated connections)
        Phase 3: Returns websocket is not None and authenticated
        """
        ...

    async def send_telemetry(self, signal: str, payload: dict[str, Any]) -> bool:
        """
        Send telemetry to peer (fire-and-forget via bounded queue).

        Args:
            signal: Telemetry signal name (e.g., "RESOURCE_UPDATE", "MODEL_LOADED")
            payload: Telemetry data dict

        Returns:
            True if telemetry enqueued successfully.
            False if queue is full (backpressure) or connection lost.

        Note:
            Non-blocking - uses bounded queue (BoundedQueue.try_put()).
            Dropped telemetry is logged but does not raise exceptions.

        Implementation:
            MUST use bounded queue (BoundedQueue.try_put())
            MUST NOT block or await send directly
        """
        ...

    async def send_request(self, payload: dict[str, Any]) -> dict[str, Any]:
        """
        Send request and await response (for future RPC).

        Phase 1-3: Implementations should raise NotImplementedError.
        Future: Will support request/response patterns.

        Raises:
            NotImplementedError: Until RPC support is implemented.
        """
        ...

    async def disconnect(self) -> None:
        """
        Disconnect from peer and cleanup resources.

        Call during:
            - System shutdown (ConnectionManager.stop())
            - Manual disconnection requests
            - Connection error recovery

        Implementation Requirements:
            MUST perform these actions:
                - Close WebSocket connection (if open)
                - Cancel all background tasks (sender, ping, receivers)
                - Cleanup queues and reset state
                - Set is_connected = False

            MUST be idempotent:
                - Safe to call multiple times
                - No errors if already disconnected
                - No-op if resources already cleaned up

        Phase 2/3 Contract:
            - Phase 2 (RemotePeerConnection): Server manages lifecycle,
              disconnect() likely no-op (handled by server shutdown)
            - Phase 3 (RemoteWebSocketClient): Full cleanup - cancel tasks,
              close socket, stop telemetry sender

        Note:
            After disconnect(), is_connected should return False.
            Reconnection (if desired) requires new connect() call.
        """
        ...
