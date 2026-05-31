"""
Boundary selection strategy - simplified for Silero-only approach.

DELETED (WebRTC elimination):
- find_best_boundary_with_candidates() - WebRTC gating removed
- handle_speech_with_defer() - defer strategy removed

NEW:
- select_boundary() - Direct Silero probability analysis
"""

from typing import TYPE_CHECKING

import numpy as np
from universal_logging import get_logger

from ...vad import VADMethod

if TYPE_CHECKING:
    from .core import SlidingWindowBuffer

logger = get_logger(__name__)

SAMPLE_RATE = 16000


def select_boundary(buffer: "SlidingWindowBuffer") -> tuple[int | None, float]:
    """
    Select boundary using Silero probability analysis.

    Simplified strategy (WebRTC eliminated):
    1. Use Silero to find natural boundary (gap or local minimum)
    2. If no natural boundary and buffer >= max_window, force cut with leave-behind
    3. Otherwise return None (wait for more audio)

    Args:
        buffer: SlidingWindowBuffer instance

    Returns:
        Tuple of (boundary_bytes, confidence) or (None, 0.0) to wait
        Confidence: 0.0-1.0, higher = more natural boundary
    """
    buffer_bytes = buffer.audio_manager.get_buffer_bytes()
    max_window_bytes = buffer.max_window_size * 2

    # Try to find natural boundary using Silero probability analysis
    boundary_offset, confidence = buffer.boundary_finder.find_speech_boundary()

    if boundary_offset is not None:
        logger.debug(
            f"Natural boundary at {boundary_offset}B "
            f"({boundary_offset / SAMPLE_RATE / 2:.2f}s, confidence={confidence:.2f})"
        )
        return boundary_offset, confidence

    # No natural boundary found - check if we've reached max window
    if buffer_bytes >= max_window_bytes:
        # Try second-pass with stricter parameters
        if hasattr(buffer, "second_pass_searcher"):
            second_pass_boundary, second_pass_confidence = (
                buffer.second_pass_searcher.find_boundary(confidence)
            )
            if second_pass_boundary is not None:
                logger.debug(
                    f"Second-pass boundary at {second_pass_boundary}B "
                    f"({second_pass_boundary / SAMPLE_RATE / 2:.2f}s, confidence={second_pass_confidence:.2f})"
                )
                return second_pass_boundary, second_pass_confidence

        # Force cut with leave-behind (lowest confidence)
        leave_ms = buffer.config.boundaries.max_window_leave_behind_ms or 0
        leave_bytes = int(SAMPLE_RATE * leave_ms / 1000) * 2
        boundary_offset = max(buffer_bytes - leave_bytes, 0)

        logger.debug(
            f"Max window reached: forcing boundary with "
            f"leave_behind={leave_ms}ms (offset={boundary_offset}B)"
        )
        return boundary_offset, 0.2  # Low confidence for forced cut

    # Wait for more audio
    logger.debug(
        f"No natural boundary found, waiting for more audio "
        f"({buffer_bytes}B / {max_window_bytes}B)"
    )
    return None, 0.0


# KEEP: find_best_silero_boundary - still useful for multi-candidate evaluation
def find_best_silero_boundary(
    buffer: "SlidingWindowBuffer", buffer_audio: np.ndarray, buffer_bytes: int
) -> tuple[int | None, float]:
    """
    Find best boundary from multiple Silero speech segment candidates.

    Evaluates all speech segment ends and picks the one with highest quality:
    - Prefer boundaries with longer silence after them
    - Prefer boundaries further from min_window (avoid premature cuts)
    - Avoid boundaries too close to max_window (preserve some buffer)

    Returns:
        Tuple of (boundary_bytes, confidence) or (None, 0.0)
    """
    vad = buffer.vad_detector

    if not hasattr(vad.boundary_detector, "vad_methods"):
        boundary, conf = buffer.boundary_finder.find_speech_boundary()
        return boundary, conf

    if VADMethod.SILERO not in vad.boundary_detector.vad_methods:
        boundary, conf = buffer.boundary_finder.find_speech_boundary()
        return boundary, conf

    from ...vad import SileroVAD

    silero_vad: SileroVAD = vad.boundary_detector.vad_methods[VADMethod.SILERO]

    try:
        speech_timestamps = silero_vad.get_speech_timestamps(buffer_audio)

        if not speech_timestamps:
            logger.debug("Silero found no speech, processing complete buffer")
            return buffer_bytes, 0.9  # High confidence - definite silence

        # Evaluate each speech segment end as a potential boundary
        candidates = []
        min_window_samples = int(SAMPLE_RATE * buffer.config.min_window_duration)
        buffer_samples = len(buffer_audio)

        for i, segment in enumerate(speech_timestamps):
            segment_end_s = segment["end"]
            boundary_sample = int(segment_end_s * SAMPLE_RATE)

            if boundary_sample < min_window_samples:
                continue

            # Calculate silence duration after this boundary
            silence_duration_s = 0.0
            if i < len(speech_timestamps) - 1:
                next_start_s = speech_timestamps[i + 1]["start"]
                silence_duration_s = next_start_s - segment_end_s
            else:
                silence_duration_s = (buffer_samples / SAMPLE_RATE) - segment_end_s

            score, confidence = calculate_boundary_score_with_confidence(
                buffer, boundary_sample, silence_duration_s, buffer_samples
            )

            candidates.append(
                {
                    "sample": boundary_sample,
                    "silence_duration_s": silence_duration_s,
                    "score": score,
                    "confidence": confidence,
                }
            )

        if not candidates:
            return buffer_bytes, 0.5

        best = max(candidates, key=lambda c: c["score"])

        logger.debug(
            f"Selected boundary at {best['sample'] / SAMPLE_RATE:.2f}s "
            f"with {best['silence_duration_s']:.3f}s silence "
            f"(score={best['score']:.3f}, confidence={best['confidence']:.2f})"
        )

        return best["sample"] * 2, best["confidence"]

    except Exception as e:
        logger.error(f"Error finding best Silero boundary: {e}")
        boundary, conf = buffer.boundary_finder.find_speech_boundary()
        return boundary, conf


def calculate_boundary_score_with_confidence(
    buffer: "SlidingWindowBuffer",
    boundary_sample: int,
    silence_duration_s: float,
    buffer_samples: int,
) -> tuple[float, float]:
    """
    Calculate quality score and confidence for a boundary candidate.

    Returns:
        Tuple of (score, confidence) where both are 0.0-1.0
    """
    min_window_samples = int(SAMPLE_RATE * buffer.config.min_window_duration)
    max_window_samples = int(SAMPLE_RATE * buffer.config.max_window_duration)

    # Silence score: longer silence = higher score and confidence
    silence_score = min(silence_duration_s / 0.5, 2.0)
    silence_confidence = min(1.0, silence_duration_s / 0.3)  # 300ms+ = full confidence

    # Position score
    position_ratio = (boundary_sample - min_window_samples) / (
        max_window_samples - min_window_samples
    )
    position_score = min(position_ratio, 1.0)

    # Penalty for boundaries too close to max_window
    if boundary_sample > max_window_samples * 0.9:
        position_score *= 0.5

    total_score = (silence_score * 0.7) + (position_score * 0.3)

    # Confidence is primarily based on silence duration
    confidence = silence_confidence * 0.8 + position_score * 0.2

    return total_score, min(1.0, confidence)
