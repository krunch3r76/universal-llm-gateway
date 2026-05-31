"""Speech probability curve analysis for boundary detection.

This module analyzes Silero VAD output to find natural speech boundaries:
1. Gap detection: Find pauses between speech segments from timestamps
2. Minimum detection: Find weakest speech regions in probability curves
3. Confidence scoring: Rate boundary quality for downstream decisions

Used by boundary_finder.py to replace WebRTC binary gating.
"""

from dataclasses import dataclass

import numpy as np
from universal_logging import get_logger

logger = get_logger(__name__)

SAMPLE_RATE = 16000


@dataclass
class BoundaryCandidate:
    """A potential boundary point with quality metrics."""

    offset_samples: int  # Position in audio samples
    confidence: float  # 0.0-1.0, higher = more confident this is a good boundary
    source: str  # "gap", "minimum", or "forced"
    gap_duration_ms: int = 0  # Duration of silence gap (if gap-based)
    probability: float = 0.0  # Speech probability at boundary (if minimum-based)


class SpeechProbabilityAnalyzer:
    """
    Analyzes Silero speech probability curves to find natural boundaries.

    Strategy hierarchy (preferred to fallback):
    1. Speech timestamp gaps - Natural pauses between utterances (highest confidence)
    2. Local probability minima - Weakest speech regions (medium confidence)
    3. Global minimum in search region - Last resort (low confidence)

    Boundary confidence scores influence:
    - Overlap correction: High confidence = skip correction
    - Second pass: Low confidence = trigger refinement
    - Leave-behind: Only applied for low confidence / forced cuts
    """

    def __init__(
        self,
        min_gap_ms: int = 300,
        min_probability_for_gap: float = 0.3,
        search_start_ratio: float = 0.4,
    ):
        """
        Initialize analyzer.

        Args:
            min_gap_ms: Minimum gap duration to consider as natural boundary (ms)
            min_probability_for_gap: Max probability threshold for gap detection
            search_start_ratio: Start searching from this ratio of buffer (0.0-1.0)
                               Default 0.4 means search latter 60% of buffer
        """
        self.min_gap_ms = min_gap_ms
        self.min_probability_for_gap = min_probability_for_gap
        self.search_start_ratio = search_start_ratio

    def find_best_boundary(
        self,
        speech_timestamps: list[dict],
        buffer_duration_ms: int,
        min_window_ms: int,
        probabilities: np.ndarray | None = None,
    ) -> BoundaryCandidate | None:
        """
        Find the best natural boundary using all available signals.

        Args:
            speech_timestamps: List of {'start': sec, 'end': sec} from Silero
            buffer_duration_ms: Total buffer duration for context
            min_window_ms: Minimum window requirement (reject earlier boundaries)
            probabilities: Optional frame-level probabilities for local minima

        Returns:
            BoundaryCandidate with offset and confidence, or None if no boundary found
        """
        # Strategy 1: Try to find boundary using timestamp gaps (highest confidence)
        gap_candidate = self._find_boundary_from_gaps(
            speech_timestamps, buffer_duration_ms, min_window_ms
        )
        if gap_candidate is not None:
            return gap_candidate

        # Strategy 2: Find local minimum in probability curve (medium confidence)
        if probabilities is not None and len(probabilities) >= 10:
            min_candidate = self._find_local_minimum(
                probabilities, buffer_duration_ms, min_window_ms
            )
            if min_candidate is not None:
                return min_candidate

        # No natural boundary found
        logger.debug("No natural boundary found via probability analysis")
        return None

    def _find_boundary_from_gaps(
        self,
        speech_timestamps: list[dict],
        buffer_duration_ms: int,
        min_window_ms: int,
    ) -> BoundaryCandidate | None:
        """
        Find natural boundary using speech timestamp gaps.

        Gaps are natural pauses between speech segments. Longer gaps = higher confidence.
        """
        if not speech_timestamps or len(speech_timestamps) < 2:
            return None

        # Convert to milliseconds for consistent comparison
        gaps = []
        for i in range(len(speech_timestamps) - 1):
            gap_start_ms = int(speech_timestamps[i]["end"] * 1000)
            gap_end_ms = int(speech_timestamps[i + 1]["start"] * 1000)
            gap_duration = gap_end_ms - gap_start_ms

            if gap_duration >= self.min_gap_ms:
                # Use midpoint of gap as boundary
                boundary_ms = gap_start_ms + (gap_duration // 2)

                # Skip boundaries before min_window
                if boundary_ms < min_window_ms:
                    continue

                position_ratio = boundary_ms / buffer_duration_ms
                gaps.append(
                    {
                        "boundary_ms": boundary_ms,
                        "gap_duration": gap_duration,
                        "position_ratio": position_ratio,
                    }
                )

        if not gaps:
            return None

        # Prefer gaps in the latter portion of buffer (more context for transcription)
        # Among qualified gaps, choose the longest (strongest silence signal)
        qualified_gaps = [
            g for g in gaps if g["position_ratio"] >= self.search_start_ratio
        ]
        if not qualified_gaps:
            qualified_gaps = gaps  # Fall back to any gap if none in latter portion

        best_gap = max(qualified_gaps, key=lambda g: g["gap_duration"])

        # Calculate confidence: longer gap = higher confidence, cap at 1.0
        # 300ms gap = 0.6 confidence, 500ms+ gap = 1.0 confidence
        confidence = min(
            1.0, 0.6 + (best_gap["gap_duration"] - self.min_gap_ms) / 500.0
        )

        boundary_samples = int(best_gap["boundary_ms"] / 1000.0 * SAMPLE_RATE)

        logger.debug(
            f"Gap boundary at {best_gap['boundary_ms']}ms "
            f"(gap={best_gap['gap_duration']}ms, confidence={confidence:.2f})"
        )

        return BoundaryCandidate(
            offset_samples=boundary_samples,
            confidence=confidence,
            source="gap",
            gap_duration_ms=best_gap["gap_duration"],
        )

    def _find_local_minimum(
        self,
        probabilities: np.ndarray,
        buffer_duration_ms: int,
        min_window_ms: int,
    ) -> BoundaryCandidate | None:
        """
        Find local minimum in speech probability curve.

        Scans probability curve to find weakest speech region (lowest probability)
        as natural boundary point.
        """
        # Silero uses 512-sample frames at 16kHz = 32ms per frame
        ms_per_frame = 32

        # Calculate search start index based on min_window
        min_window_frames = max(
            int(len(probabilities) * self.search_start_ratio),
            int(min_window_ms / ms_per_frame),
        )
        search_region = probabilities[min_window_frames:]

        if len(search_region) == 0:
            return None

        # Find indices where probability is below gap threshold
        low_prob_mask = search_region < self.min_probability_for_gap

        if not low_prob_mask.any():
            # No clear low-probability regions, find global minimum
            local_min_idx = np.argmin(search_region)
            boundary_idx = min_window_frames + local_min_idx
            probability = float(probabilities[boundary_idx])

            # Reject if outside acceptable range for minima:
            # - Too low (< 0.05): Pure silence, should be gap detection
            # - Too high (> 0.7): Strong ongoing speech, no natural boundary
            # Minima should find WEAK SPEECH in range [0.05, 0.7]
            if probability < 0.05:
                logger.debug(
                    f"Rejecting minima at frame {boundary_idx}: "
                    f"prob={probability:.3f} is pure silence (< 0.05)"
                )
                return None

            if probability > 0.7:
                logger.debug(
                    f"Rejecting minima at frame {boundary_idx}: "
                    f"prob={probability:.3f} is strong speech (> 0.7), no natural boundary"
                )
                return None

            # Low confidence for global minimum (no clear gap)
            confidence = max(0.3, 0.6 - probability)

            logger.debug(
                f"Global minimum at frame {boundary_idx} "
                f"(prob={probability:.3f}, confidence={confidence:.2f})"
            )
        else:
            # Find longest continuous low-probability region
            low_regions = self._find_continuous_regions(low_prob_mask)

            if not low_regions:
                return None

            # Choose longest low-probability region, use its midpoint
            longest_region = max(low_regions, key=lambda r: r[1] - r[0])
            region_midpoint = (longest_region[0] + longest_region[1]) // 2
            boundary_idx = min_window_frames + region_midpoint

            probability = float(probabilities[boundary_idx])
            region_length = longest_region[1] - longest_region[0]

            # Reject if outside acceptable range
            if probability < 0.05 or probability > 0.7:
                logger.debug(
                    f"Rejecting low-prob region at frame {boundary_idx}: "
                    f"prob={probability:.3f} outside valid range [0.05, 0.7]"
                )
                return None

            # Higher confidence for longer low-probability regions
            confidence = min(0.8, 0.5 + region_length * 0.02)

            logger.debug(
                f"Low-prob region at frame {boundary_idx} "
                f"(prob={probability:.3f}, region_len={region_length}, confidence={confidence:.2f})"
            )

        # Convert frame index to samples (512 samples per frame)
        boundary_samples = boundary_idx * 512

        return BoundaryCandidate(
            offset_samples=boundary_samples,
            confidence=confidence,
            source="minimum",
            probability=probability,
        )

    def _find_continuous_regions(self, mask: np.ndarray) -> list[tuple[int, int]]:
        """Find continuous True regions in boolean mask."""
        regions = []
        in_region = False
        region_start = 0

        for i, is_low in enumerate(mask):
            if is_low and not in_region:
                region_start = i
                in_region = True
            elif not is_low and in_region:
                regions.append((region_start, i))
                in_region = False

        if in_region:
            regions.append((region_start, len(mask)))

        return regions
