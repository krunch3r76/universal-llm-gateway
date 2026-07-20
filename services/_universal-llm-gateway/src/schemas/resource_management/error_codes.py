"""Standard string error codes for queue management API failure responses.

Centralizes machine-readable error identifiers referenced by load/unload handlers
and clients when resource, state, or priority validation checks fail.
"""


class ErrorCodes:
    """Standard error codes for queue management API"""

    INSUFFICIENT_RESOURCES = "insufficient_resources"
    MODEL_NOT_FOUND = "model_not_found"
    MODEL_ALREADY_LOADED = "model_already_loaded"
    MODEL_NOT_LOADED = "model_not_loaded"
    MODEL_BUSY = "model_busy"
    LOAD_FAILED = "load_failed"
    UNLOAD_FAILED = "unload_failed"
    INVALID_PRIORITY = "invalid_priority"
    RESOURCE_TRACKING_ERROR = "resource_tracking_error"
    INFERENCE_STATE_ERROR = "inference_state_error"
