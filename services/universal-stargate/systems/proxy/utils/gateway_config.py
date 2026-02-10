"""
Gateway configuration normalization utilities.
"""

from urllib.parse import urlparse

from universal_logging import get_logger

from gateway_client import GatewayConfig

logger = get_logger(__name__)


def _is_valid_url(url: str) -> bool:
    """
    Validate that a string is a valid HTTP/HTTPS URL.

    Args:
        url: URL string to validate

    Returns:
        True if URL is valid HTTP/HTTPS, False otherwise
    """
    try:
        result = urlparse(url)
        return all([result.scheme in ("http", "https"), result.netloc])
    except (ValueError, AttributeError):
        return False


def _normalize_gateway_config(
    gateway_config: str | GatewayConfig | None = None,
    gateway_url: str | None = None,
    default_url: str | None = "http://localhost:8000",
    default_timeout: float = 30.0,
) -> GatewayConfig:
    """
    Normalize gateway configuration inputs to GatewayConfig.

    Args:
        gateway_config: Gateway URL (string) or GatewayConfig object
        gateway_url: Single gateway URL (convenience parameter)
        default_url: Default URL if nothing provided (None = require explicit config)
        default_timeout: Default timeout for gateway connections (seconds)

    Returns:
        GatewayConfig object

    Raises:
        ValueError: If both gateway_config and gateway_url provided
        ValueError: If URLs are invalid
    """
    # Validate mutual exclusivity
    if gateway_config is not None and gateway_url is not None:
        raise ValueError(
            "Cannot provide both 'gateway_config' and 'gateway_url'. "
            "Use one or the other."
        )

    # If explicit config provided
    if gateway_config is not None:
        logger.info(
            f"_normalize_gateway_config(): gateway_config type={type(gateway_config).__name__}, "
            f"gateway_url={gateway_url}, default_url={default_url}"
        )
        if isinstance(gateway_config, str):
            if not _is_valid_url(gateway_config):
                raise ValueError(
                    f"Invalid gateway URL: '{gateway_config}'. "
                    f"Must be a valid HTTP/HTTPS URL (e.g., 'http://localhost:9998')"
                )
            return GatewayConfig(
                base_url=gateway_config,
                name="gateway-1",
                api_key=None,
                timeout=default_timeout,
                socket_path=None,
            )
        elif isinstance(gateway_config, GatewayConfig):
            # CRITICAL: Capture original socket_path to verify it's preserved
            original_socket_path = gateway_config.socket_path

            # Diagnostic logging
            logger.info(
                f"_normalize_gateway_config(): Received GatewayConfig - "
                f"name={gateway_config.name}, "
                f"socket_path={original_socket_path}, "
                f"base_url={gateway_config.base_url}"
            )

            # Skip URL validation if using Unix socket (base_url is ignored)
            if not gateway_config.socket_path and not _is_valid_url(
                gateway_config.base_url
            ):
                raise ValueError(
                    f"Invalid gateway URL in GatewayConfig: '{gateway_config.base_url}'"
                )
            if gateway_config.timeout is not None and gateway_config.timeout < 0.1:
                raise ValueError(
                    f"Invalid timeout for {gateway_config.base_url}: "
                    f"{gateway_config.timeout}. Timeout must be >= 0.1 seconds"
                )

            # CRITICAL: Verify socket_path is preserved (should never change)
            if (
                original_socket_path
                and gateway_config.socket_path != original_socket_path
            ):
                error_msg = (
                    f"CRITICAL: socket_path was modified during normalization! "
                    f"Original: {original_socket_path}, Current: {gateway_config.socket_path}"
                )
                logger.error(error_msg)
                raise ValueError(error_msg)

            # CRITICAL: Preserve socket_path when returning GatewayConfig
            logger.info(
                f"_normalize_gateway_config(): Returning GatewayConfig - "
                f"socket_path={gateway_config.socket_path}, base_url={gateway_config.base_url}"
            )
            return gateway_config
        else:
            raise ValueError(
                f"Invalid gateway config type: {type(gateway_config).__name__}. "
                f"Expected str or GatewayConfig."
            )

    # Convenience: single URL
    if gateway_url is not None:
        if not _is_valid_url(gateway_url):
            raise ValueError(
                f"Invalid gateway URL: '{gateway_url}'. "
                f"Must be a valid HTTP/HTTPS URL (e.g., 'http://localhost:9998')"
            )
        return GatewayConfig(
            base_url=gateway_url,
            name="gateway-1",
            api_key=None,
            timeout=default_timeout,
            socket_path=None,
        )

    # Default fallback
    if default_url is None:
        raise ValueError(
            "Either gateway_config or gateway_url must be provided. "
            "No default configuration available."
        )

    if not _is_valid_url(default_url):
        raise ValueError(f"Invalid default gateway URL: '{default_url}'")

    logger.warning(
        f"_normalize_gateway_config(): Using default URL fallback: {default_url} "
        f"(socket_path=None) - this should only happen if no config provided!"
    )
    return GatewayConfig(
        base_url=default_url,
        name="gateway-1",
        api_key=None,
        timeout=default_timeout,
        socket_path=None,
    )


def _normalize_gateway_configs(
    gateway_configs: list[str | GatewayConfig] | None = None,
    gateway_url: str | None = None,
    default_url: str | None = "http://localhost:8000",
    default_timeout: float = 30.0,
) -> list[GatewayConfig]:
    """
    Normalize gateway configuration inputs to List[GatewayConfig].

    Accepts various input formats and normalizes to a consistent list of
    GatewayConfig objects. This ensures all code paths use the same
    configuration structure regardless of input format.

    Args:
        gateway_configs: List of gateway URLs (strings) or GatewayConfig objects
        gateway_url: Single gateway URL (convenience parameter)
        default_url: Default URL if nothing provided (None = require explicit config)
        default_timeout: Default timeout for gateway connections (seconds)

    Returns:
        List of GatewayConfig objects (always at least 1)

    Raises:
        ValueError: If gateway_configs is empty list or contains invalid types
        ValueError: If no configuration provided and default_url is None
        ValueError: If URLs are invalid or duplicate
        ValueError: If both gateway_configs and gateway_url provided

    Examples:
        # Single URL via convenience parameter
        configs = _normalize_gateway_configs(gateway_url="http://localhost:9998")

        # List of URLs
        configs = _normalize_gateway_configs(
            gateway_configs=["http://localhost:9998", "http://localhost:9997"]
        )

        # List of GatewayConfig objects
        configs = _normalize_gateway_configs(
            gateway_configs=[
                GatewayConfig(base_url="http://localhost:9998", name="gw1"),
                GatewayConfig(base_url="http://localhost:9997", name="gw2")
            ]
        )

        # Mixed list
        configs = _normalize_gateway_configs(
            gateway_configs=[
                "http://localhost:9998",
                GatewayConfig(base_url="http://localhost:9997", name="gw2")
            ]
        )
    """
    # Validate mutual exclusivity
    if gateway_configs is not None and gateway_url is not None:
        raise ValueError(
            "Cannot provide both 'gateway_configs' and 'gateway_url'. "
            "Use 'gateway_configs' for multiple gateways or 'gateway_url' for a single gateway."
        )

    # If explicit list provided
    if gateway_configs is not None:
        if not gateway_configs:
            raise ValueError("gateway_configs must contain at least one gateway")

        # Normalize mixed string/GatewayConfig list
        normalized = []
        seen_urls = set()

        for i, gw in enumerate(gateway_configs):
            if isinstance(gw, str):
                # Validate URL format
                if not _is_valid_url(gw):
                    raise ValueError(
                        f"Invalid gateway URL at index {i}: '{gw}'. "
                        f"Must be a valid HTTP/HTTPS URL (e.g., 'http://localhost:9998')"
                    )

                # Check for duplicates
                if gw in seen_urls:
                    raise ValueError(f"Duplicate gateway URL: {gw}")
                seen_urls.add(gw)

                normalized.append(
                    GatewayConfig(
                        base_url=gw,
                        name=f"gateway-{i + 1}",
                        api_key=None,
                        timeout=default_timeout,
                        socket_path=None,
                    )
                )
            elif isinstance(gw, GatewayConfig):
                # Validate GatewayConfig URL
                if not _is_valid_url(gw.base_url):
                    raise ValueError(
                        f"Invalid gateway URL in GatewayConfig at index {i}: '{gw.base_url}'"
                    )

                # Check for duplicates
                if gw.base_url in seen_urls:
                    raise ValueError(f"Duplicate gateway URL: {gw.base_url}")
                seen_urls.add(gw.base_url)

                # Validate timeout if specified
                if gw.timeout is not None and gw.timeout < 0.1:
                    raise ValueError(
                        f"Invalid timeout for {gw.base_url}: {gw.timeout}. "
                        f"Timeout must be >= 0.1 seconds"
                    )

                normalized.append(gw)
            else:
                raise ValueError(
                    f"Invalid gateway config type at index {i}: {type(gw).__name__}. "
                    f"Expected str or GatewayConfig."
                )

        logger.debug(f"Normalized {len(normalized)} gateway config(s)")
        return normalized

    # Convenience: single URL
    if gateway_url is not None:
        if not _is_valid_url(gateway_url):
            raise ValueError(
                f"Invalid gateway URL: '{gateway_url}'. "
                f"Must be a valid HTTP/HTTPS URL (e.g., 'http://localhost:9998')"
            )

        logger.debug(f"Normalized single gateway URL: {gateway_url}")
        return [
            GatewayConfig(
                base_url=gateway_url,
                name="gateway-1",
                api_key=None,
                timeout=default_timeout,
                socket_path=None,
            )
        ]

    # Default fallback or require explicit configuration
    if default_url is None:
        raise ValueError(
            "Either gateway_configs or gateway_url must be provided. "
            "No default configuration available."
        )

    # Validate default URL
    if not _is_valid_url(default_url):
        raise ValueError(f"Invalid default gateway URL: '{default_url}'")

    logger.debug(f"Using default gateway URL: {default_url}")
    return [
        GatewayConfig(
            base_url=default_url,
            name="gateway-1",
            api_key=None,
            timeout=default_timeout,
            socket_path=None,
        )
    ]
