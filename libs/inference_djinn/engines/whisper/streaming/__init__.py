"""
Streaming ASR Package for Whisper Engine

Provides real-time audio streaming transcription with natural boundary detection.

Usage:
    from inference_djinn.engines.whisper.streaming import (
        StreamingASRService,
        StreamingConfig,
        TranscriptionResult,
    )

    service = StreamingASRService(whisper_model)
    session_id = await service.create_session(config)
    results = service.process_audio(session_id, audio_data)
"""

from .buffer import EfficientAudioBuffer
from .config import EnhancedConfig, StreamingConfig
from .service import StreamingASRService
from .types import HighResTimedWord, StreamingResponse, TranscriptionResult

__version__ = "1.0.0"

__all__ = [
    "EfficientAudioBuffer",
    "EnhancedConfig",
    "StreamingConfig",
    "StreamingASRService",
    "HighResTimedWord",
    "TranscriptionResult",
    "StreamingResponse",
]
