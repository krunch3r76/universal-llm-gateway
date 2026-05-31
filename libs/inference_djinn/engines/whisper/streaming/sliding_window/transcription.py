"""Whisper transcription for sliding window processing."""

import time
from collections.abc import Callable
from typing import TYPE_CHECKING

import numpy as np
from universal_logging import get_logger

from ...word_processing import (
    PipelineMetrics,
    build_text_from_words,
    detect_hallucinations,
    extract_words,
    filter_by_context,
    filter_by_probability,
    to_timed_words,
)
from ..context_carryover import ContextCarryover
from ..types import TranscriptionResult

if TYPE_CHECKING:
    from .core import SlidingWindowBuffer

logger = get_logger(__name__)

SAMPLE_RATE = 16000


class Transcriber:
    """Handles Whisper transcription for sliding window processing."""

    def __init__(
        self,
        parent: "SlidingWindowBuffer",
        whisper_model,
        beam_size_func: Callable[[], int],
        min_word_probability: float = 0.15,
    ):
        """
        Initialize transcriber.

        Args:
            parent: Parent SlidingWindowBuffer
            whisper_model: faster-whisper model instance
            beam_size_func: Function returning beam size for transcription
            min_word_probability: Minimum word probability threshold for filtering
        """
        self.parent = parent
        self.model = whisper_model
        self.get_beam_size = beam_size_func
        self.min_word_probability = min_word_probability

        # Language detection
        self.detected_language: str | None = None
        self.language_confidence: float = 0.0
        self.language_forced: bool = False

        # Context carryover (Phase 2)
        cc_cfg = parent.config.context_carryover
        self.context_carryover = ContextCarryover(
            context_duration_s=cc_cfg.duration_s,
            enabled=cc_cfg.enabled,
        )

        logger.debug(
            "Context carryover: enabled=%s, duration=%.2fs",
            cc_cfg.enabled,
            cc_cfg.duration_s,
        )

        # Pipeline metrics (reused across calls)
        self._metrics = PipelineMetrics()

    def set_forced_language(self, language: str | None) -> None:
        """Set forced language for transcription."""
        if language:
            self.detected_language = language
            self.language_confidence = 1.0
            self.language_forced = True
        else:
            self.language_forced = False

    def transcribe_segment(
        self,
        audio_array: np.ndarray,
        start_time: float,
        use_whisper_vad: bool = False,
        whisper_vad_params: dict | None = None,
    ) -> TranscriptionResult | None:
        """Transcribe audio segment through pipeline with optional context."""
        t0 = time.perf_counter()

        # Prepare audio with context (Phase 2)
        full_audio, context_offset_s = self.context_carryover.prepare_audio(audio_array)
        t1 = time.perf_counter()

        # Calculate times for new audio only
        new_audio_duration = len(audio_array) / SAMPLE_RATE
        end_time = start_time + new_audio_duration

        try:
            # Transcribe (full_audio includes context if enabled)
            transcribe_kwargs = dict(
                audio=full_audio.astype(np.float32) / 32768.0,
                beam_size=self.get_beam_size(),
                language=self.detected_language,
                word_timestamps=True,
                best_of=1,
                temperature=0.0,
            )

            if use_whisper_vad and whisper_vad_params:
                transcribe_kwargs["vad_filter"] = True
                transcribe_kwargs["vad_parameters"] = whisper_vad_params

            t2 = time.perf_counter()
            result = self.model.transcribe(**transcribe_kwargs)
            t3 = time.perf_counter()

            segments_list = result.get("segments", [])

            # Update language detection
            if not self.language_forced:
                self.detected_language = result.get("language")
                self.language_confidence = 1.0

            # Process through pipeline (context_offset_s filters context words)
            t4 = time.perf_counter()
            transcription = self._process_pipeline(
                segments_list,
                start_time,
                end_time,
                context_offset_s,
            )
            t5 = time.perf_counter()

            # Update context AFTER successful transcription
            self.context_carryover.update_context(audio_array)

            # Log timing breakdown
            logger.info(
                f"⏱️  Transcription timing (audio={new_audio_duration:.2f}s): "
                f"prepare={t1 - t0:.3f}s, "
                f"whisper={t3 - t2:.3f}s, "
                f"pipeline={t5 - t4:.3f}s, "
                f"total={t5 - t0:.3f}s"
            )

            return transcription

        except Exception as e:
            logger.error(f"Transcription failed: {e}")
            return None

    def _process_pipeline(
        self,
        segments: list,
        start_time: float,
        end_time: float,
        context_offset_s: float,
    ) -> TranscriptionResult | None:
        """
        Process Whisper segments through word pipeline.

        Pipeline stages:
        1. Extract words from segments
        2. Filter by context (if context carryover enabled)
        3. Filter by probability (quality filter)
        4. Detect hallucinations (future)
        5. Convert to final format
        """
        # Reset metrics for this run
        self._metrics = PipelineMetrics()
        metrics = self._metrics

        # === PIPELINE ===

        # Stage 1: Extract
        words = extract_words(segments, metrics)

        if not words:
            return None

        # Stage 2: Context filter (offset=0.0 in Phase 1)
        words = filter_by_context(words, context_offset_s, metrics)

        if not words:
            return None

        # Stage 3: Probability filter
        words = filter_by_probability(
            words,
            threshold=self.min_word_probability,
            metrics=metrics,
            collect_removed=True,
        )

        if not words:
            return None

        # Stage 4: Hallucination detection (placeholder)
        words = detect_hallucinations(words, metrics)

        if not words:
            return None

        # Stage 5: Convert to final format
        timed_words = to_timed_words(words, start_time, metrics)

        # Build text from filtered words (fixes text/word divergence bug)
        full_text = build_text_from_words(words)

        if not full_text:
            return None

        # Calculate average probability
        avg_probability = (
            sum(w.probability for w in words) / len(words) if words else 0.5
        )

        # Log pipeline metrics
        metrics.log_summary()

        # Final hallucination check (segment-level)
        duration = end_time - start_time
        if duration < 0.8 and avg_probability < 0.25:
            logger.warning(
                f"Dropped segment: {duration:.2f}s, prob={avg_probability:.3f}"
            )
            return None

        return TranscriptionResult(
            text=full_text,
            start_time=start_time,
            end_time=end_time,
            probability=avg_probability,
            language=self.detected_language or "unknown",
            words=timed_words if timed_words else None,
        )
