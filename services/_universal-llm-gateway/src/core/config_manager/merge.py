"""Deep-merge helpers for model_loaders.yaml configuration updates.

Preserves unspecified fields while applying explicit updates, including
nested dict recursion and controlled int/str/None type transitions used by
hot-reload and ConfigManager.upsert_model.
"""

from typing import Any


def _is_type_transition_allowed(existing: Any, new: Any) -> bool:
    """
    Check if type transition is allowed for configuration merging.

    Allows specific type transitions that are common in configuration updates:
    - None -> any type (common initialization pattern)
    - any type -> None (for optional fields that allow null)
    - int -> str (e.g., 33000000000 -> "33B")
    - str -> int (e.g., "33B" -> 33000000000)

    Args:
        existing: Current value
        new: New value to merge

    Returns:
        True if transition is allowed, False otherwise
    """
    # Allow None -> any type
    if existing is None:
        return True

    # Allow any type -> None (for optional fields that allow null)
    if new is None:
        return True

    # Allow int -> str (for parameters like "33B")
    if isinstance(existing, int) and isinstance(new, str):
        return True

    # Allow str -> int (for parameters like "33B" -> 33000000000)
    if isinstance(existing, str) and isinstance(new, int):
        return True

    return False


def deep_merge_dict(base: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
    """
    Deep merge two dictionaries, preserving existing fields and updating only explicitly provided ones.

    This function implements a merge strategy that:
    1. Preserves existing fields not provided in the update
    2. Updates only explicitly set fields
    3. Merges nested objects recursively
    4. Handles profiles, loader configs, and resources appropriately

    Args:
        base: Base dictionary to merge into
        update: Dictionary with updates to apply

    Returns:
        Merged dictionary with updates applied

    Raises:
        ValueError: If there are type conflicts between base and update values
    """
    result = base.copy()

    for key, value in update.items():
        if key not in result:
            # New key - add it
            result[key] = value
        elif isinstance(result[key], dict) and isinstance(value, dict):
            # Both are dicts - merge recursively
            result[key] = deep_merge_dict(result[key], value)
        elif isinstance(result[key], list) and isinstance(value, list):
            # Both are lists - merge lists (extend with new items)
            # For model configs, we typically want to replace lists rather than extend
            result[key] = value
        elif _is_type_transition_allowed(result[key], value):
            # Allow specific type transitions (None -> any, int <-> str)
            result[key] = value
        elif type(result[key]) is type(value):
            # Same types - update the value
            result[key] = value
        else:
            # Type conflict - raise error
            raise ValueError(
                f"Type conflict for key '{key}': "
                f"existing type {type(result[key]).__name__}, "
                f"update type {type(value).__name__}"
            )

    return result
