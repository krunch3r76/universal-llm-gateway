"""Worker stream handlers.

Directory-based naming for specificity:
  worker/stream/* is the worker-streaming domain (avoid generic streaming.py collisions).
"""

from .audio_rpc import WhisperStreamingHandlers
from .inference_run import StreamInferenceRunHandlers
from .inference_start import StreamInferenceStartHandlers


class StreamingHandlers(
    StreamInferenceStartHandlers,
    StreamInferenceRunHandlers,
    WhisperStreamingHandlers,
):
    """Composite mix-in providing streaming-related RPC handler methods."""

    pass


__all__ = ["StreamingHandlers"]
