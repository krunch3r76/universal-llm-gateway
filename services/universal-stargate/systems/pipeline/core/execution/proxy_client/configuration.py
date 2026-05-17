"""ProxyClient configuration dataclass.

Transport resolution is fully delegated to transport_utils; this module
no longer performs manual STARGATE_* environment parsing.
"""

from __future__ import annotations

from dataclasses import dataclass

from transport_utils import DEFAULT_STARGATE_URL


@dataclass(slots=True, frozen=True)
class ProxyClientConfig:
    """Configuration for ProxyClient Stargate transport.

    The stargate_url field encodes the transport mode:
    - "unix:///path/to/stargate.sock" for UDS (edge deployments)
    - "http://host:port" for TCP

    Resolution order is owned by transport_utils (STARGATE_UNIX_SOCKET,
    STARGATE_URL, then STARGATE_PORT default).

    Only a single request_timeout is passed to make_async_client(); connect
    vs read distinction is handled inside transport_utils if needed in future.
    """

    stargate_url: str = DEFAULT_STARGATE_URL
    request_timeout: float = 3600.0

    @classmethod
    def from_environment(cls) -> ProxyClientConfig:
        """Return config using transport_utils default resolution.

        from_environment is retained for API compatibility with existing
        call sites (ProxyClient.from_environment, handler wiring). It does
        not read environment variables directly; transport_utils owns that.
        """
        return cls(stargate_url=DEFAULT_STARGATE_URL)
