"""Failure text normalization and reason-code classification for model load errors."""

from .deps import get_resource_tracker


def is_oom_error(error_str: str) -> bool:
    """Check if error is an OOM (Out of Memory) error."""
    oom_indicators = [
        "out of memory",
        "cuda out of memory",
        "oom",
        "cuda oom",
        "memory error",
        "allocation failed",
        "cannot allocate",
    ]
    return any(indicator in error_str for indicator in oom_indicators)


def is_resource_error(error_str: str) -> bool:
    """Check if error is a resource constraint error."""
    resource_indicators = [
        "insufficient",
        "not enough",
        "exceeded",
        "quota",
    ]
    return any(indicator in error_str for indicator in resource_indicators)


def classify_load_failure(error_message: str) -> tuple[str, str]:
    """Normalize failure text and derive a stable reason code."""
    normalized = (error_message or "Unknown error").strip()
    lower = normalized.lower()

    if normalized.startswith("OOM:"):
        return normalized, "oom"
    if normalized.startswith("RESOURCE:"):
        return normalized, "insufficient_resources"
    if is_oom_error(lower):
        return f"OOM:{normalized}", "oom"
    if is_resource_error(lower):
        return f"RESOURCE:{normalized}", "insufficient_resources"
    if "timeout" in lower or "timed out" in lower:
        return normalized, "timeout"
    if "not found" in lower or "no such file" in lower:
        return normalized, "missing_file"
    if "config" in lower or "invalid" in lower:
        return normalized, "config_error"
    return normalized, "unknown"


def resolve_load_failure(model_id: str, fallback: str) -> tuple[str, str]:
    """Prefer any recorded tracker error before falling back to a generic failure."""
    model_info = get_resource_tracker().get_model_info(model_id)
    existing_error = (
        model_info.error_message
        if model_info is not None and model_info.error_message
        else fallback
    )
    return classify_load_failure(existing_error)
