"""
Context carryover for streaming Whisper transcription.

Prepends previous segment audio to give Whisper more context,
improving accuracy at segment boundaries.
"""

import numpy as np
from universal_logging import get_logger

logger = get_logger(__name__)

SAMPLE_RATE = 16000

# Minimum audio energy threshold for storing context
# Below this, audio is considered silence and context is reset
MIN_AUDIO_ENERGY_RMS = 50  # RMS threshold for int16 audio


class ContextCarryover:
    """
    Manages audio context carryover for streaming Whisper transcription.

    Stores previous segment audio (int16) and prepends it to new segments.

    Edge Cases Handled:
    - Empty audio: Returns unchanged, no context stored
    - Long silence: Detects via RMS energy, resets context
    - First segment: No context available, returns unchanged
    - Very short audio: Stored as context but may not help
    """

    def __init__(
        self,
        context_duration_s: float = 1.5,
        sample_rate: int = SAMPLE_RATE,
        enabled: bool = False,
        min_rms_threshold: float = MIN_AUDIO_ENERGY_RMS,
    ):
        self.context_duration_s = context_duration_s
        self.sample_rate = sample_rate
        self.enabled = enabled
        self.min_rms_threshold = min_rms_threshold
        self._last_audio_int16: np.ndarray | None = None

    def _compute_rms(self, audio_int16: np.ndarray) -> float:
        """Compute RMS energy of audio."""
        if len(audio_int16) == 0:
            return 0.0
        return float(np.sqrt(np.mean(audio_int16.astype(np.float32) ** 2)))

    def prepare_audio(
        self,
        new_audio_int16: np.ndarray,
    ) -> tuple[np.ndarray, float]:
        """
        Prepare audio with context for transcription.

        Args:
            new_audio_int16: New audio segment (int16 format)

        Returns:
            (full_audio_int16, context_offset_s)
            - full_audio: Context prepended to new audio (or just new audio)
            - context_offset_s: Duration of prepended context (0.0 if none)

        Edge cases:
        - Empty input: Returns (input, 0.0)
        - No stored context: Returns (input, 0.0)
        - Disabled: Returns (input, 0.0)
        """
        # Edge case: empty audio
        if len(new_audio_int16) == 0:
            logger.debug("Context carryover: empty audio, skipping")
            return new_audio_int16, 0.0

        # Not enabled or no context stored yet
        if not self.enabled or self._last_audio_int16 is None:
            return new_audio_int16, 0.0

        context_samples = int(self.context_duration_s * self.sample_rate)
        context = self._last_audio_int16[-context_samples:]
        full_audio = np.concatenate([context, new_audio_int16])
        context_offset_s = len(context) / self.sample_rate

        logger.debug(
            "Context carryover: %.2fs context + %.2fs new = %.2fs total",
            context_offset_s,
            len(new_audio_int16) / self.sample_rate,
            len(full_audio) / self.sample_rate,
        )

        return full_audio, context_offset_s

    def update_context(self, audio_int16: np.ndarray) -> None:
        """
        Store audio as context for next iteration.

        Args:
            audio_int16: Audio to store (int16 format)

        Edge cases:
        - Empty audio: No context stored
        - Silent audio (RMS < threshold): Context RESET (prevents accumulating silence)
        - Very short audio: Stored, but may not be useful context
        """
        if not self.enabled:
            return

        # Edge case: empty audio
        if len(audio_int16) == 0:
            logger.debug("Context update: empty audio, not storing")
            return

        # Edge case: detect silence via RMS energy
        rms = self._compute_rms(audio_int16)
        if rms < self.min_rms_threshold:
            logger.debug(
                "Context update: audio is silence (RMS=%.1f < %.1f), resetting context",
                rms,
                self.min_rms_threshold,
            )
            self.reset()
            return

        self._last_audio_int16 = audio_int16.copy()

    def reset(self) -> None:
        """Clear stored context."""
        self._last_audio_int16 = None
