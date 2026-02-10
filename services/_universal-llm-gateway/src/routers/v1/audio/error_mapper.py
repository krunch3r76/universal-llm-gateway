"""Error mapping for audio streaming endpoints."""

__all__ = ["map_worker_error"]


def map_worker_error(error: Exception) -> tuple[str, str, bool]:
    """
    Map worker exceptions to client error codes.

    Args:
        error: Exception from worker RPC call

    Returns:
        Tuple of (error_code, error_message, should_close)
    """
    msg = str(error)

    # Model was unloaded during stream
    if "No supervisor found for model" in msg:
        return ("model_unloaded", "Model was unloaded", True)

    # Generic processing error
    return ("processing_error", f"Audio processing failed: {msg}", False)
