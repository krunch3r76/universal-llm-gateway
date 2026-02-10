"""Streaming ASR service for managing streaming sessions."""

from universal_logging import get_logger
import uuid
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from .config import EnhancedConfig, StreamingConfig
from .overlap_correction import OverlapCorrector
from .sliding_window import SlidingWindowBuffer
from .types import TranscriptionResult

logger = get_logger(__name__)


@dataclass
class SessionState:
    """State for a streaming session."""

    buffer: SlidingWindowBuffer
    corrector: OverlapCorrector


class StreamingASRService:
    """
    Streaming ASR service manager.

    Manages multiple concurrent streaming sessions, each with its own
    SlidingWindowBuffer for independent audio processing.
    """

    def __init__(
        self,
        whisper_model,
        beam_size_func: Callable[[], int] | None = None,
    ):
        """
        Initialize streaming ASR service.

        Args:
            whisper_model: faster-whisper model instance
            beam_size_func: Function returning beam size for transcription
        """
        self.whisper_model = whisper_model
        self.beam_size_func = beam_size_func or (lambda: 5)

        self.active_sessions: dict[str, SessionState] = {}
        self.session_count = 0

        logger.info("✅ StreamingASRService initialized")

    async def create_session(
        self,
        config: StreamingConfig | EnhancedConfig | dict,
    ) -> str:
        """
        Create a new streaming session.

        Args:
            config: Streaming configuration

        Returns:
            session_id: Unique session identifier
        """
        # Normalize config to EnhancedConfig
        if isinstance(config, dict):
            enhanced_config = EnhancedConfig(**config)
        elif isinstance(config, StreamingConfig):
            enhanced_config = config.to_enhanced_config()
        else:
            enhanced_config = config

        # Generate unique session ID
        session_id = f"stream_{self.session_count}_{uuid.uuid4().hex[:8]}"
        self.session_count += 1

        # Create sliding window buffer for this session
        buffer = SlidingWindowBuffer(
            client_id=session_id,
            config=enhanced_config,
            whisper_model=self.whisper_model,
            beam_size_func=self.beam_size_func,
        )

        # Start the background processing loop
        await buffer.start()

        # Create overlap corrector with config (use config directly to get all fields)
        corrector = OverlapCorrector(enhanced_config.overlap_cfg)

        self.active_sessions[session_id] = SessionState(
            buffer=buffer,
            corrector=corrector,
        )

        logger.info(f"📡 Created streaming session: {session_id}")
        logger.info(f"   Language: {enhanced_config.language or 'auto-detect'}")
        logger.info(f"   VAD: {enhanced_config.vad_method.value}")

        return session_id

    async def process_audio(
        self,
        session_id: str,
        audio_data: np.ndarray,
    ) -> list[TranscriptionResult]:
        """
        Process audio data for a streaming session.

        Args:
            session_id: Session identifier
            audio_data: Audio samples as numpy array (int16 or float32)

        Returns:
            List of transcription results (may be empty if no boundary detected)
        """
        if session_id not in self.active_sessions:
            logger.warning(f"Unknown session: {session_id}")
            return []

        session = self.active_sessions[session_id]
        raw_results = await session.buffer.add_audio(audio_data)

        # Apply overlap correction
        corrected_results: list[TranscriptionResult] = []
        for result in raw_results:
            corrected_results.extend(session.corrector.submit(result))

        if corrected_results:
            logger.debug(
                f"Session {session_id}: {len(corrected_results)} results, "
                f"total text: {sum(len(r.text) for r in corrected_results)} chars"
            )

        return corrected_results

    async def close_session(self, session_id: str) -> list[TranscriptionResult]:
        """
        Close a streaming session and cleanup resources.

        Args:
            session_id: Session identifier

        Returns:
            List of remaining transcription results
        """
        if session_id in self.active_sessions:
            session = self.active_sessions[session_id]

            # Stop the background processing loop
            await session.buffer.stop()

            # Flush any pending overlap-corrected results
            final_results = session.corrector.flush()

            del self.active_sessions[session_id]
            logger.info(f"📡 Closed streaming session: {session_id}")
            return final_results
        else:
            logger.warning(f"Attempted to close unknown session: {session_id}")
            return []

    def get_session_count(self) -> int:
        """Get number of active sessions."""
        return len(self.active_sessions)

    def get_session_ids(self) -> list[str]:
        """Get list of active session IDs."""
        return list(self.active_sessions.keys())

    def has_session(self, session_id: str) -> bool:
        """Check if session exists."""
        return session_id in self.active_sessions
