"""
Authentication client for Local Edge connection.

Relay initiates connection and sends auth to Edge.
Uses same protocol as Remote→Master authentication.
"""

import asyncio
import json

from universal_logging import get_logger
from websockets.client import WebSocketClientProtocol

from ....common.protocol import (
    FederationMessageType,
    create_federation_auth,
    parse_federation_message,
)
from ....common.types import PROTOCOL_VERSION

logger = get_logger(__name__)


class LocalEdgeAuthClient:
    """
    Handles Relay→Edge authentication.

    Same protocol as Remote→Master (RemoteAuthClient).
    """

    def __init__(
        self,
        local_stargate_id: str,
        api_key: str,
        auth_timeout_seconds: float = 5.0,
    ):
        self._local_stargate_id = local_stargate_id
        self._api_key = api_key
        self._auth_timeout_seconds = auth_timeout_seconds

    async def authenticate(self, ws: WebSocketClientProtocol) -> bool:
        """
        Perform authentication handshake.

        Sends auth first, waits for result (same as Remote→Master).

        Returns:
            True if accepted, False otherwise.
        """
        # Send auth message
        auth_msg = create_federation_auth(
            stargate_id=self._local_stargate_id,
            api_key=self._api_key,
            protocol_version=PROTOCOL_VERSION,
        )

        await ws.send(json.dumps(auth_msg.to_dict()))

        try:
            raw = await asyncio.wait_for(
                ws.recv(),
                timeout=self._auth_timeout_seconds,
            )

            msg_dict = json.loads(raw)
            msg = parse_federation_message(msg_dict)

            if msg.type != FederationMessageType.FEDERATION_AUTH_RESULT.value:
                logger.warning(f"Unexpected auth response: {msg.type}")
                return False

            data = msg.data

            if not data.get("success"):
                reason = data.get("message", "Unknown")
                logger.warning(f"Auth rejected by Edge: {reason}")
                return False

            return True

        except TimeoutError:
            logger.warning("Auth timeout from Edge")
            return False
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(f"Invalid auth response: {e}")
            return False
