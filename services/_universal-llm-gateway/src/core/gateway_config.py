"""Gateway configuration models"""

from typing import Literal

from pydantic import BaseModel, Field


class ServerConfig(BaseModel):
    """Server configuration settings"""

    host: str = "0.0.0.0"
    port: int = 9998
    workers: int = 1
    timeout: int = 300


class LoggingConfig(BaseModel):
    """Logging configuration settings"""

    level: str = "INFO"
    format: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    file: str = "logs/gateway.log"
    max_size: str = "10MB"
    backup_count: int = 5


class ProcessIsolationConfig(BaseModel):
    """Process isolation configuration settings"""

    enabled: bool = True
    socket_dir: str = "/tmp/universal-protocol"
    max_workers_per_model: int = 1
    max_concurrent_workers: int = 1
    worker_timeout: int = Field(
        300, ge=60, le=3600
    )  # Main inference timeout (1-60 minutes)
    token_counting_timeout: int = Field(
        60, ge=5, le=300
    )  # Timeout for token counting operations (5-300 seconds)
    model_info_timeout: int = Field(
        5, ge=5, le=300
    )  # Timeout for get_model_info operations (5-300 seconds)
    shutdown_timeout: int = Field(
        10, ge=5, le=300
    )  # Timeout for graceful shutdown (5-300 seconds)
    force_stop_timeout: int = Field(
        5, ge=1, le=60
    )  # Timeout for force killing processes (1-60 seconds)
    startup_timeout: int = Field(
        60, ge=60, le=1800
    )  # Timeout for worker startup (1-30 minutes)
    sigterm_wait_timeout: int = Field(
        5, ge=1, le=30
    )  # Wait time for SIGTERM termination (1-30 seconds)
    sigkill_wait_timeout: int = Field(
        3, ge=1, le=10
    )  # Wait time for SIGKILL termination (1-10 seconds)
    use_hierarchical_timeouts: bool = (
        True  # Specific timeouts fall back to worker_timeout
    )
    cleanup_on_exit: bool = True
    # Model unloading configuration
    fast_model_unload: bool = Field(
        True, description="Use SIGKILL immediately for fast model unloading"
    )
    skip_sigterm_on_unload: bool = Field(
        True, description="Skip SIGTERM and go directly to SIGKILL for model unloading"
    )


class WorkerProcessesConfig(BaseModel):
    """Worker process configuration settings"""

    use_inference_djinn_venv: bool = False
    inference_djinn_venv_path: str = (
        "/mnt/torus/projects/inference-djinn/.djinn-venv/bin/python"
    )
    detached: bool = True


class ModelRegistryValidationConfig(BaseModel):
    """Model registry validation settings"""

    check_paths: bool = True
    check_formats: bool = True


class ModelRegistryConfig(BaseModel):
    """Model registry configuration settings"""

    auto_discovery: bool = False
    config_file: str = "config/model_loaders.yaml"
    validation: ModelRegistryValidationConfig = Field(
        default_factory=ModelRegistryValidationConfig
    )


class ModelsConfig(BaseModel):
    """Models configuration settings"""

    auto_load_on_request: bool = False  # Require explicit model loading by default


class CachingConfig(BaseModel):
    """Caching configuration settings"""

    enabled: bool = True
    max_size: int = 1000
    ttl: int = 3600  # 1 hour


class RateLimitingConfig(BaseModel):
    """Rate limiting configuration settings"""

    enabled: bool = True
    requests_per_minute: int = 60
    burst_size: int = 10


class HealthCheckConfig(BaseModel):
    """Health check configuration settings"""

    enabled: bool = True
    interval: int = 30
    timeout: int = 5  # Legacy field, kept for compatibility
    health_check_timeout: float = Field(
        5.0, description="Timeout for async ping-based health checks in seconds"
    )


class SecurityConfig(BaseModel):
    """Security configuration settings"""

    allowed_origins: list[str] = ["*"]


class ManagementAPIConfig(BaseModel):
    """Management API configuration settings"""

    enabled: bool = Field(
        False, description="Enable model configuration management API endpoints"
    )
    require_token: bool = Field(
        True,
        description=(
            "Require authentication token for management endpoints "
            "(disable for dev/testing)"
        ),
    )
    token: str | None = Field(
        None,
        description=(
            "Authentication token for management API (optional if require_token=false)"
        ),
    )


class HotReloadConfig(BaseModel):
    """Hot reload configuration settings"""

    enabled: bool = Field(
        False, description="Enable automatic YAML configuration hot reload"
    )
    watch_directory: str = Field(
        "config", description="Directory to watch for configuration changes"
    )
    debounce_ms: int = Field(
        500,
        ge=100,
        le=5000,
        description="Debounce delay in milliseconds for file changes",
    )
    recursive: bool = Field(True, description="Watch subdirectories recursively")
    supported_formats: list[str] = Field(
        default=[".yaml", ".yml", ".json"],
        description="File formats to watch for changes",
    )
    log_level: str = Field("info", description="Logging level for hot reload events")

    # Security settings
    allowed_paths: list[str] = Field(
        default=["config"], description="Allowed watch directories for security"
    )
    require_authentication: bool = Field(
        False, description="Require authentication for manual reloads"
    )
    max_file_size_mb: int = Field(
        10, ge=1, le=100, description="Maximum file size for reload in MB"
    )


class StreamingConfig(BaseModel):
    """Streaming configuration settings"""

    timeout: int = Field(
        300, ge=30, le=3600, description="Default streaming timeout in seconds"
    )
    cancellation_check_interval: int = Field(
        1,
        ge=1,
        le=10,
        description="Interval in seconds for checking cancellation signals",
    )
    enable_cancellation: bool = Field(
        True, description="Enable streaming cancellation support"
    )


class ResourceGuardConfig(BaseModel):
    """Configuration for pre-flight resource checking (OOM prevention)."""

    enabled: bool = Field(True, description="Enable pre-flight resource checks")
    vram_safety_margin: float = Field(
        0.0,
        ge=0.0,
        le=1.0,
        description="Fraction of required VRAM to reserve as buffer (0.0-1.0)",
    )
    ram_safety_margin: float = Field(
        0.0,
        ge=0.0,
        le=1.0,
        description="Fraction of required RAM to reserve as buffer (0.0-1.0)",
    )
    min_vram_margin_mb: int = Field(
        0, ge=0, description="Minimum absolute VRAM margin in MB"
    )
    min_ram_margin_mb: int = Field(
        0, ge=0, description="Minimum absolute RAM margin in MB"
    )


class WarmupModeConfig(BaseModel):
    """Configuration for warmup behavior per request type.

    Warmup modes:
    - minimal: Use dummy prompt ("WARMUP " repeated), activates GPU kernels
    - full_prompt: Use actual user request, pre-fills KV cache

    KV Cache Clearing:
    - clear_kv_before: Clear before warmup (ensures fresh start)
    - clear_kv_after: Clear after warmup (removes warmup pollution before generation)

    Invariant: ∀ warmup: (enabled ∧ mode = full_prompt) ⟹ request_data_passed
    Invariant: ∀ request: (clear_kv_before ∨ clear_kv_after) ⟹ fresh_generation
    """

    enabled: bool = Field(
        False, description="Enable warmup before inference for this request type"
    )
    mode: Literal["minimal", "full_prompt"] = Field(
        "minimal",
        description=(
            "Warmup mode: 'minimal' uses dummy prompt for GPU activation, "
            "'full_prompt' uses actual user request to pre-fill KV cache"
        ),
    )
    max_tokens: int | None = Field(
        None,
        description=(
            "Override max_tokens during warmup. "
            "None = use 1 for minimal mode, request's max_tokens for full_prompt"
        ),
    )
    minimal_prompt_tokens: int = Field(
        100,
        description=(
            "Approximate token count for minimal mode dummy prompt. "
            "Controls 'WARMUP ' repetitions. Only used when mode='minimal'"
        ),
    )
    clear_kv_before: bool = Field(
        True,
        description=(
            "Clear KV cache before warmup. "
            "Ensures fresh start, removes previous request's data."
        ),
    )
    clear_kv_after: bool = Field(
        False,
        description=(
            "Clear KV cache after warmup but before generation. "
            "Use when warmup pollution is undesirable (minimal mode)."
        ),
    )


class WarmupConfig(BaseModel):
    """Warmup configuration with separate controls for streaming and non-streaming.

    Invariant: ∀ request_type ∈ {streaming, non_streaming}: config_independent
    """

    streaming: WarmupModeConfig = Field(
        default_factory=WarmupModeConfig,
        description="Warmup settings for streaming requests",
    )
    non_streaming: WarmupModeConfig = Field(
        default_factory=WarmupModeConfig,
        description="Warmup settings for non-streaming requests",
    )


class GGUFEngineConfig(BaseModel):
    """GGUF engine-specific configuration (llama-cpp-python).

    Breaking changes:
    - DELETE enable_kv_warmup (replaced by warmup.*.enabled)
    - DELETE disable_kv_cache_clear (replaced by warmup.*.clear_kv_before/after)
    """

    warmup: WarmupConfig = Field(
        default_factory=WarmupConfig,
        description="Per-request-type warmup and KV cache configuration",
    )


class GatewayConfig(BaseModel):
    """Main gateway configuration model"""

    server: ServerConfig = Field(default_factory=ServerConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    process_isolation: ProcessIsolationConfig = Field(
        default_factory=ProcessIsolationConfig
    )
    worker_processes: WorkerProcessesConfig = Field(
        default_factory=WorkerProcessesConfig
    )
    model_registry: ModelRegistryConfig = Field(default_factory=ModelRegistryConfig)
    models: ModelsConfig = Field(default_factory=ModelsConfig)
    caching: CachingConfig = Field(default_factory=CachingConfig)
    rate_limiting: RateLimitingConfig = Field(default_factory=RateLimitingConfig)
    health_check: HealthCheckConfig = Field(default_factory=HealthCheckConfig)
    management_api: ManagementAPIConfig = Field(default_factory=ManagementAPIConfig)
    hot_reload: HotReloadConfig = Field(default_factory=HotReloadConfig)
    streaming: StreamingConfig = Field(default_factory=StreamingConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    resource_guard: ResourceGuardConfig = Field(default_factory=ResourceGuardConfig)
    gguf: GGUFEngineConfig = Field(default_factory=GGUFEngineConfig)
