"""
Base classes and enums for Speech VAD package.
"""

from universal_logging import get_logger
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum

import numpy as np

logger = get_logger("speech-vad")


class VADMethod(Enum):
    """Voice Activity Detection methods."""

    ENERGY = "energy"  # Simple energy-based detection
    SILERO = "silero"  # Silero VAD model
    WEBRTC = "webrtc"  # WebRTC VAD


@dataclass
class VADConfig:
    """Configuration for VAD detection parameters.

    All parameters have None defaults and should be set by the calling application
    to ensure consistent configuration across all VAD methods.
    """

    # Speech boundary parameters
    min_silence_duration_ms: int | None = None  # Minimum silence gap for boundary (ms)
    min_speech_duration_ms: int | None = None  # Minimum speech segment duration (ms)

    # Energy-based VAD parameters
    energy_threshold: float | None = None  # RMS energy threshold for silence detection
    silence_ratio_threshold: float | None = (
        None  # Fraction of frames that must be silent
    )
    frame_size_ms: int | None = None  # Frame size for analysis (ms)

    # Silero VAD parameters
    silero_threshold: float | None = None  # Silero detection threshold (0.0-1.0)

    # WebRTC VAD parameters
    webrtc_aggressiveness: int | None = None  # WebRTC aggressiveness level (0-3)
    webrtc_frame_duration_ms: int | None = (
        None  # WebRTC frame duration (10, 20, or 30 ms)
    )
    webrtc_voice_threshold: float | None = None  # Voice activity ratio threshold

    def get_defaults(self) -> "VADConfig":
        """Get a VADConfig with sensible fallback defaults for any None values.

        This method should only be used as a last resort when the application
        hasn't provided proper configuration. It's better to configure explicitly.
        """
        return VADConfig(
            # Speech boundary parameters - conservative defaults
            min_silence_duration_ms=self.min_silence_duration_ms or 400,
            min_speech_duration_ms=self.min_speech_duration_ms or 300,
            # Energy-based VAD parameters
            energy_threshold=self.energy_threshold or 0.005,
            silence_ratio_threshold=self.silence_ratio_threshold or 0.7,
            frame_size_ms=self.frame_size_ms or 20,
            # Silero VAD parameters - moderate sensitivity
            silero_threshold=self.silero_threshold or 0.5,
            # WebRTC VAD parameters - conservative for noise filtering
            webrtc_aggressiveness=self.webrtc_aggressiveness or 3,
            webrtc_frame_duration_ms=self.webrtc_frame_duration_ms or 20,
            webrtc_voice_threshold=self.webrtc_voice_threshold or 0.6,
        )


class BaseVAD(ABC):
    """Abstract base class for VAD implementations."""

    def __init__(self, config: VADConfig, sample_rate: int = 16000):
        # Always use config with defaults applied to ensure no None values
        self.config = config.get_defaults() if config else VADConfig().get_defaults()
        self.sample_rate = sample_rate
        self.logger = get_logger(f"speech-vad.{self.__class__.__name__.lower()}")

    @abstractmethod
    def detect_boundary(self, audio: np.ndarray, check_duration_ms: int) -> bool | None:
        """
        Detect speech boundary in audio segment.

        Args:
            audio: Audio segment to analyze (float32, -1.0 to 1.0)
            check_duration_ms: Duration of audio segment in milliseconds

        Returns:
            True if silence boundary detected (send to transcription)
            False if speech is ongoing (keep accumulating audio)
            None if no speech detected (skip transcription entirely)
        """
        pass

    @abstractmethod
    def is_available(self) -> bool:
        """
        Check if this VAD method is available and properly initialized.

        Returns:
            True if VAD method is ready to use, False otherwise
        """
        pass

    def get_name(self) -> str:
        """Get the name of this VAD method."""
        return self.__class__.__name__

    def validate_audio(self, audio: np.ndarray) -> bool:
        """
        Validate audio input format.

        Args:
            audio: Audio array to validate

        Returns:
            True if audio format is valid, False otherwise
        """
        if not isinstance(audio, np.ndarray):
            self.logger.error(f"Audio must be numpy array, got {type(audio)}")
            return False

        if audio.dtype != np.float32:
            self.logger.warning(f"Audio should be float32, got {audio.dtype}")

        if len(audio.shape) != 1:
            self.logger.error(f"Audio must be 1D array, got shape {audio.shape}")
            return False

        if len(audio) == 0:
            self.logger.error("Audio array is empty")
            return False

        return True
