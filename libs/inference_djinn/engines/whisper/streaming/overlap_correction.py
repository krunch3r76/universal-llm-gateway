"""
Word overlap correction for streaming transcription.

Handles two overlap cases at chunk boundaries:
1. Prefix cutoffs: "Chi" -> "China" (partial word completed in next chunk)
2. Identical duplications: "water" -> "water" (from audio preservation)

Minimal audio preservation (~100ms) prevents word loss at boundaries,
and this corrector removes the resulting duplications.
"""

from universal_logging import get_logger
from dataclasses import dataclass, field

from .types import HighResTimedWord, TranscriptionResult

logger = get_logger(__name__)


def _normalize_word(text: str) -> str:
    """Lowercase and strip surrounding punctuation/whitespace for comparison."""
    return text.strip().strip(".,;:!?\"'()[]{}").lower()


@dataclass
class OverlapCorrectionConfig:
    """Configuration for word overlap correction."""

    enabled: bool = False
    hold_word_count: int = 2  # Number of trailing words to hold for comparison
    max_time_gap_ms: int = 350  # Max time gap for overlap detection
    min_prefix_ratio: float = 0.4  # Minimum prefix length ratio (last/first)
    skip_on_high_confidence: bool = True  # Skip correction for natural boundaries
    high_confidence_threshold: float = 0.7  # Confidence threshold to skip


@dataclass
class PendingChunk:
    """Transcription chunk held pending overlap verification."""

    result: TranscriptionResult
    held_words: list[HighResTimedWord] = field(default_factory=list)


class OverlapCorrector:
    """
    Corrects word overlaps between consecutive transcription results.

    FUTURE: Could operate on NormalizedWord[] (from word_processing.py)
    instead of TranscriptionResult, enabling probability-weighted
    overlap decisions and metadata access. Not implemented yet.

    Handles overlap from minimal audio preservation (~100ms):
    1. Identical words: "water" -> "water" (duplication from preserved audio)
    2. Prefix cutoffs: "Chi" -> "China" (partial word completed in next chunk)

    Workflow:
    1. submit(result) - Submit new transcription result
    2. Corrector holds last N words, compares with next chunk's first words
    3. If overlap detected, removes duplicate/cutoff words from held chunk
    4. Returns corrected chunks ready for emission

    Usage:
        corrector = OverlapCorrector(config)
        for result in transcription_results:
            for corrected in corrector.submit(result):
                emit(corrected)
        # Flush remaining on session close
        for remaining in corrector.flush():
            emit(remaining)
    """

    def __init__(self, config: OverlapCorrectionConfig | None = None) -> None:
        self.config = config or OverlapCorrectionConfig()
        self._pending: PendingChunk | None = None

    def submit(
        self,
        result: TranscriptionResult,
        boundary_confidence: float = 0.5,  # NEW parameter
    ) -> list[TranscriptionResult]:
        """
        Submit transcription result for overlap correction.

        Args:
            result: New transcription result from Whisper
            boundary_confidence: Confidence of the boundary that produced this result
                               0.0-1.0, higher = more natural boundary

        Returns:
            List of corrected results ready to emit (may be empty if holding)
        """
        if not self.config.enabled:
            return [result]

        # Skip correction for high-confidence natural boundaries
        if (
            self.config.skip_on_high_confidence
            and boundary_confidence >= self.config.high_confidence_threshold
        ):
            logger.debug(
                f"Skipping overlap correction: boundary confidence {boundary_confidence:.2f} "
                f">= threshold {self.config.high_confidence_threshold}"
            )
            # Flush any pending and emit current directly
            output = self._flush_pending()
            output.append(result)
            return output

        if not result.words:
            output = self._flush_pending()
            output.append(result)
            return output

        output: list[TranscriptionResult] = []

        if self._pending is not None:
            corrected = self._check_and_correct_overlap(self._pending, result)
            if corrected:
                output.append(corrected)

        self._pending = self._create_pending(result)

        return output

    def flush(self) -> list[TranscriptionResult]:
        """
        Flush any pending chunks (call on session close).

        Returns:
            List of remaining results
        """
        return self._flush_pending()

    def _flush_pending(self) -> list[TranscriptionResult]:
        """Flush pending chunk without correction."""
        if self._pending is None:
            return []
        result = self._pending.result
        self._pending = None
        return [result]

    def _create_pending(self, result: TranscriptionResult) -> PendingChunk:
        """Create pending chunk with held words extracted."""
        words = result.words or []
        hold_count = min(self.config.hold_word_count, len(words))
        held_words = words[-hold_count:] if hold_count > 0 else []
        return PendingChunk(result=result, held_words=held_words)

    def _check_and_correct_overlap(
        self, pending: PendingChunk, current: TranscriptionResult
    ) -> TranscriptionResult | None:
        """
        Check for overlap and correct pending chunk if needed.

        Checks in order:
        1. Identical word duplication (from audio preservation)
        2. Prefix cutoff (partial word completed in next chunk)

        Returns:
            Corrected pending result, or original if no overlap
        """
        if not pending.held_words or not current.words:
            return pending.result

        last_word = pending.held_words[-1]
        first_word = current.words[0]

        # Check for identical word duplication (preservation artifact)
        if self._is_identical_duplicate(last_word, first_word):
            logger.info(f"Duplicate removed: '{last_word.word}' (preserved audio)")
            return self._remove_trailing_words(pending.result, 1)

        # Check for prefix cutoff
        if self._is_prefix_cutoff(last_word, first_word):
            logger.info(f"Prefix cutoff: '{last_word.word}' -> '{first_word.word}'")
            return self._remove_trailing_words(pending.result, 1)

        return pending.result

    def _is_identical_duplicate(
        self, last_word: HighResTimedWord, first_word: HighResTimedWord
    ) -> bool:
        """
        Detect identical word duplication from audio preservation.

        With ~100ms preservation, the same word can appear at end of chunk N
        and start of chunk N+1. Remove from chunk N (keep the complete one).
        """
        last_text = _normalize_word(last_word.word)
        first_text = _normalize_word(first_word.word)

        if not last_text or not first_text:
            return False

        # Must be identical
        if last_text != first_text:
            return False

        # Time proximity check (preservation is ~100ms, allow up to 500ms gap)
        time_gap_us = first_word.start_us - last_word.end_us
        max_gap_us = 500_000  # 500ms - generous for preservation artifacts
        jitter_tolerance_us = 50_000  # 50ms

        return -jitter_tolerance_us <= time_gap_us <= max_gap_us

    def _is_prefix_cutoff(
        self, last_word: HighResTimedWord, first_word: HighResTimedWord
    ) -> bool:
        """
        Detect if last_word is a cut-off prefix of first_word.

        Criteria:
        1. Text prefix: first_word starts with last_word (case-insensitive)
        2. Time proximity: within max_time_gap_ms
        3. Length ratio: last_word is shorter and meets min_prefix_ratio
        """
        last_text = _normalize_word(last_word.word)
        first_text = _normalize_word(first_word.word)

        # Must have text
        if not last_text or not first_text:
            return False

        time_gap_us = first_word.start_us - last_word.end_us
        max_gap_us = self.config.max_time_gap_ms * 1000
        jitter_tolerance_us = 50_000  # 50ms tolerance

        # Time proximity check
        if time_gap_us < -jitter_tolerance_us or time_gap_us > max_gap_us:
            return False

        # Text prefix check: "Chi" is prefix of "China"
        if not first_text.startswith(last_text):
            return False

        # Length check: last word should be shorter
        if len(last_text) >= len(first_text):
            return False

        # Require meaningful length gap
        if (len(first_text) - len(last_text)) < 2:
            return False

        # Prefix ratio check
        prefix_ratio = len(last_text) / len(first_text)
        if prefix_ratio < self.config.min_prefix_ratio:
            return False

        return True

    def _remove_trailing_words(
        self, result: TranscriptionResult, count: int
    ) -> TranscriptionResult:
        """Create new result with last N words removed."""
        if not result.words or len(result.words) <= count:
            return result

        corrected_words = result.words[:-count]
        removed_words = result.words[-count:]

        # Rebuild text from remaining words
        corrected_text = " ".join(w.word.strip() for w in corrected_words)

        # Adjust end time to last remaining word
        new_end_time = corrected_words[-1].end if corrected_words else result.start_time

        logger.info(
            f"Removed '{' '.join(w.word for w in removed_words)}' from chunk "
            f"[{result.start_time:.1f}s-{result.end_time:.1f}s]"
        )

        return TranscriptionResult(
            text=corrected_text,
            start_time=result.start_time,
            end_time=new_end_time,
            probability=result.probability,
            language=result.language,
            words=corrected_words,
        )
