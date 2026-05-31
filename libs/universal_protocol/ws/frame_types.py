"""Stream frame type constants and helpers.

Terminal frame types:
  - Data frames: t="done", t="err" (with various codes)

Control event codes (all use t="err"):
  - CANCELLED: Client-initiated cancellation
  - IDLE_TIMEOUT: Stream expired due to inactivity
  - MODEL_UNLOADED: Model being unloaded
"""

from typing import Final

# Frame type values (for t field)
FRAME_TOKEN: Final = "token"
FRAME_DONE: Final = "done"
FRAME_ERR: Final = "err"

# Control event codes (for code field, all use t="err")
CODE_CANCELLED: Final = "CANCELLED"
CODE_IDLE_TIMEOUT: Final = "IDLE_TIMEOUT"
CODE_MODEL_UNLOADED: Final = "MODEL_UNLOADED"
CODE_QUEUE_CLOSED: Final = "QUEUE_CLOSED"
CODE_STREAM_ERROR: Final = "STREAM_ERROR"

# Terminal frame types (cause loop exit)
TERMINAL_FRAME_TYPES: Final[frozenset[str]] = frozenset(
    {
        FRAME_DONE,
        FRAME_ERR,
    }
)

# Reserved keys that make_control_frame protects
_RESERVED_KEYS: Final[frozenset[str]] = frozenset({"t", "code", "source", "stream_id"})


def is_terminal_frame(frame: dict) -> bool:
    """Check if frame is terminal (ends stream loop)."""
    return frame.get("t") in TERMINAL_FRAME_TYPES


def get_close_code(frame: dict) -> int:
    """Get WebSocket close code for frame.

    Returns:
        1000 for "done", 1001 for all error frames
    """
    return 1000 if frame.get("t") == FRAME_DONE else 1001


def make_control_frame(
    code: str,
    stream_id: str,
    *,
    message: str | None = None,
    source: str = "registry",
    **extra: object,
) -> dict:
    """Create control event frame (t="err" with specific code).

    Inputs:
        code: One of CODE_CANCELLED, CODE_IDLE_TIMEOUT, CODE_MODEL_UNLOADED
        stream_id: Stream identifier
        message: Human-readable message
        source: Error source (default "registry")
        extra: Additional fields (cannot override t, code, source)

    Outputs:
        Frame dict with t="err" and specified code

    Raises:
        ValueError: If extra contains reserved keys
    """
    # Protect reserved keys from being overwritten
    conflicts = _RESERVED_KEYS & set(extra.keys())
    if conflicts:
        raise ValueError(f"Cannot override reserved keys: {conflicts}")

    frame: dict = {
        "t": FRAME_ERR,
        "code": code,
        "source": source,
        "stream_id": stream_id,
    }
    if message:
        frame["message"] = message
    frame.update(extra)
    return frame
