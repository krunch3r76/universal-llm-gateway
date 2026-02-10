"""
Authentication handler for Master mode WebSocket server.

Validates incoming Remote connections.

INVARIANT: ¬authenticated(c, t=5s) ⟹ close(c, code=4003)
"""

import asyncio
import hmac
import json
import time
from dataclasses import dataclass, field

from fastapi import WebSocket
from universal_logging import get_logger

from ....common.config import FederationConfig
from ....common.protocol import (
    FederationMessageType,
    create_federation_auth_result,
    parse_federation_message,
)
from ....common.types import (
    PROTOCOL_VERSION,
    WS_CLOSE_AUTH_DEADLINE,
    WS_CLOSE_AUTH_FAILED,
    WS_CLOSE_IDENTITY_COLLISION,
    WS_CLOSE_PROTOCOL_MISMATCH,
    validate_version,
)

logger = get_logger(__name__)


@dataclass
class AuthenticatedRemote:
    """Authenticated Remote connection info."""

    stargate_id: str
    ws: WebSocket
    protocol_version: str
    authenticated_at: float = field(default_factory=time.time)


class MasterAuthHandler:
    """
    Handles authentication for incoming Remote WebSocket connections.

    Master accepts connections FROM Remotes (Remote-initiates model).

    INVARIANT: auth_deadline exceeded ⟹ close(code=4003)
    """

    def __init__(self, config: FederationConfig):
        self._config = config
        # Build allowed remotes from config.remotes
        self._allowed_remotes: dict[str, str] = {
            remote.stargate_id: remote.api_key for remote in config.remotes
        }
        # Active connections by stargate_id
        self._connections: dict[str, AuthenticatedRemote] = {}

    @property
    def connected_remotes(self) -> list[str]:
        """List of connected Remote stargate_ids."""
        return list(self._connections.keys())

    def get_connection(self, stargate_id: str) -> AuthenticatedRemote | None:
        """Get connection for a specific Remote."""
        return self._connections.get(stargate_id)

    def is_connected(self, stargate_id: str) -> bool:
        """Check if Remote is connected."""
        return stargate_id in self._connections

    async def authenticate(self, ws: WebSocket) -> str | None:
        """
        Authenticate incoming Remote connection.

        Returns remote_id on success, None on failure.
        """
        deadline = self._config.ws_server.auth_deadline_seconds

        try:
            return await asyncio.wait_for(
                self._do_authenticate(ws),
                timeout=deadline,
            )
        except TimeoutError:
            logger.warning("Federation auth deadline exceeded")
            await self._close_with_code(
                ws, WS_CLOSE_AUTH_DEADLINE, "Auth deadline exceeded"
            )
            return None

    async def _do_authenticate(self, ws: WebSocket) -> str | None:
        """
        Perform authentication handshake.

        Receives auth message, validates credentials, registers connection.

        Args:
            ws: WebSocket connection to authenticate

        Returns:
            remote_id (str) on success, None on failure.
            Connection is closed with appropriate code on failure.

        NOTE: This method implements a linear authentication flow with ~12 sequential
        validation steps. This is acceptable for authentication handlers where the
        process is inherently sequential (receive → parse → validate → accept/reject).
        """
        try:
            raw = await ws.receive_text()
            msg_dict = json.loads(raw)
            msg = parse_federation_message(msg_dict)
        except json.JSONDecodeError as e:
            logger.warning(f"Invalid JSON in auth: {e}")
            await self._send_auth_result(ws, False, "Invalid JSON")
            return None
        except ValueError as e:
            logger.warning(f"Invalid message structure: {e}")
            await self._send_auth_result(ws, False, "Invalid message structure")
            return None

        if msg.type != FederationMessageType.FEDERATION_AUTH.value:
            logger.warning(f"Expected federation_auth, got {msg.type}")
            await self._send_auth_result(ws, False, "Expected federation_auth")
            return None

        data = msg.data
        remote_id = data.get("stargate_id")
        api_key = data.get("api_key")
        protocol_version = data.get("protocol_version")

        if not all([remote_id, api_key, protocol_version]):
            logger.warning("Missing auth fields")
            await self._send_auth_result(ws, False, "Missing required fields")
            return None

        if not validate_version(protocol_version):
            logger.warning(
                f"Protocol mismatch: {PROTOCOL_VERSION} vs {protocol_version}"
            )
            await self._close_with_code(
                ws,
                WS_CLOSE_PROTOCOL_MISMATCH,
                f"Protocol mismatch: expected {PROTOCOL_VERSION}",
            )
            return None

        # Validate remote is allowed (from remotes: config)
        expected_key = self._allowed_remotes.get(remote_id)
        if not expected_key:
            logger.warning(f"Unknown remote: {remote_id}")
            await self._send_auth_result(ws, False, "Unknown remote")
            await self._close_with_code(ws, WS_CLOSE_AUTH_FAILED, "Unknown remote")
            return None

        # Validate API key (constant-time)
        # Diagnostic logging (safe - doesn't expose key values, check before stripping)
        provided_len_orig = len(api_key)
        expected_len_orig = len(expected_key)
        provided_has_whitespace = api_key != api_key.strip()
        expected_has_whitespace = expected_key != expected_key.strip()

        # Strip whitespace (should already be stripped in config loader)
        api_key = api_key.strip()
        expected_key = expected_key.strip()

        provided_len = len(api_key)
        expected_len = len(expected_key)
        provided_repr = (
            repr(api_key) if provided_len <= 100 else f"<{provided_len} chars>"
        )
        expected_repr = (
            repr(expected_key) if expected_len <= 100 else f"<{expected_len} chars>"
        )

        if not hmac.compare_digest(api_key.encode(), expected_key.encode()):
            logger.warning(
                f"Invalid API key for remote {remote_id}",
                extra={
                    "provided_len": provided_len,
                    "expected_len": expected_len,
                    "provided_len_orig": provided_len_orig,
                    "expected_len_orig": expected_len_orig,
                    "lengths_match": provided_len == expected_len,
                    "provided_has_whitespace": provided_has_whitespace,
                    "expected_has_whitespace": expected_has_whitespace,
                    "provided_repr": provided_repr,
                    "expected_repr": expected_repr,
                },
            )
            await self._send_auth_result(ws, False, "Invalid credentials")
            await self._close_with_code(ws, WS_CLOSE_AUTH_FAILED, "Invalid credentials")
            return None

        # Check identity collision
        if remote_id in self._connections:
            logger.warning(f"Identity collision: {remote_id} already connected")
            await self._close_with_code(
                ws, WS_CLOSE_IDENTITY_COLLISION, "Identity already connected"
            )
            return None

        # Check connection limit
        max_connections = self._config.ws_server.max_connections
        if len(self._connections) >= max_connections:
            logger.warning(
                f"Connection limit exceeded: {len(self._connections)}/{max_connections}"
            )
            await self._send_auth_result(ws, False, "Connection limit exceeded")
            await self._close_with_code(
                ws, WS_CLOSE_AUTH_FAILED, "Connection limit exceeded"
            )
            return None

        # Auth successful
        self._connections[remote_id] = AuthenticatedRemote(
            stargate_id=remote_id,
            ws=ws,
            protocol_version=protocol_version,
        )

        await self._send_auth_result(ws, True, "Authenticated")
        logger.info(f"Remote {remote_id} authenticated successfully")

        return remote_id

    def remove_connection(self, remote_id: str) -> None:
        """Remove connection on disconnect."""
        self._connections.pop(remote_id, None)

    async def _send_auth_result(
        self,
        ws: WebSocket,
        success: bool,
        message: str | None = None,
    ) -> None:
        """Send authentication result to Remote."""
        msg = create_federation_auth_result(
            success=success,
            message=message,
            stargate_id=self._config.stargate_id,
            protocol_version=PROTOCOL_VERSION,
        )
        await ws.send_json(msg.to_dict())

    async def _close_with_code(self, ws: WebSocket, code: int, reason: str) -> None:
        """Close WebSocket with specific code."""
        try:
            await ws.close(code=code, reason=reason)
        except Exception as e:
            logger.debug(f"Error closing WebSocket (code={code}): {e}")
