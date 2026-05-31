"""Gateway client configuration and metadata classes."""

from dataclasses import dataclass
from typing import Any


@dataclass
class GatewayConfig:
    """Configuration for a gateway instance.

    Supports two transport modes (mutually exclusive):
    - TCP: base_url = "http://host:port" (legacy)
    - Unix socket: socket_path = "/path/to/socket"

    Invariant: (socket_path ≠ None) ⟺ (transport = unix_socket)
    """

    base_url: str  # Used for HTTP path construction even with Unix sockets
    name: str = "gateway"
    enabled: bool = True
    timeout: float = 30.0
    max_retries: int = 3
    retry_delay: float = 1.0
    headers: dict[str, str] | None = None
    api_key: str | None = None
    connectivity_timeout: float | None = None
    health_timeout: float | None = None
    capabilities: dict[str, str] | None = None
    socket_path: str | None = None  # Unix socket path (if set, overrides TCP)

    def __post_init__(self):
        """Validate and process configuration."""
        if not self.base_url and not self.socket_path:
            raise ValueError(
                "Gateway config requires either 'base_url' or 'socket_path'"
            )

        # Auto-inject API key into headers if provided
        if self.api_key:
            if self.headers is None:
                self.headers = {}
            if "authorization" not in self.headers:
                self.headers["authorization"] = f"Bearer {self.api_key}"

    @property
    def uses_unix_socket(self) -> bool:
        """True if this gateway uses Unix socket transport."""
        return self.socket_path is not None


@dataclass
class ModelMetadata:
    """Model metadata from gateway API"""

    id: str
    model_type: str
    input_schema: str
    parameter_defaults: dict[str, Any]
    supported_parameters: list[str]
    middleware_config: dict[str, Any]
    enabled: bool
    loader_type: str
    path: str
    ram_usage: int = 0
    vram_usage: int = 0
    context_length: int | None = None
    sticky: bool = True
    capabilities: dict[str, Any] | None = None

    @property
    def format(self) -> str:
        """Alias for model_type for backward compatibility"""
        return self.model_type

    @classmethod
    def from_api_response(cls, data: dict[str, Any]) -> "ModelMetadata":
        """Create ModelMetadata from API response"""
        # Determine loader_type from loader_config if available
        loader_type = data.get("loader_type", "unknown")
        if loader_type == "unknown" and "loader_config" in data:
            loader_config = data["loader_config"]
            if "n_gpu_layers" in loader_config:
                n_gpu = loader_config.get("n_gpu_layers", 0)
                if n_gpu == 0:
                    loader_type = "llama_cpp_cpu"
                elif n_gpu == -1:
                    loader_type = "llama_cpp_gpu"
                else:
                    loader_type = "llama_cpp_hybrid"

        caps = data.get("capabilities", {})
        input_schema = (
            caps.get("input_schema") if isinstance(caps, dict) else None
        ) or data.get("input_schema", "prompt")

        return cls(
            id=data["id"],
            model_type=data.get("model_type")
            or data.get("format")
            or data.get("model_family", "default"),
            input_schema=input_schema,
            parameter_defaults=data.get("parameter_defaults", {}),
            supported_parameters=data.get("supported_parameters", []),
            middleware_config=data.get("middleware_config", {}),
            enabled=data.get("enabled", True),
            loader_type=loader_type,
            path=data.get("path", ""),
            ram_usage=data.get("ram_usage", 0),
            vram_usage=data.get("vram_usage", 0),
            context_length=data.get("context_length")
            or data.get("training_context_length")
            or (
                caps.get("limits", {}).get("max_context_length")
                if isinstance(caps, dict)
                else None
            ),
            sticky=bool(data.get("middleware_config", {}).get("sticky", True)),
            capabilities=caps if isinstance(caps, dict) and caps else None,
        )
