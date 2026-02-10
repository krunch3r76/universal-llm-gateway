"""Enhanced authentication middleware for WebSocket connections."""

import hashlib
import hmac
import os
import time
from dataclasses import dataclass
from enum import Enum

from universal_logging import get_logger

logger = get_logger(__name__)


class AuthLevel(Enum):
    """Authentication levels for WebSocket connections."""

    NONE = "none"
    API_KEY = "api_key"
    SIGNED_TOKEN = "signed_token"
    OAUTH = "oauth"  # Future enhancement


@dataclass
class AuthConfig:
    """Authentication configuration."""

    enabled: bool = True
    min_level: AuthLevel = AuthLevel.API_KEY
    api_keys: dict[str, "APIKeyInfo"] = None
    jwt_secret: str | None = None
    allowed_origins: list[str] = None

    def __post_init__(self):
        if self.api_keys is None:
            self.api_keys = {}
        if self.allowed_origins is None:
            self.allowed_origins = ["*"]


@dataclass
class APIKeyInfo:
    """Information about an API key."""

    key_id: str
    key_hash: str  # SHA256 hash of the actual key
    permissions: set[str]
    rate_limit_tier: str = "standard"
    created_at: float = 0
    last_used: float = 0
    metadata: dict = None


class WebSocketAuthenticator:
    """Enhanced authenticator for WebSocket connections."""

    def __init__(self, config: AuthConfig | None = None):
        self.config = config or self._load_default_config()
        self._permission_cache: dict[str, set[str]] = {}

    def _load_default_config(self) -> AuthConfig:
        """Load default configuration from environment."""
        logger.info(
            "Loading WebSocket authentication config from environment variables..."
        )
        config = AuthConfig()

        # Check if authentication is enabled
        config.enabled = os.environ.get("WS_AUTH_ENABLED", "true").lower() == "true"
        logger.info(f"WebSocket auth enabled: {config.enabled}")

        # Load API key from environment
        api_key = os.environ.get("GATEWAY_API_KEY")
        if api_key:
            key_hash = hashlib.sha256(api_key.encode()).hexdigest()
            config.api_keys[key_hash] = APIKeyInfo(
                key_id=f"{api_key[:4]}...{api_key[-4:]}",
                key_hash=key_hash,
                permissions={"*"},
                rate_limit_tier="unlimited",
            )
            logger.info(f"Loaded API key with ID: {config.api_keys[key_hash].key_id}")
        else:
            logger.error("GATEWAY_API_KEY not set — auth will reject all requests.")

        # Load allowed origins
        allowed_origins = os.environ.get("WS_ALLOWED_ORIGINS", "*")
        config.allowed_origins = [o.strip() for o in allowed_origins.split(",")]

        return config

    async def authenticate(
        self,
        api_key: str | None = None,
        auth_header: str | None = None,
        origin: str | None = None,
    ) -> APIKeyInfo | None:
        """
        Authenticate a WebSocket connection.

        Args:
            api_key: API key from query parameter
            auth_header: Authorization header value
            origin: Origin header value

        Returns:
            APIKeyInfo if authenticated, None otherwise
        """
        if not self.config.enabled:
            # Authentication disabled, allow all
            return APIKeyInfo(
                key_id="anonymous",
                key_hash="",
                permissions={"*"},
                rate_limit_tier="standard",
            )

        # Check origin if configured
        if not self._check_origin(origin):
            logger.warning(f"Rejected connection from unauthorized origin: {origin}")
            return None

        # Extract API key
        key = api_key
        if not key and auth_header:
            if auth_header.startswith("Bearer "):
                key = auth_header[7:]

        logger.info(f"Attempting to authenticate with key: {key}")

        if not key:
            logger.warning("No API key provided")
            return None

        # Validate API key
        key_info = self._validate_api_key(key)
        if key_info:
            logger.info(f"Successfully validated API key. Key ID: {key_info.key_id}")
            # Update last used timestamp
            key_info.last_used = time.time()
            return key_info

        logger.warning(f"Invalid API key provided: {key}")
        return None

    def _check_origin(self, origin: str | None) -> bool:
        """Check if origin is allowed."""
        if not origin or "*" in self.config.allowed_origins:
            return True

        # Normalize origin
        origin = origin.lower().rstrip("/")

        for allowed in self.config.allowed_origins:
            allowed = allowed.lower().rstrip("/")
            if allowed == origin:
                return True
            # Support wildcard subdomains
            if allowed.startswith("*.") and origin.endswith(allowed[1:]):
                return True

        return False

    def _validate_api_key(self, key: str) -> APIKeyInfo | None:
        """Validate an API key."""
        key_hash = hashlib.sha256(key.encode()).hexdigest()

        for key_info in self.config.api_keys.values():
            if hmac.compare_digest(key_info.key_hash, key_hash):
                return key_info

        return None

    def check_permission(self, key_info: APIKeyInfo, permission: str) -> bool:
        """
        Check if an API key has a specific permission.

        Args:
            key_info: API key information
            permission: Permission to check (e.g., "state.read", "resource.reserve")

        Returns:
            True if permission granted, False otherwise
        """
        if "*" in key_info.permissions:
            return True

        # Check exact permission
        if permission in key_info.permissions:
            return True

        # Check wildcard permissions (e.g., "state.*" matches "state.read")
        parts = permission.split(".")
        for i in range(len(parts)):
            wildcard = ".".join(parts[: i + 1]) + ".*"
            if wildcard in key_info.permissions:
                return True

        return False

    def get_rate_limit_tier(self, key_info: APIKeyInfo) -> str:
        """Get the rate limit tier for an API key."""
        return key_info.rate_limit_tier

    def add_api_key(
        self, key: str, permissions: set[str], rate_limit_tier: str = "standard"
    ) -> str:
        """Add a new API key (for admin use)."""
        key_id = hashlib.sha256(f"{key}{time.time()}".encode()).hexdigest()[:8]
        key_hash = hashlib.sha256(key.encode()).hexdigest()

        self.config.api_keys[key_id] = APIKeyInfo(
            key_id=key_id,
            key_hash=key_hash,
            permissions=permissions,
            rate_limit_tier=rate_limit_tier,
            created_at=time.time(),
        )

        return key_id

    def revoke_api_key(self, key_id: str) -> bool:
        """Revoke an API key."""
        if key_id in self.config.api_keys:
            del self.config.api_keys[key_id]
            return True
        return False


# Rate limit tiers configuration
RATE_LIMIT_TIERS = {
    "basic": {"requests_per_minute": 30, "burst": 10},
    "standard": {"requests_per_minute": 60, "burst": 20},
    "premium": {"requests_per_minute": 300, "burst": 50},
    "unlimited": {"requests_per_minute": 10000, "burst": 1000},
}
