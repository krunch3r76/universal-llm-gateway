"""Private configuration for web_tab observation (not generic persona defaults)."""

from __future__ import annotations

import math
import os

_DEFAULT_CDP_EVALUATE_TIMEOUT_S = 10.0
_DEFAULT_CDP_CLEANUP_TIMEOUT_S = 1.0


def resolve_cdp_url(explicit: str | None) -> str:
    """Return configured CDP endpoint URL; explicit wins over env."""
    if explicit and str(explicit).strip():
        return str(explicit).strip()
    url = os.environ.get("WEB_TAB_CDP_URL", "").strip()
    if not url:
        raise ValueError("missing_cdp_url")
    return url


def resolve_cdp_evaluate_timeout_s() -> float:
    """Finite bound for CDP attach + target match + evaluate (seconds).

    Invalid, nonpositive, or nonfinite ``WEB_TAB_CDP_TIMEOUT_S`` values fall
    back to the default (~10s) rather than disabling the bound.
    """
    raw = os.environ.get("WEB_TAB_CDP_TIMEOUT_S", "").strip()
    if not raw:
        return _DEFAULT_CDP_EVALUATE_TIMEOUT_S
    try:
        val = float(raw)
    except ValueError:
        return _DEFAULT_CDP_EVALUATE_TIMEOUT_S
    if not math.isfinite(val) or val <= 0:
        return _DEFAULT_CDP_EVALUATE_TIMEOUT_S
    return val


def resolve_cdp_cleanup_timeout_s() -> float:
    """Finite bound for Playwright ``stop()`` during attach/evaluate cleanup (seconds).

    Invalid, nonpositive, or nonfinite ``WEB_TAB_CDP_CLEANUP_TIMEOUT_S`` values
    fall back to the default (~1s). Cleanup is separate from the operation
    deadline; uncooperative driver shutdown may still leave a process needing
    recovery when stop ignores cancellation.
    """
    raw = os.environ.get("WEB_TAB_CDP_CLEANUP_TIMEOUT_S", "").strip()
    if not raw:
        return _DEFAULT_CDP_CLEANUP_TIMEOUT_S
    try:
        val = float(raw)
    except ValueError:
        return _DEFAULT_CDP_CLEANUP_TIMEOUT_S
    if not math.isfinite(val) or val <= 0:
        return _DEFAULT_CDP_CLEANUP_TIMEOUT_S
    return val
