"""
Generation parameter assembly and whitelist filtering.

ALLOWED_GENERATION_PARAMS defines the set of parameters the pipeline layer is
permitted to send to Stargate. Parameters outside this set are stripped and
logged — they indicate either a misconfigured step or an API mismatch between
pipeline yaml and the proxy layer.

The assembly order in _build_generation_params is intentional:
  resolved_config (handler-computed) → step.generation_parameters (author overrides)
Max_tokens is excluded from step overrides to protect the constrained_multiplier
invariant: once the executor has computed an epistemic bound, a step-level
override would silently bypass it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from universal_logging import get_logger

if TYPE_CHECKING:
    from ...schemas import StepConfig

logger = get_logger(__name__)

# Supported generation parameters (whitelist)
ALLOWED_GENERATION_PARAMS: set[str] = {
    "temperature",
    "max_tokens",
    "top_p",
    "top_k",
    "stop",
    "response_format",
    "tool_choice",
    "seed",
    "stream",
    "presence_penalty",
    "frequency_penalty",
}


def _build_generation_params(
    step: StepConfig,
    resolved_config: dict[str, Any],
) -> tuple[dict[str, Any], set[str]]:
    """Build generation parameters with filtering.

    Hierarchy:
    1. Start with resolved config (from handler logic, token_defaults, etc.)
    2. Overlay step.generation_parameters (explicit overrides)
    3. Filter to whitelist

    response_format merging from prompt.json_schema preserved for compatibility.

    Token constraint invariant: max_tokens from resolved_config already includes
    constrained_multiplier when expansion_safe=false, so explicit step values
    must not override it (would bypass epistemic boundedness constraint).

    Returns:
        Tuple of (filtered_params, removed_keys). removed_keys is empty
        when no filtering occurred.
    """
    params: dict[str, Any] = {
        k: v
        for k, v in resolved_config.items()
        if k in {"temperature", "max_tokens"} and v is not None
    }

    step_overrides = {
        k: v for k, v in step.generation_parameters.items() if k != "max_tokens"
    }
    params.update(step_overrides)

    if resolved_config.get("json_schema"):
        params.setdefault(
            "response_format",
            {"type": "json_object", "schema": resolved_config["json_schema"]},
        )

    filtered = {k: v for k, v in params.items() if k in ALLOWED_GENERATION_PARAMS}

    removed = set(params.keys()) - set(filtered.keys())
    if removed:
        logger.warning(
            f"Filtered unsupported generation params: {removed}. "
            f"Allowed: {ALLOWED_GENERATION_PARAMS}"
        )

    return filtered, removed
