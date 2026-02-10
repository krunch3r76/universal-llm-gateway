"""Model configuration builder for worker initialization."""

from typing import Any

from universal_logging import get_logger

logger = get_logger(__name__)


def build_model_config_for_worker(
    model_id: str,
    model_registry: Any,
    gateway_config: Any,
) -> dict[str, Any]:
    """
    Build model configuration for worker initialization.

    Constructs loader_config, streaming_config, and paths from registry metadata.

    Invariant: ∀ model ∈ registry: ∃ (format ∧ path ∧ loader_config)

    Args:
        model_id: Model identifier from catalog
        model_registry: ModelRegistry instance
        gateway_config: Gateway configuration object

    Returns:
        Configuration dictionary with:
        - name: Model name
        - format: Model format (vllm/gguf)
        - path: Model file path
        - loader_config: Engine loader parameters
        - streaming_config: Stream configuration

    Raises:
        ValueError: If model not found or missing required fields
    """
    # Get model info from registry
    model_info = model_registry.get_model_info(model_id)
    if not model_info:
        raise ValueError(f"Model {model_id} not found in registry")

    # Get model path and loader config
    model_path = model_registry.get_model_path(model_id) or ""
    loader_config = model_registry.get_model_loader_config(model_id) or {}

    # Inject global GGUF configuration if model is GGUF format
    # NOTE: Uses llama-server (native engine) for GGUF inference
    model_format = (model_info.format or "").lower()
    if model_format == "gguf" and hasattr(gateway_config, "gguf"):
        gguf_config = gateway_config.gguf

        # Inject warmup config (structured, per-request-type)
        if "warmup" not in loader_config:
            # Convert Pydantic model to dict for engine kwargs
            warmup_config = gguf_config.warmup
            streaming_cfg = warmup_config.streaming
            non_streaming_cfg = warmup_config.non_streaming
            loader_config["warmup"] = {
                "streaming": {
                    "enabled": streaming_cfg.enabled,
                    "mode": streaming_cfg.mode,
                    "max_tokens": streaming_cfg.max_tokens,
                    "minimal_prompt_tokens": streaming_cfg.minimal_prompt_tokens,
                    "clear_kv_before": streaming_cfg.clear_kv_before,
                    "clear_kv_after": streaming_cfg.clear_kv_after,
                },
                "non_streaming": {
                    "enabled": non_streaming_cfg.enabled,
                    "mode": non_streaming_cfg.mode,
                    "max_tokens": non_streaming_cfg.max_tokens,
                    "minimal_prompt_tokens": non_streaming_cfg.minimal_prompt_tokens,
                    "clear_kv_before": non_streaming_cfg.clear_kv_before,
                    "clear_kv_after": non_streaming_cfg.clear_kv_after,
                },
            }

        # NOTE: disable_kv_cache_clear is REMOVED
        # KV clearing is now per-request-type via warmup.*.clear_kv_before/after

        # Log applied config
        warmup = loader_config.get("warmup", {})
        streaming = warmup.get("streaming", {})
        non_streaming = warmup.get("non_streaming", {})
        s_enabled = streaming.get("enabled")
        s_clear = streaming.get("clear_kv_before")
        ns_enabled = non_streaming.get("enabled")
        ns_clear = non_streaming.get("clear_kv_before")
        logger.info(
            f"🔧 [GGUF] Applied warmup config: "
            f"streaming(enabled={s_enabled}, clear_before={s_clear}), "
            f"non_streaming(enabled={ns_enabled}, clear_before={ns_clear})"
        )

    # Build streaming config from gateway config
    streaming_config: dict[str, Any] = {}
    if hasattr(gateway_config, "streaming") and gateway_config.streaming:
        streaming_config = {
            "chunk_batch_size": getattr(
                gateway_config.streaming, "chunk_batch_size", 3
            ),
        }
    # Allow per-model override in loader_config
    if "streaming" in loader_config:
        streaming_config.update(loader_config.get("streaming", {}))

    # Get engine field from model config (for engine factory dispatch)
    model_config_data = model_registry.get_model_config(model_id) or {}
    info = model_config_data.get("info", {})
    engine = info.get("engine", "unknown")

    # Resolve parallel_slots: controls FifoCapacityGate concurrency.
    # Default is 1 (serial). Models with parallel_slots > 1 in loader_config
    # enable batched concurrent inference (e.g., llama-server -np 8).
    # Invariant: worker gate limit ≡ parallel_slots
    if "parallel_slots" in loader_config:
        effective_slots = loader_config["parallel_slots"]
        logger.info(
            f"[config_builder] Using explicit "
            f"parallel_slots={effective_slots} from model config"
        )
    else:
        effective_slots = 1
        loader_config["parallel_slots"] = effective_slots
        if engine == "native":
            logger.info(
                f"[config_builder] parallel_slots not configured, "
                f"defaulting to {effective_slots} (engine={engine})"
            )

    streaming_config["parallel_slots"] = effective_slots

    # Build final config
    return {
        "name": model_info.name or model_id,
        "format": model_info.format or "unknown",
        "engine": engine,
        "path": model_path,
        "loader_config": loader_config,
        "streaming_config": streaming_config,
    }


def get_worker_timeout(gateway_config: Any) -> float:
    """
    Get worker timeout from gateway config.

    Args:
        gateway_config: Gateway configuration object

    Returns:
        Timeout in seconds (default: 300)
    """
    return float(getattr(gateway_config.process_isolation, "worker_timeout", 300))


def get_force_stop_timeout(gateway_config: Any) -> float:
    """
    Get force stop timeout from gateway config.

    Args:
        gateway_config: Gateway configuration object

    Returns:
        Force stop timeout in seconds (default: 5)
    """
    return float(getattr(gateway_config.process_isolation, "force_stop_timeout", 5))
