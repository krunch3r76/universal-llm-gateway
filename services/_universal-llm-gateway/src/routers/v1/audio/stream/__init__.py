"""WebSocket endpoint package for real-time audio streaming transcription.

Package-shadow of stream.py. Re-exports `router` so `from src.routers.v1.audio
import stream` and app_factory `audio_stream.router` keep working unchanged.
"""

from . import live_transcribe as _live_transcribe  # noqa: F401 — register routes
from .deps import router

__all__ = ["router"]
