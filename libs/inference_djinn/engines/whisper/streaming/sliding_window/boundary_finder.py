"""Boundary finding logic for sliding window processing."""

import time
from typing import TYPE_CHECKING

import numpy as np
from universal_logging import get_logger

from ...vad import SileroVAD, VADMethod

if TYPE_CHECKING:
    from .core import SlidingWindowBuffer

logger = get_logger(__name__)

SAMPLE_RATE = 16000


class BoundaryFinder:
    """Finds speech boundaries in audio buffer."""

    def __init__(self, parent: "SlidingWindowBuffer"):
        """Initialize boundary finder with parent reference."""
        self.parent = parent
        self._last_boundary_time = time.time()
        self._last_boundary_check_time = 0.0
        self._last_boundary_confidence = 0.0  # Track confidence for overlap correction

    def find_speech_boundary(
        self,
        vad_method: VADMethod | None = None,
    ) -> tuple[int | None, float]:
        """
        Find the first speech boundary from the end of audio buffer.

        Args:
            vad_method: VAD method to use (defaults to config)

        Returns:
            Tuple of (byte_offset, confidence) or (None, 0.0) if no boundary found
            Confidence is 0.0-1.0, higher = more natural boundary
        """
        if vad_method is None:
            vad_method = self.parent.config.vad_method

        audio_mgr = self.parent.audio_manager

        if audio_mgr.get_buffer_bytes() < self.parent.min_window_size * 2:
            return None, 0.0

        # Use Silero probability analysis (primary method)
        if vad_method == VADMethod.SILERO:
            return self._find_boundary_silero()

        # Fallback for non-Silero methods (lower confidence)
        boundary = self._find_boundary_chunked(vad_method)
        return boundary, 0.5 if boundary else 0.0

    def _find_boundary_silero(self) -> tuple[int | None, float]:
        """
        Find boundary using Silero VAD with probability analysis.

        Returns:
            Tuple of (boundary_bytes, confidence) where confidence is 0.0-1.0
            Returns (None, 0.0) if no boundary found
        """
        t0 = time.perf_counter()
        from .probability_analyzer import SpeechProbabilityAnalyzer

        audio_mgr = self.parent.audio_manager
        vad = self.parent.vad_detector
        config = self.parent.config

        audio_float = audio_mgr.get_buffer_audio_float32()
        buffer_duration_ms = int(len(audio_float) / SAMPLE_RATE * 1000)
        min_window_ms = int(config.min_window_duration * 1000)

        if not hasattr(vad.boundary_detector, "vad_methods"):
            logger.warning("Silero VAD not available in boundary detector")
            return None, 0.0

        if VADMethod.SILERO not in vad.boundary_detector.vad_methods:
            logger.warning("Silero VAD not in vad_methods")
            return None, 0.0

        silero_vad: SileroVAD = vad.boundary_detector.vad_methods[VADMethod.SILERO]

        # Get speech timestamps
        t1 = time.perf_counter()
        speech_timestamps = silero_vad.get_speech_timestamps(audio_float)
        t2 = time.perf_counter()

        # Get frame probabilities if available
        probabilities = self._get_frame_probabilities(silero_vad, audio_float)
        t3 = time.perf_counter()

        # Analyze with probability analyzer
        min_silence_ms = config.silero.min_silence_duration_ms or 300
        analyzer = SpeechProbabilityAnalyzer(
            min_gap_ms=min_silence_ms,
            min_probability_for_gap=0.3,
            search_start_ratio=0.4,
        )

        candidate = analyzer.find_best_boundary(
            speech_timestamps=speech_timestamps,
            buffer_duration_ms=buffer_duration_ms,
            min_window_ms=min_window_ms,
            probabilities=probabilities,
        )
        t4 = time.perf_counter()

        if candidate is None:
            logger.debug(
                f"⏱️  VAD timing (no boundary): "
                f"timestamps={t2 - t1:.3f}s, "
                f"probabilities={t3 - t2:.3f}s, "
                f"analysis={t4 - t3:.3f}s, "
                f"total={t4 - t0:.3f}s"
            )
            return None, 0.0

        boundary_bytes = candidate.offset_samples * 2  # 16-bit audio
        offset_s = candidate.offset_samples / SAMPLE_RATE

        # Log timing with boundary info
        logger.debug(
            f"⏱️  VAD timing (found boundary): "
            f"timestamps={t2 - t1:.3f}s, "
            f"probabilities={t3 - t2:.3f}s, "
            f"analysis={t4 - t3:.3f}s, "
            f"total={t4 - t0:.3f}s"
        )

        # Log differently based on boundary source for diagnostic visibility
        if candidate.source == "minimum":
            logger.debug(
                "Boundary via minima at %.2fs, conf=%.2f, prob=%.3f",
                offset_s,
                candidate.confidence,
                candidate.probability,
            )
        else:
            logger.debug(
                "Silero boundary at %.2fs via %s (conf=%.2f)",
                offset_s,
                candidate.source,
                candidate.confidence,
            )

        return boundary_bytes, candidate.confidence

    def _get_frame_probabilities(
        self, silero_vad: "SileroVAD", audio_float: np.ndarray
    ) -> np.ndarray | None:
        """
        Extract frame-level speech probabilities from Silero.

        Prefers native get_frame_probabilities() method which handles edge
        cases and returns contiguous float32 array.

        Returns:
            Array of probabilities (0.0-1.0) per frame, or None if unavailable.
        """
        try:
            probs = silero_vad.get_frame_probabilities(audio_float)
            if probs is not None and len(probs) > 0:
                return probs
            return None
        except Exception as e:
            logger.debug(f"Could not get frame probabilities: {e}")
            return None

    def _find_boundary_chunked(self, vad_method: VADMethod) -> int | None:
        """Find boundary using chunked scanning (for non-Silero methods)."""
        audio_mgr = self.parent.audio_manager
        vad = self.parent.vad_detector

        frame_size_samples = vad.get_frame_size_samples(vad_method)
        frame_size_bytes = frame_size_samples * 2

        scan_step_ms = 100
        scan_step_samples = int(SAMPLE_RATE * scan_step_ms / 1000)
        aligned_scan_step = (
            (scan_step_samples + frame_size_samples - 1) // frame_size_samples
        ) * frame_size_samples
        aligned_scan_bytes = aligned_scan_step * 2

        buffer_bytes = audio_mgr.get_buffer_bytes()
        max_search_depth = (self.parent.max_window_size // 2) * 2
        min_scan_pos = max(aligned_scan_bytes, buffer_bytes - max_search_depth)

        current_pos = buffer_bytes
        max_iterations = max(20, max_search_depth // aligned_scan_bytes)
        iteration = 0

        while current_pos > min_scan_pos and iteration < max_iterations:
            current_pos = max(current_pos - aligned_scan_bytes, frame_size_bytes)
            iteration += 1

            remaining_bytes = buffer_bytes - current_pos
            if remaining_bytes < frame_size_bytes:
                continue

            audio_bytes = audio_mgr.audio_buffer[
                current_pos : current_pos + remaining_bytes
            ]
            segment = (
                np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
            )

            duration_ms = len(segment) / SAMPLE_RATE * 1000
            result = vad.detect_speech_boundary(segment, duration_ms, vad_method)

            if result is True:
                # Found boundary - move to silence midpoint
                return self._move_to_silence_midpoint(
                    current_pos,
                    vad.get_min_silence_duration_ms(),
                )
            elif result is None:
                # No speech - continue searching
                continue
            # result is False - speech ongoing, continue

        logger.debug(f"No boundary found (searched {iteration} positions)")
        return None

    def _move_to_silence_midpoint(
        self,
        boundary_start: int,
        min_silence_ms: int,
    ) -> int:
        """Move boundary to configurable split point within silence period."""
        audio_mgr = self.parent.audio_manager
        config = self.parent.config

        min_silence_samples = int(SAMPLE_RATE * min_silence_ms / 1000)
        silence_to_process_ratio = 1.0 - config.silence_retention_ratio
        silence_process_samples = int(min_silence_samples * silence_to_process_ratio)
        silence_process_bytes = silence_process_samples * 2

        split_offset = boundary_start + silence_process_bytes
        max_allowed = audio_mgr.get_buffer_bytes()
        split_offset = min(split_offset, max_allowed)

        # Ensure minimum remaining audio
        min_remaining_ms = 200
        min_remaining_bytes = int(SAMPLE_RATE * min_remaining_ms / 1000) * 2
        remaining = max_allowed - split_offset

        if remaining < min_remaining_bytes:
            split_offset = max(boundary_start, max_allowed - min_remaining_bytes)

        logger.debug(
            f"Silence split: {boundary_start / 2 / SAMPLE_RATE:.2f}s → "
            f"{split_offset / 2 / SAMPLE_RATE:.2f}s"
        )

        return split_offset

    def should_check_boundary(self, min_check_interval: float = 0.25) -> bool:
        """Check if enough time has passed since last boundary check."""
        current_time = time.time()
        if current_time - self._last_boundary_check_time < min_check_interval:
            return False
        self._last_boundary_check_time = current_time
        return True

    def reset_boundary_timer(self) -> None:
        """Reset the boundary timer after successful processing."""
        self._last_boundary_time = time.time()
