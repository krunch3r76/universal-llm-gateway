"""
Authorization module for Universal Stargate Proxy

Implements OpenAI API-compliant authorization using API keys.
Supports multiple authorization methods:
1. Bearer token in Authorization header
2. API key in X-API-Key header
3. API key as query parameter (for compatibility)
"""

import hashlib
import ipaddress
import os
import secrets

from fastapi import Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.security.utils import get_authorization_scheme_param
from universal_logging import get_logger

from .core.errors import AuthErrorBuilder

logger = get_logger(__name__)


class APIKeyManager:
    """Manages API keys and validation"""

    def __init__(self, config: dict):
        self.enabled = config.get("enabled", True)
        self.valid_keys: set[str] = set()
        self.key_hashes: set[str] = set()
        self.require_auth_for_all = config.get("require_auth_for_all", True)
        self.exempt_endpoints = set(
            config.get(
                "exempt_endpoints", ["/", "/health", "/docs", "/redoc", "/openapi.json"]
            )
        )

        # IP whitelisting configuration
        self.ip_whitelist_enabled = config.get("ip_whitelist", {}).get("enabled", True)
        self.whitelisted_ips: set[ipaddress.IPv4Network] = set()
        self.whitelisted_networks: set[ipaddress.IPv4Network] = set()

        # Load API keys from configuration
        self._load_api_keys(config)

        # Load IP whitelist
        self._load_ip_whitelist(config)

        # Generate default key if none exist
        if not self.valid_keys and not self.key_hashes:
            self._generate_default_key()

    def _load_ip_whitelist(self, config: dict):
        """Load IP whitelist from configuration"""
        if not self.ip_whitelist_enabled:
            return

        ip_config = config.get("ip_whitelist", {})

        # Load individual IPs
        individual_ips = ip_config.get("individual_ips", [])
        for ip in individual_ips:
            try:
                # Handle both single IPs and CIDR notation
                if "/" in ip:
                    self.whitelisted_networks.add(
                        ipaddress.IPv4Network(ip, strict=False)
                    )
                else:
                    self.whitelisted_ips.add(
                        ipaddress.IPv4Network(f"{ip}/32", strict=False)
                    )
                logger.info(f"Whitelisted IP: {ip}")
            except ValueError as e:
                logger.warning(f"Invalid IP address in whitelist: {ip} - {e}")

        # Load network ranges
        network_ranges = ip_config.get("network_ranges", [])
        for network in network_ranges:
            try:
                self.whitelisted_networks.add(
                    ipaddress.IPv4Network(network, strict=False)
                )
                logger.info(f"Whitelisted network: {network}")
            except ValueError as e:
                logger.warning(f"Invalid network range in whitelist: {network} - {e}")

        # Default localhost and local networks if none specified
        if not individual_ips and not network_ranges:
            default_whitelist = [
                "127.0.0.1/32",  # localhost
                "::1/128",  # IPv6 localhost
                "10.0.0.0/8",  # Private network
                "172.16.0.0/12",  # Private network
                "192.168.0.0/16",  # Private network
            ]

            for ip in default_whitelist:
                try:
                    if "::" in ip:
                        # Skip IPv6 for now, focus on IPv4
                        continue
                    self.whitelisted_networks.add(
                        ipaddress.IPv4Network(ip, strict=False)
                    )
                    logger.info(f"Added default whitelist: {ip}")
                except ValueError as e:
                    logger.warning(f"Failed to add default whitelist {ip}: {e}")

    def _load_api_keys(self, config: dict):
        """Load API keys from configuration"""
        # Load from environment variables
        env_keys = os.getenv("STARGATE_API_KEYS", "")
        if env_keys:
            for key in env_keys.split(","):
                key = key.strip()
                if key:
                    self.valid_keys.add(key)
                    logger.info(f"Loaded API key from environment: {key[:8]}...")

        # Load from config file
        config_keys = config.get("api_keys", [])
        for key in config_keys:
            if isinstance(key, dict):
                # Support for key metadata
                key_value = key.get("key", "")
                if key_value:
                    self.valid_keys.add(key_value)
                    logger.info(f"Loaded API key from config: {key_value[:8]}...")
            elif isinstance(key, str):
                self.valid_keys.add(key)
                logger.info(f"Loaded API key from config: {key[:8]}...")

        # Load hashed keys for security
        hashed_keys = config.get("hashed_api_keys", [])
        for key_hash in hashed_keys:
            self.key_hashes.add(key_hash)
            logger.info(f"Loaded hashed API key: {key_hash[:8]}...")

    def _generate_default_key(self):
        """Generate a default API key for initial setup"""
        default_key = f"sk-stargate-{secrets.token_urlsafe(32)}"
        self.valid_keys.add(default_key)
        logger.warning(f"Generated default API key: {default_key}")
        logger.warning("IMPORTANT: Change this default key in production!")

    def validate_key(self, api_key: str) -> bool:
        """Validate an API key"""
        if not self.enabled:
            return True

        if not api_key:
            return False

        # Check direct key match
        if api_key in self.valid_keys:
            return True

        # Check hashed key match
        key_hash = hashlib.sha256(api_key.encode()).hexdigest()
        if key_hash in self.key_hashes:
            return True

        return False

    def is_endpoint_exempt(self, path: str) -> bool:
        """Check if endpoint is exempt from authorization"""
        return path in self.exempt_endpoints

    def is_ip_whitelisted(self, client_ip: str) -> bool:
        """Check if client IP is whitelisted"""
        if not self.ip_whitelist_enabled:
            return False

        try:
            # Handle both IPv4 and IPv6
            if ":" in client_ip:
                # IPv6 address - for now, just check if it's localhost
                if client_ip in ["::1", "::ffff:127.0.0.1"]:
                    return True
                return False

            # IPv4 address
            client_addr = ipaddress.IPv4Address(client_ip)

            # Check individual IPs
            for whitelisted_ip in self.whitelisted_ips:
                if client_addr in whitelisted_ip:
                    return True

            # Check network ranges
            for network in self.whitelisted_networks:
                if client_addr in network:
                    return True

            return False
        except ValueError:
            # Invalid IP address
            logger.warning(f"Invalid client IP address: {client_ip}")
            return False


class StargateAuthBearer(HTTPBearer):
    """Custom HTTPBearer that supports multiple authorization methods"""

    def __init__(self, api_key_manager: APIKeyManager, auto_error: bool = True):
        self.api_key_manager = api_key_manager
        super().__init__(auto_error=auto_error)

    async def __call__(self, request: Request) -> HTTPAuthorizationCredentials | None:
        """Extract and validate authorization from request"""

        # Check if endpoint is exempt
        if self.api_key_manager.is_endpoint_exempt(request.url.path):
            return None

        # Try multiple authorization methods
        api_key = self._extract_api_key(request)

        if not api_key:
            if self.auto_error:
                raise AuthErrorBuilder.api_key_required()
            return None

        # Validate the API key
        if not self.api_key_manager.validate_key(api_key):
            raise AuthErrorBuilder.invalid_api_key()

        # Return credentials for compatibility
        return HTTPAuthorizationCredentials(scheme="Bearer", credentials=api_key)

    def _extract_api_key(self, request: Request) -> str | None:
        """Extract API key from request using multiple methods"""

        # Method 1: Authorization header (Bearer token)
        auth_header = request.headers.get("Authorization")
        if auth_header:
            scheme, credentials = get_authorization_scheme_param(auth_header)
            if scheme.lower() == "bearer" and credentials:
                return credentials

        # Method 2: X-API-Key header
        api_key_header = request.headers.get("X-API-Key")
        if api_key_header:
            return api_key_header

        # Method 3: Query parameter (for compatibility)
        api_key_param = request.query_params.get("api_key")
        if api_key_param:
            return api_key_param

        return None


def _extract_api_key_from_request(request: Request) -> str | None:
    """Extract API key from request using multiple methods"""

    # Method 1: Authorization header (Bearer token)
    auth_header = request.headers.get("Authorization")
    if auth_header:
        scheme, credentials = get_authorization_scheme_param(auth_header)
        if scheme.lower() == "bearer" and credentials:
            return credentials

    # Method 2: X-API-Key header
    api_key_header = request.headers.get("X-API-Key")
    if api_key_header:
        return api_key_header

    # Method 3: Query parameter (for compatibility)
    api_key_param = request.query_params.get("api_key")
    if api_key_param:
        return api_key_param

    return None


def create_auth_dependency(api_key_manager: APIKeyManager):
    """Create FastAPI dependency for authorization"""

    async def get_current_user(request: Request):
        """FastAPI dependency for authorization"""
        # If authorization is disabled, allow all requests
        if not api_key_manager.enabled:
            return {
                "authenticated": True,
                "api_key": None,
                "auth_method": "auth_disabled",
            }

        # Check if endpoint is exempt
        if api_key_manager.is_endpoint_exempt(request.url.path):
            return {
                "authenticated": True,
                "api_key": None,
                "auth_method": "exempt_endpoint",
            }

        # Get client IP
        client_ip = request.client.host if request.client else "unknown"

        # Check if IP is whitelisted
        if api_key_manager.is_ip_whitelisted(client_ip):
            # logger.info(f"Request from whitelisted IP: {client_ip}")
            return {
                "authenticated": True,
                "api_key": None,
                "auth_method": "ip_whitelist",
                "client_ip": client_ip,
            }

        # Extract API key from request
        api_key = _extract_api_key_from_request(request)

        if not api_key:
            raise AuthErrorBuilder.api_key_required()

        # Validate the API key
        if not api_key_manager.validate_key(api_key):
            raise AuthErrorBuilder.invalid_api_key()

        return {
            "authenticated": True,
            "api_key": api_key,
            "key_prefix": api_key[:8] + "..." if api_key else None,
            "auth_method": "api_key",
            "client_ip": client_ip,
        }

    return get_current_user


def create_optional_auth_dependency(api_key_manager: APIKeyManager):
    """Create optional FastAPI dependency for authorization (doesn't fail if no key provided)"""

    async def get_optional_user(request: Request):
        """FastAPI dependency for optional authorization"""
        # If authorization is disabled, allow all requests
        if not api_key_manager.enabled:
            return {
                "authenticated": True,
                "api_key": None,
                "auth_method": "auth_disabled",
            }

        # Check if endpoint is exempt
        if api_key_manager.is_endpoint_exempt(request.url.path):
            return {
                "authenticated": True,
                "api_key": None,
                "auth_method": "exempt_endpoint",
            }

        # Get client IP
        client_ip = request.client.host if request.client else "unknown"

        # Check if IP is whitelisted
        if api_key_manager.is_ip_whitelisted(client_ip):
            # Don't log per-request to avoid blocking I/O
            # logger.info(f"Request from whitelisted IP: {client_ip}")
            return {
                "authenticated": True,
                "api_key": None,
                "auth_method": "ip_whitelist",
                "client_ip": client_ip,
            }

        # Extract API key from request
        api_key = _extract_api_key_from_request(request)

        if not api_key:
            return {
                "authenticated": False,
                "api_key": None,
                "auth_method": "none",
                "client_ip": client_ip,
            }

        is_valid = api_key_manager.validate_key(api_key)

        return {
            "authenticated": is_valid,
            "api_key": api_key,
            "key_prefix": api_key[:8] + "..." if api_key else None,
            "auth_method": "api_key" if is_valid else "invalid_key",
            "client_ip": client_ip,
        }

    return get_optional_user


class AuthorizationManager:
    """Main authorization manager for the Stargate proxy"""

    def __init__(self, config: dict):
        self.api_key_manager = APIKeyManager(config)
        self.auth_dependency = create_auth_dependency(self.api_key_manager)
        self.optional_auth_dependency = create_optional_auth_dependency(
            self.api_key_manager
        )

        # Expose IP whitelist settings for easy access
        self.ip_whitelist_enabled = self.api_key_manager.ip_whitelist_enabled
        self.whitelisted_networks = self.api_key_manager.whitelisted_networks

    def is_enabled(self) -> bool:
        """Check if authorization is enabled"""
        return self.api_key_manager.enabled

    def get_auth_dependency(self):
        """Get the main authorization dependency"""
        return self.auth_dependency

    def get_optional_auth_dependency(self):
        """Get the optional authorization dependency"""
        return self.optional_auth_dependency

    def validate_request(self, request: Request) -> bool:
        """Validate a request's authorization"""
        if not self.api_key_manager.enabled:
            return True

        if self.api_key_manager.is_endpoint_exempt(request.url.path):
            return True

        api_key = self._extract_api_key(request)
        return self.api_key_manager.validate_key(api_key)

    def _extract_api_key(self, request: Request) -> str | None:
        """Extract API key from request"""
        # Try Authorization header
        auth_header = request.headers.get("Authorization")
        if auth_header:
            scheme, credentials = get_authorization_scheme_param(auth_header)
            if scheme.lower() == "bearer" and credentials:
                return credentials

        # Try X-API-Key header
        api_key_header = request.headers.get("X-API-Key")
        if api_key_header:
            return api_key_header

        # Try query parameter
        api_key_param = request.query_params.get("api_key")
        if api_key_param:
            return api_key_param

        return None
