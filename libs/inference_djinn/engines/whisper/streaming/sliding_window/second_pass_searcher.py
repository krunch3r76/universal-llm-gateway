"""Second-pass boundary search with stricter parameters.

Triggered when:
- Primary boundary finder returns low confidence (<0.5)
- Buffer approaching max_window but no natural boundary found

Uses stricter Silero parameters to find boundaries that primary pass missed.
"""

from typing import TYPE_CHECKING

from universal_logging import get_logger

from ...vad import SileroVAD, VADMethod

if TYPE_CHECKING:
    from ..config import SecondPassConfig
    from .core import SlidingWindowBuffer

logger = get_logger(__name__)

SAMPLE_RATE = 16000


class SecondPassSearcher:
    """Handles second-pass boundary search with stricter parameters."""

    def __init__(self, parent: "SlidingWindowBuffer"):
        """Initialize second-pass searcher with parent reference."""
        self.parent = parent

    def find_boundary(
        self, primary_confidence: float = 0.0
    ) -> tuple[int | None, float]:
        """
        Second-pass boundary search with stricter parameters.

        Triggered when primary pass returns low confidence or no boundary.

        Args:
            primary_confidence: Confidence from primary boundary finder

        Returns:
            Tuple of (boundary_bytes, confidence) or (None, 0.0)
        """
        config = self.parent.config

        # Check if second pass config exists
        if not hasattr(config, "second_pass"):
            return None, 0.0

        second_pass = config.second_pass

        if not second_pass.enabled:
            return None, 0.0

        # Skip if primary confidence is already good
        if primary_confidence >= 0.7:
            logger.debug(
                f"Skipping second-pass: primary confidence {primary_confidence:.2f} sufficient"
            )
            return None, 0.0

        audio_mgr = self.parent.audio_manager
        buffer_bytes = audio_mgr.get_buffer_bytes()

        if buffer_bytes < self.parent.min_window_size * 2:
            return None, 0.0

        # Use Silero with stricter parameters
        return self._find_boundary_silero_strict(second_pass)

    def _find_boundary_silero_strict(
        self, second_pass: "SecondPassConfig"
    ) -> tuple[int | None, float]:
        """
        Second-pass Silero boundary search with stricter silence requirement.

        Uses longer min_silence_duration to find more definitive boundaries.
        """
        from .probability_analyzer import SpeechProbabilityAnalyzer

        audio_mgr = self.parent.audio_manager
        vad = self.parent.vad_detector
        config = self.parent.config

        audio_float = audio_mgr.get_buffer_audio_float32()
        buffer_duration_ms = int(len(audio_float) / SAMPLE_RATE * 1000)

        # Use guard window to avoid cutting too early
        guard_window_ms = int(second_pass.guard_window_s * 1000)
        min_window_ms = int(config.min_window_duration * 1000) + guard_window_ms

        if not hasattr(vad.boundary_detector, "vad_methods"):
            return None, 0.0

        if VADMethod.SILERO not in vad.boundary_detector.vad_methods:
            return None, 0.0

        silero_vad: SileroVAD = vad.boundary_detector.vad_methods[VADMethod.SILERO]

        # Get speech timestamps with stricter silence requirement
        original_min_silence = silero_vad.config.min_silence_duration_ms
        try:
            silero_vad.config.min_silence_duration_ms = (
                second_pass.min_silence_duration_ms
            )
            speech_timestamps = silero_vad.get_speech_timestamps(audio_float)
        finally:
            silero_vad.config.min_silence_duration_ms = original_min_silence

        # Analyze with stricter parameters
        analyzer = SpeechProbabilityAnalyzer(
            min_gap_ms=second_pass.min_silence_duration_ms,
            min_probability_for_gap=0.25,  # Stricter threshold
            search_start_ratio=0.5,  # Search only latter half
        )

        candidate = analyzer.find_best_boundary(
            speech_timestamps=speech_timestamps,
            buffer_duration_ms=buffer_duration_ms,
            min_window_ms=min_window_ms,
            probabilities=None,  # Skip probability analysis for speed
        )

        if candidate is None:
            logger.debug("Second-pass: no boundary found with stricter parameters")
            return None, 0.0

        boundary_bytes = candidate.offset_samples * 2

        # Second-pass boundaries get slightly lower confidence (refinement, not primary)
        confidence = min(0.8, candidate.confidence * 0.9)

        logger.debug(
            f"Second-pass boundary at {candidate.offset_samples / SAMPLE_RATE:.2f}s "
            f"(confidence={confidence:.2f})"
        )

        return boundary_bytes, confidence
