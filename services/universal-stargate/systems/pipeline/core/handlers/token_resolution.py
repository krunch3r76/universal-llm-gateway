"""
Token budget resolution for pipeline handlers.

Provides _resolve_max_tokens and _constrained_tokens as a mixin for BaseHandler.
Consults pipeline token_defaults and applies constrained_multiplier when
expansion_safe=false (epistemically bounded questions).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from universal_logging import get_logger

if TYPE_CHECKING:
    from ..schemas import StepConfig
    from .protocol import PipelineContext

logger = get_logger(__name__)

# Token categories that map to token_defaults keys
TOKEN_CATEGORIES = (
    "analyze", "classify", "answer", "enrich",
    "verify", "post_process", "reseed", "review", "consult",
)


def resolve_max_tokens(
    step: StepConfig,
    context: PipelineContext,
    *,
    handler_default: int | None = None,
) -> int | None:
    """
    Resolve max_tokens with token_defaults and constrained_multiplier.

    Hierarchy:
    1. Step generation_parameters (explicit override)
    2. Pipeline token_defaults (category-based)
    3. handler_default (handler's own fallback)
    4. None (use model default)

    When expansion_safe=false, applies constrained_multiplier to limit
    response length for epistemically bounded questions.
    """
    # Priority 1: Explicit step override
    if "max_tokens" in step.generation_parameters:
        base_tokens = step.generation_parameters["max_tokens"]
    else:
        # Priority 2: Token defaults by category
        token_defaults = getattr(context.pipeline, "token_defaults", None)
        category = infer_token_category(step) if token_defaults else None
        if token_defaults and category and category in token_defaults:
            base_tokens = token_defaults[category]
        elif handler_default is not None:
            # Priority 3: Handler's own default
            base_tokens = handler_default
        else:
            return None

    # Apply constrained_multiplier if expansion_safe=false
    return _apply_constraint(base_tokens, context)


def constrained_tokens(
    base: int,
    context: PipelineContext,
) -> int:
    """
    Apply constrained_multiplier to a token budget if expansion_safe=false.

    Lightweight helper for internal sub-calls (decompose, verify-per-claim)
    where the handler knows its own budget. Use resolve_max_tokens() for
    top-level handler calls that should consult token_defaults.
    """
    return _apply_constraint(base, context)


def _apply_constraint(base_tokens: int, context: PipelineContext) -> int:
    """Apply constrained_multiplier when expansion_safe=false."""
    expansion_safe_output = context.get_output("classify_expansion_safety")
    if expansion_safe_output and expansion_safe_output.json:
        expansion_safe = expansion_safe_output.json.get("expansion_safe", True)
        if not expansion_safe:
            token_defaults = getattr(context.pipeline, "token_defaults", None)
            if token_defaults:
                multiplier = token_defaults.get("constrained_multiplier", 1.0)
                constrained = int(base_tokens * multiplier)
                logger.info(
                    "Token constraint: %d → %d (multiplier=%.2f)",
                    base_tokens, constrained, multiplier,
                )
                return constrained
    return base_tokens


def infer_token_category(step: StepConfig) -> str | None:
    """
    Infer token category from step name or type.

    Categories: analyze, classify, answer, enrich, verify, post_process, reseed,
    review, consult.
    """
    name_lower = step.name.lower()
    for cat in TOKEN_CATEGORIES:
        if cat in name_lower or cat.replace("_", "") in name_lower:
            return cat

    # Fallback: check step type
    type_lower = step.type.lower()
    for cat in ("analyze", "classify", "answer", "enrich", "verify"):
        if cat in type_lower:
            return cat
    if "post_process" in type_lower or "synthesize" in type_lower:
        return "post_process"

    return None
