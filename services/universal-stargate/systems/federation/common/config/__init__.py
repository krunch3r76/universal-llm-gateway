"""
Federation configuration module.

Provides configuration schema, loading, and validation for federation system.

Split into modules for Single Responsibility:
- schema.py: Dataclasses only (~200 SLOC)
- loader.py: YAML loading and parsing (~300 SLOC)
- validation.py: Validation logic (~100 SLOC)
"""

# Schema exports
# Loader exports
from .env_expansion import expand_env_vars
from .loader import load_federation_config, log_startup_banner
from .schema import (
    AllowedPeerConfig,
    ConfigurationError,
    ConnectionLimits,
    EndpointCategory,
    FederationConfig,
    HTTPPoolConfig,
    LocalEdgeConfig,
    MasterConnectionConfig,
    OverflowPolicy,
    RemoteStargateConfig,
    StargateMode,
    TelemetryBackpressure,
    TLSConfig,
    WSServerConfig,
)

__all__ = [
    # Schema
    "AllowedPeerConfig",
    "ConfigurationError",
    "ConnectionLimits",
    "EndpointCategory",
    "FederationConfig",
    "HTTPPoolConfig",
    "LocalEdgeConfig",
    "MasterConnectionConfig",
    "OverflowPolicy",
    "RemoteStargateConfig",
    "StargateMode",
    "TelemetryBackpressure",
    "TLSConfig",
    "WSServerConfig",
    # Loader
    "expand_env_vars",
    "load_federation_config",
    "log_startup_banner",
]
