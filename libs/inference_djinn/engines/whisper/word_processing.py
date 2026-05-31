"""
Word processing pipeline for Whisper transcription.

This module provides a clean intermediate representation (NormalizedWord) and
composable transformation functions for processing Whisper output.

Design Goals:
- Each transformation is a pure function (testable in isolation)
- Metadata collection for analysis and debugging
- Performance profiling hooks for each pipeline stage
- Future: Reusable by non-streaming transcription path
"""

import time
from dataclasses import dataclass, field
from typing import Any

from universal_logging import DEBUG, get_logger

logger = get_logger(__name__)

SAMPLE_RATE = 16000


@dataclass
class NormalizedWord:
    """
    Intermediate word representation with timestamps in seconds.

    Attributes:
        text: Word text (stripped)
        start_s: Start time in seconds (relative to audio segment)
        end_s: End time in seconds (relative to audio segment)
        probability: Word-level confidence from Whisper (0.0-1.0)
        metadata: Extensible dict for additional annotations
            - Reserved keys: 'source_segment_idx', 'filtered_reason', 'speaker'
    """

    text: str
    start_s: float
    end_s: float
    probability: float
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def duration_s(self) -> float:
        """Word duration in seconds."""
        return self.end_s - self.start_s

    def with_adjusted_times(self, offset_s: float) -> "NormalizedWord":
        """Return copy with timestamps adjusted by offset (subtracts offset)."""
        return NormalizedWord(
            text=self.text,
            start_s=self.start_s - offset_s,
            end_s=self.end_s - offset_s,
            probability=self.probability,
            metadata=self.metadata.copy(),
        )


@dataclass
class PipelineMetrics:
    """
    Performance and statistics for pipeline execution.

    Collected during processing for profiling and debugging.
    """

    # Timing (milliseconds)
    extract_ms: float = 0.0
    context_filter_ms: float = 0.0
    probability_filter_ms: float = 0.0
    hallucination_filter_ms: float = 0.0
    convert_ms: float = 0.0
    total_ms: float = 0.0

    # Counts
    words_extracted: int = 0
    words_after_context: int = 0
    words_after_probability: int = 0
    words_after_hallucination: int = 0
    words_final: int = 0

    # Probability filter stats (for proving effectiveness)
    prob_filter_removed: int = 0
    prob_filter_removed_texts: list[str] = field(default_factory=list)
    prob_filter_avg_removed_prob: float = 0.0
    prob_filter_avg_kept_prob: float = 0.0

    def log_summary(self, level: int = DEBUG) -> None:
        """
        Log pipeline metrics summary.

        Production note: Logs at DEBUG level. Control via logger config.
        """
        logger.log(
            level,
            "Pipeline: %d→%d→%d words (%.1fms) | "
            "prob_filter: -%d words (avg_removed=%.2f, avg_kept=%.2f)",
            self.words_extracted,
            self.words_after_context,
            self.words_final,
            self.total_ms,
            self.prob_filter_removed,
            self.prob_filter_avg_removed_prob,
            self.prob_filter_avg_kept_prob,
        )


def extract_words(
    segments: list,
    metrics: PipelineMetrics | None = None,
) -> list[NormalizedWord]:
    """
    Extract words from Whisper segments into normalized form.

    Handles both dict and object segment formats from different Whisper backends.
    """
    start_time = time.perf_counter()
    words: list[NormalizedWord] = []

    for seg_idx, segment in enumerate(segments):
        # Handle dict or object format
        if isinstance(segment, dict):
            segment_words = segment.get("words", [])
        else:
            segment_words = getattr(segment, "words", []) or []

        for word in segment_words:
            if isinstance(word, dict):
                text = word.get("word", "")
                start = word.get("start", 0.0)
                end = word.get("end", 0.0)
                prob = word.get("probability", 1.0)
            else:
                text = getattr(word, "word", "")
                start = getattr(word, "start", 0.0)
                end = getattr(word, "end", 0.0)
                prob = getattr(word, "probability", 1.0)

            words.append(
                NormalizedWord(
                    text=text.strip(),
                    start_s=float(start),
                    end_s=float(end),
                    probability=float(prob),
                    metadata={"source_segment_idx": seg_idx},
                )
            )

    if metrics:
        metrics.extract_ms = (time.perf_counter() - start_time) * 1000
        metrics.words_extracted = len(words)

    return words


def filter_by_context(
    words: list[NormalizedWord],
    context_offset_s: float,
    metrics: PipelineMetrics | None = None,
) -> list[NormalizedWord]:
    """
    Remove words in context region and adjust timestamps.

    Words with start_s < context_offset_s are filtered (they belong to
    the prepended context audio, not the new segment).

    Timestamp math:
    - Whisper returns times relative to input start (0.0s)
    - Context audio occupies [0, context_offset_s)
    - New audio occupies [context_offset_s, end)
    - After filtering: subtract offset so new audio starts at 0.0s
    """
    start_time = time.perf_counter()

    if context_offset_s <= 0.0:
        if metrics:
            metrics.context_filter_ms = 0.0
            metrics.words_after_context = len(words)
        return words

    filtered = [
        w.with_adjusted_times(context_offset_s)
        for w in words
        if w.start_s >= context_offset_s
    ]

    if metrics:
        metrics.context_filter_ms = (time.perf_counter() - start_time) * 1000
        metrics.words_after_context = len(filtered)

    return filtered


def filter_by_probability(
    words: list[NormalizedWord],
    threshold: float,
    metrics: PipelineMetrics | None = None,
    collect_removed: bool = True,
) -> list[NormalizedWord]:
    """
    Remove low-probability words.

    This is the primary quality filter. Metrics collection proves effectiveness
    by tracking what was removed and the probability distributions.
    """
    start_time = time.perf_counter()

    kept: list[NormalizedWord] = []
    removed: list[NormalizedWord] = []

    for w in words:
        if w.probability >= threshold:
            kept.append(w)
        else:
            removed.append(w)

    if metrics:
        metrics.probability_filter_ms = (time.perf_counter() - start_time) * 1000
        metrics.words_after_probability = len(kept)
        metrics.prob_filter_removed = len(removed)

        if collect_removed and removed:
            metrics.prob_filter_removed_texts = [w.text for w in removed[:10]]

        if removed:
            metrics.prob_filter_avg_removed_prob = sum(
                w.probability for w in removed
            ) / len(removed)
        if kept:
            metrics.prob_filter_avg_kept_prob = sum(w.probability for w in kept) / len(
                kept
            )

        if removed:
            logger.debug(
                "Probability filter: removed %d words (avg_prob=%.3f), "
                "kept %d words (avg_prob=%.3f), threshold=%.2f, "
                "removed_sample=%s",
                len(removed),
                metrics.prob_filter_avg_removed_prob,
                len(kept),
                metrics.prob_filter_avg_kept_prob,
                threshold,
                metrics.prob_filter_removed_texts[:5],
            )

    return kept


def detect_hallucinations(
    words: list[NormalizedWord],
    metrics: PipelineMetrics | None = None,
) -> list[NormalizedWord]:
    """
    Detect and filter hallucinated words/patterns.

    Current implementation: pass-through (placeholder for future enhancement).
    """
    start_time = time.perf_counter()
    result = words

    if metrics:
        metrics.hallucination_filter_ms = (time.perf_counter() - start_time) * 1000
        metrics.words_after_hallucination = len(result)

    return result


def to_timed_words(
    words: list[NormalizedWord],
    start_time: float,
    metrics: PipelineMetrics | None = None,
) -> list:
    """
    Convert NormalizedWord to final HighResTimedWord format.

    Applies absolute time offset and converts to microsecond precision.

    Args:
        words: Normalized words with relative timestamps (post-context-filter)
        start_time: Absolute start time offset in seconds

    Note:
        Imports from streaming.types. For future non-streaming reuse,
        consider relocating HighResTimedWord to a shared types module.
    """
    from .streaming.types import HighResTimedWord

    start = time.perf_counter()

    timed = [
        HighResTimedWord(
            word=w.text,
            start_us=int((start_time + w.start_s) * 1_000_000),
            end_us=int((start_time + w.end_s) * 1_000_000),
            probability=w.probability,
        )
        for w in words
    ]

    if metrics:
        metrics.convert_ms = (time.perf_counter() - start) * 1000
        metrics.words_final = len(timed)
        metrics.total_ms = (
            metrics.extract_ms
            + metrics.context_filter_ms
            + metrics.probability_filter_ms
            + metrics.hallucination_filter_ms
            + metrics.convert_ms
        )

    return timed


def build_text_from_words(words: list[NormalizedWord]) -> str:
    """
    Build text string from word list.

    This ensures text is always consistent with the filtered word list,
    fixing the existing bug where segment text could diverge from filtered words.
    """
    return " ".join(w.text for w in words if w.text)
