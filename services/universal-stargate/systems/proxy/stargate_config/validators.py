"""Pure validators for Stargate configuration sections.

These functions are called during startup validation and must remain side-effect
free so invalid configuration fails fast with deterministic errors.
"""

from typing import Any


def _validate_scheduler_config(scheduler_config: dict[str, Any]) -> None:
    """Validate scheduler bounds and timeout semantics before runtime admission."""
    if "max_queue_size" in scheduler_config:
        max_queue_size = scheduler_config["max_queue_size"]
        if not isinstance(max_queue_size, int) or max_queue_size < 0:
            raise ValueError(
                "scheduler.max_queue_size must be a non-negative integer, "
                f"got: {max_queue_size}"
            )

    if "gateway_check_interval" in scheduler_config:
        check_interval = scheduler_config["gateway_check_interval"]
        if not isinstance(check_interval, int | float) or check_interval <= 0:
            raise ValueError(
                "scheduler.gateway_check_interval must be a positive number, "
                f"got: {check_interval}"
            )

    if "request_timeout" in scheduler_config:
        request_timeout = scheduler_config["request_timeout"]
        if not isinstance(request_timeout, int | float) or request_timeout <= 0:
            raise ValueError(
                "scheduler.request_timeout must be a positive number, "
                f"got: {request_timeout}"
            )


def _validate_request_queue_config(request_queue_config: dict[str, Any]) -> None:
    """Validate request queue sizing, timeout policy, and non-sticky sub-config."""
    if "max_size" in request_queue_config:
        max_size = request_queue_config["max_size"]
        if not isinstance(max_size, int) or max_size < 0:
            raise ValueError(
                "request_queue.max_size must be a non-negative integer, "
                f"got: {max_size}"
            )

    if "max_concurrent_processing" in request_queue_config:
        max_concurrent = request_queue_config["max_concurrent_processing"]
        if not isinstance(max_concurrent, int) or max_concurrent < 1:
            raise ValueError(
                "request_queue.max_concurrent_processing must be a positive integer, "
                f"got: {max_concurrent}"
            )

    # Unified queue timeout (top-level) — used by sticky, non-sticky, master capacity.
    if "queue_timeout" in request_queue_config:
        timeout = request_queue_config["queue_timeout"]
        if not isinstance(timeout, int | float) or timeout <= 0:
            raise ValueError(
                f"request_queue.queue_timeout must be a positive number, got: {timeout}"
            )

    # Upstream retry timeout — budget for retrying retryable 502 (federated upstream).
    if "upstream_retry_timeout" in request_queue_config:
        urt = request_queue_config["upstream_retry_timeout"]
        if not isinstance(urt, int | float) or urt <= 0:
            raise ValueError(
                "request_queue.upstream_retry_timeout must be a positive number, "
                f"got: {urt}"
            )

    # Validate non_sticky sub-config (no queue_timeout — use top-level).
    if "non_sticky" in request_queue_config:
        non_sticky = request_queue_config["non_sticky"]
        if not isinstance(non_sticky, dict):
            raise ValueError("request_queue.non_sticky must be a mapping")

        if "enabled" in non_sticky and not isinstance(non_sticky["enabled"], bool):
            raise ValueError(
                f"request_queue.non_sticky.enabled must be a boolean, "
                f"got: {non_sticky['enabled']}"
            )

        if "max_concurrent" in non_sticky:
            raise ValueError(
                "request_queue.non_sticky.max_concurrent is removed. "
                "Capacity: Gateway FifoCapacityGate (parallel_slots)."
            )


def _validate_eviction_hysteresis(
    config: dict[str, Any], configured_queue_timeout: float
) -> None:
    """Validate routing.eviction_cooldown_s against queue timeout safety bounds."""
    cooldown = config.get("routing", {}).get("eviction_cooldown_s")
    if cooldown is None:
        return
    cooldown = float(cooldown)
    if cooldown < 30.0:
        raise ValueError(f"routing.eviction_cooldown_s={cooldown} too low (min 30s)")
    if cooldown > configured_queue_timeout:
        raise ValueError(
            f"routing.eviction_cooldown_s={cooldown} exceeds queue timeout "
            f"({configured_queue_timeout}s). Requests would always time out."
        )


def _validate_routing_capacity(config: dict[str, Any]) -> None:
    """Reject removed routing capacity keys now owned by gateway-side controls."""
    capacity = config.get("routing", {}).get("scoring", {}).get("capacity", {})
    if "max_concurrent_per_gateway" in capacity:
        raise ValueError(
            "routing.scoring.capacity.max_concurrent_per_gateway is REMOVED. "
            "Capacity is now managed by Gateway's FifoCapacityGate (parallel_slots)."
        )


def _validate_model_routing_config(model_routing_config: dict[str, Any]) -> None:
    """Validate sticky routing defaults and per-model override map constraints."""
    if "default_sticky" in model_routing_config and not isinstance(
        model_routing_config["default_sticky"], bool
    ):
        raise ValueError(
            "model_routing.default_sticky must be a boolean, "
            f"got: {model_routing_config['default_sticky']}"
        )

    if "sticky_overrides" in model_routing_config:
        overrides = model_routing_config["sticky_overrides"]
        if not isinstance(overrides, dict):
            raise ValueError(
                "model_routing.sticky_overrides must be a mapping of model_id->bool"
            )
        for model_id, sticky in overrides.items():
            if not isinstance(model_id, str) or not model_id:
                raise ValueError(
                    "model_routing.sticky_overrides keys must be non-empty strings"
                )
            if not isinstance(sticky, bool):
                raise ValueError(
                    f"model_routing.sticky_overrides['{model_id}'] must be boolean, "
                    f"got: {sticky}"
                )
