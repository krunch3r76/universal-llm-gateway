"""
Gateway connection validation.

Validates that Edge Stargates connect to Gateway (not another Stargate).

INVARIANT:
    ∀ edge: health(gateway).role = "gateway"
    ∀ edge: health(gateway).service = "universal-llm-gateway"
"""

from dataclasses import dataclass
from typing import Any

from universal_logging import get_logger

logger = get_logger(__name__)


class GatewayConnectionError(Exception):
    """Raised when Edge Stargate connects to wrong service type."""

    pass


@dataclass(slots=True, frozen=True)
class ServiceFingerprint:
    """Service fingerprint from health endpoint."""

    service: str
    role: str
    raw_health: dict[str, Any]

    @classmethod
    def from_health_response(cls, health: dict[str, Any]) -> "ServiceFingerprint":
        """Parse fingerprint from health response."""
        return cls(
            service=health.get("service", "").lower(),
            role=health.get("role", "").lower(),
            raw_health=health,
        )

    def is_gateway(self) -> bool:
        """Check if fingerprint indicates Gateway service."""
        return self.role == "gateway" and "gateway" in self.service

    def is_stargate(self) -> bool:
        """Check if fingerprint indicates Stargate service."""
        return self.role == "stargate" or "stargate" in self.service


def validate_gateway_fingerprint(fingerprint: ServiceFingerprint) -> None:
    """
    Validate fingerprint is from Gateway (fail-fast).

    INVARIANT (strict, no fallback):
        role = "gateway" ∧ service contains "gateway" ⟹ OK
        role = "stargate" ∨ service contains "stargate" ⟹ FAIL
        else ⟹ FAIL (unknown service)

    Args:
        fingerprint: Parsed service fingerprint

    Raises:
        GatewayConnectionError: If not connected to Gateway
    """
    if fingerprint.is_gateway():
        logger.info(
            f"✅ Gateway fingerprint validated: "
            f"service={fingerprint.service}, role={fingerprint.role}"
        )
        return

    if fingerprint.is_stargate():
        raise GatewayConnectionError(
            "ARCHITECTURE VIOLATION: Edge Stargate connected to Stargate.\n"
            f"  Found: service={fingerprint.raw_health.get('service')}, "
            f"role={fingerprint.raw_health.get('role')}\n"
            f"  Expected: service=universal-llm-gateway, role=gateway\n\n"
            "Edge Stargate must connect to Gateway (execution authority).\n"
            "Check gateway.url or gateway.socket_path in your config.\n\n"
            "Architecture:\n"
            "  Edge Stargate → Gateway (correct)\n"
            "  Edge Stargate → Stargate (forbidden - this error)"
        )

    # Unknown service - fail-fast (no backward compatibility)
    raise GatewayConnectionError(
        f"Unknown service at gateway endpoint.\n"
        f"  Found: service={fingerprint.raw_health.get('service')}, "
        f"role={fingerprint.raw_health.get('role')}\n"
        f"  Expected: service=universal-llm-gateway, role=gateway\n\n"
        "Edge Stargate must connect to Gateway with valid fingerprint.\n"
        "Ensure Gateway is updated to include service fingerprint in /health."
    )


async def fetch_and_validate_gateway_connection(gateway_client: Any) -> None:
    """
    Fetch health and validate Edge Stargate connects to Gateway.

    Orchestrates:
    1. Fetch /health from connected endpoint
    2. Parse fingerprint
    3. Validate fingerprint (fail-fast)

    Args:
        gateway_client: GatewayClient with get_health() method

    Raises:
        GatewayConnectionError: If connected to wrong service type
    """
    if not gateway_client.is_connected():
        logger.debug("Gateway not connected, skipping connection validation")
        return

    try:
        health = await gateway_client.get_health()

        if not health:
            # No health response - fail-fast (no silent fallback)
            raise GatewayConnectionError(
                "Could not fetch health from gateway endpoint.\n"
                "Ensure Gateway is running and /health endpoint is accessible."
            )

        fingerprint = ServiceFingerprint.from_health_response(health)
        validate_gateway_fingerprint(fingerprint)

    except GatewayConnectionError:
        raise  # Re-raise validation errors
    except Exception as e:
        # Network errors during validation - fail-fast
        raise GatewayConnectionError(
            f"Failed to validate gateway connection: {e}\n"
            "Check network connectivity to gateway endpoint."
        ) from e
