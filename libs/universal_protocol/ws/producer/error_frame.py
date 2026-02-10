"""Producer error frame factory.

Single responsibility: Create error frames for producer errors.
"""


def make_producer_error_frame(stream_id: str, error: Exception) -> dict:
    """Create error frame for producer errors.

    Inputs:
        stream_id: Stream identifier
        error: Exception that occurred

    Outputs:
        Error frame dict ready to enqueue
    """
    return {
        "t": "err",
        "code": "PRODUCER_ERROR",
        "message": f"Producer error: {error!s}",
        "source": "stream",
        "data": {"stream_id": stream_id},
    }
