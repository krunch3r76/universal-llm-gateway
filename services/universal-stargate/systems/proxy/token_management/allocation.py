"""
Pure functions for token allocation logic.
All functions are stateless and testable.
"""

from universal_logging import get_logger

logger = get_logger(__name__)


def apply_safety_buffer(raw_generation_space: int, safety_buffer: int) -> int:
    """
    Apply safety buffer to raw generation space to prevent context overruns.

    Args:
        raw_generation_space: Maximum tokens available from token counting
        safety_buffer: Number of tokens to reserve as safety margin

    Returns:
        Available generation space after safety buffer (≥ 0)
    """
    return max(0, raw_generation_space - safety_buffer)


def compute_final_max_tokens(
    available_generation_space: int,
    user_requested_max_tokens: int | None,
    user_explicitly_specified_max_tokens: bool,
    conservative_allocation_ratio: float,
) -> int | None:
    """
    Compute final max_tokens with user preference handling.

    This function implements the core allocation strategy:
    - If user explicitly specified max_tokens: honor it (cap to available space if > 0)
    - If user did NOT specify max_tokens: auto-allocate using conservative ratio

    Args:
        available_generation_space: Tokens available after safety buffer
        user_requested_max_tokens: User's requested max_tokens (if any)
        user_explicitly_specified_max_tokens: Whether user explicitly set max_tokens
        conservative_allocation_ratio: Ratio for auto-allocation (e.g., 0.75 = 75%)

    Returns:
        Final max_tokens to use (None if no allocation)
    """
    if user_explicitly_specified_max_tokens and user_requested_max_tokens is not None:
        # MODE 1: User explicitly specified max_tokens - respect but cap to available space
        if available_generation_space > 0:
            return min(user_requested_max_tokens, available_generation_space)
        else:
            # No space available, but respect user's choice
            return user_requested_max_tokens
    else:
        # MODE 2: Auto-allocation using conservative ratio
        if available_generation_space > 0:
            return int(available_generation_space * conservative_allocation_ratio)
        else:
            # No space available, no auto-allocation
            return None
