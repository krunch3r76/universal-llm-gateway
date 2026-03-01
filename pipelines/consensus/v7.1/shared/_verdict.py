"""
Verdict normalization for pipeline compatibility.

Maps string verdicts ("valid"/"invalid", "true"/"false") to boolean.
"""

from __future__ import annotations

from universal_logging import get_logger

logger = get_logger(__name__)


def normalize_verdict(verdict: str | bool | None) -> bool:
    """
    Normalize verdict to boolean.

    Models may return varied verdict formats:
        - Boolean: True, False (already normalized)
        - Strings: "valid", "invalid", "true", "TRUE", "false", "False"
        - None: Missing verdict (defaults to False)

    Args:
        verdict: Verdict value (string, boolean, or None)

    Returns:
        Boolean verdict (True = valid/established, False = invalid/contested)

    Invariant: return type is always bool
    """
    if verdict is None:
        return False
    if isinstance(verdict, bool):
        return verdict
    if isinstance(verdict, str):
        verdict_lower = verdict.lower()
        if verdict_lower == "valid":
            return True
        elif verdict_lower == "invalid":
            return False
        elif verdict_lower == "true":
            return True
        elif verdict_lower == "false":
            return False
    logger.warning(f"Unknown verdict format: {verdict!r}, defaulting to False")
    return False
