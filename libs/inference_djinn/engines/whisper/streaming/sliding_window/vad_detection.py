"""VAD boundary detection for sliding window processing."""

from universal_logging import get_logger
from typing import TYPE_CHECKING

import numpy as np

from ...vad import BoundaryDetector, VADConfig, VADMethod

if TYPE_CHECKING:
    from ..config import EnhancedConfig
    from .core import SlidingWindowBuffer

logger = get_logger(__name__)

SAMPLE_RATE = 16000


class VADDetector:
    """Manages VAD boundary detection for sliding window processing."""

    def __init__(
        self,
        parent: "SlidingWindowBuffer",
        config: "EnhancedConfig",
    ):
        """Initialize VAD detector with configuration."""
        self.parent = parent
        self.config = config

        vad_config = self._build_vad_config(config.vad_method)
        self.boundary_detector = BoundaryDetector(vad_config, SAMPLE_RATE)

    def _build_vad_config(self, method: VADMethod) -> VADConfig:
        """Build VADConfig from EnhancedConfig parameters."""
        base = VADConfig().get_defaults()

        # Configure Silero parameters (primary VAD)
        p = self.config.silero
        if p.threshold is not None:
            base.silero_threshold = p.threshold
        if p.min_silence_duration_ms is not None:
            base.min_silence_duration_ms = p.min_silence_duration_ms
        if p.min_speech_duration_ms is not None:
            base.min_speech_duration_ms = p.min_speech_duration_ms

        # Configure Energy parameters (fallback)
        p = self.config.energy
        if p.threshold is not None:
            base.energy_threshold = p.threshold
        if p.frame_size_ms is not None:
            base.frame_size_ms = p.frame_size_ms

        # NOTE: WebRTC configuration removed - no longer used for gating

        return base

    def get_frame_size_samples(self, vad_method: VADMethod) -> int:
        """Get frame size in samples for the VAD method."""
        if vad_method == VADMethod.SILERO:
            return 512  # 32ms @ 16kHz
        else:  # ENERGY fallback
            return int(SAMPLE_RATE * self.boundary_detector.config.frame_size_ms / 1000)

    def detect_speech_boundary(
        self,
        audio_float: np.ndarray,
        duration_ms: float,
        vad_method: VADMethod | None = None,
    ) -> bool | None:
        """
        Detect speech boundary in audio.

        Returns:
            True if boundary detected, False if speech ongoing, None if no speech
        """
        if vad_method is None:
            vad_method = self.config.vad_method

        try:
            result = self.boundary_detector.detect_boundary(
                audio_float,
                duration_ms,
                method=vad_method,
                fallback_on_error=False,
            )
            return result
        except Exception as e:
            logger.warning(f"VAD error: {e}")
            return False

    def get_min_silence_duration_ms(self) -> int:
        """Get minimum silence duration from config."""
        return self.boundary_detector.config.min_silence_duration_ms
