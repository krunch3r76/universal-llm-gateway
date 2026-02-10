"""Audio buffer management for sliding window processing."""

from universal_logging import get_logger
from typing import TYPE_CHECKING

import numpy as np

from ..buffer import EfficientAudioBuffer
from ..types import TranscriptionResult

if TYPE_CHECKING:
    from .core import SlidingWindowBuffer

logger = get_logger(__name__)

SAMPLE_RATE = 16000

# Adaptive preservation bounds (ms)
MIN_PRESERVATION_MS = 80  # Fast speech floor
MAX_PRESERVATION_MS = 180  # Slow speech ceiling
DEFAULT_PRESERVATION_MS = 100  # Fallback when no word data


def calculate_adaptive_preservation_ms(result: TranscriptionResult | None) -> int:
    """
    Calculate preservation duration based on speech rate.

    Uses average word duration from transcription to adapt:
    - Fast speech (short words): less preservation needed
    - Slow speech (long words): more preservation needed

    Args:
        result: Transcription result with word timestamps, or None

    Returns:
        Preservation duration in milliseconds
    """
    if not result or not result.words or len(result.words) < 2:
        return DEFAULT_PRESERVATION_MS

    # Calculate average word duration from timestamps
    words = result.words
    total_duration_ms = 0
    valid_words = 0

    for word in words:
        duration_ms = (word.end_us - word.start_us) / 1000
        if duration_ms > 0:
            total_duration_ms += duration_ms
            valid_words += 1

    if valid_words == 0:
        return DEFAULT_PRESERVATION_MS

    avg_word_duration_ms = total_duration_ms / valid_words

    # Scale preservation based on speech rate:
    # - Fast speech (~150ms/word): use MIN_PRESERVATION_MS
    # - Slow speech (~400ms/word): use MAX_PRESERVATION_MS
    # Linear interpolation between
    fast_threshold_ms = 150
    slow_threshold_ms = 400

    if avg_word_duration_ms <= fast_threshold_ms:
        preservation = MIN_PRESERVATION_MS
    elif avg_word_duration_ms >= slow_threshold_ms:
        preservation = MAX_PRESERVATION_MS
    else:
        # Linear interpolation
        ratio = (avg_word_duration_ms - fast_threshold_ms) / (
            slow_threshold_ms - fast_threshold_ms
        )
        preservation = int(
            MIN_PRESERVATION_MS + ratio * (MAX_PRESERVATION_MS - MIN_PRESERVATION_MS)
        )

    logger.debug(
        f"Adaptive preservation: {preservation}ms "
        f"(avg word duration: {avg_word_duration_ms:.0f}ms)"
    )

    return preservation


class AudioManager:
    """Manages audio buffer operations for sliding window processing."""

    def __init__(self, parent: "SlidingWindowBuffer"):
        """Initialize audio manager with parent reference."""
        self.parent = parent
        self.audio_buffer = EfficientAudioBuffer()
        self.total_samples_processed = 0

    def add_audio(self, audio_data: np.ndarray) -> None:
        """Add audio data to the buffer."""
        self.audio_buffer.append(audio_data)

    def get_buffer_duration_seconds(self) -> float:
        """Get current buffer duration in seconds."""
        return len(self.audio_buffer) / 2 / SAMPLE_RATE

    def get_buffer_bytes(self) -> int:
        """Get current buffer size in bytes."""
        return len(self.audio_buffer)

    def has_minimum_audio(self, min_window_size: int) -> bool:
        """Check if buffer has minimum required audio."""
        min_required_bytes = min_window_size * 2
        return len(self.audio_buffer) >= min_required_bytes

    def get_buffer_audio_float32(self) -> np.ndarray:
        """Get buffer audio as float32 array."""
        return self.audio_buffer.to_numpy_float32()

    def get_buffer_audio_int16(self) -> np.ndarray:
        """Get buffer audio as int16 array."""
        return self.audio_buffer.to_numpy_int16()

    def extract_audio_up_to(self, boundary_offset: int) -> bytes:
        """Extract audio bytes up to specified offset."""
        return self.audio_buffer[:boundary_offset]

    def remove_processed_audio(
        self,
        boundary_offset: int,
        preservation_ms: int = 100,
    ) -> None:
        """
        Remove processed audio, keeping minimal trailing context.

        Preserves ~100ms of trailing audio to prevent word loss at boundaries.
        Overlap corrector handles any resulting identical-word duplications.

        Args:
            boundary_offset: Byte offset up to which audio should be removed
            preservation_ms: Trailing audio to preserve (default 100ms)
        """
        if boundary_offset <= 0 or boundary_offset >= len(self.audio_buffer):
            return

        processed_samples = boundary_offset // 2
        self.total_samples_processed += processed_samples

        preservation_samples = int(SAMPLE_RATE * preservation_ms / 1000)
        preservation_bytes = preservation_samples * 2

        # Keep minimal trailing audio for word continuity
        preservation_start = max(0, boundary_offset - preservation_bytes)
        preservation_audio = self.audio_buffer[preservation_start:boundary_offset]

        # Remove processed audio and prepend preserved portion
        self.audio_buffer.remove_front(boundary_offset)
        self.audio_buffer.prepend(preservation_audio)

        logger.debug(
            f"🔄 Removed {boundary_offset} bytes ({processed_samples / SAMPLE_RATE:.2f}s), "
            f"preserved {len(preservation_audio) / 2 / SAMPLE_RATE:.3f}s"
        )

    def clear_buffer(self) -> None:
        """Clear the audio buffer."""
        self.audio_buffer.clear()

    def advance_stream_time(self, samples: int) -> None:
        """Advance stream time without keeping audio."""
        self.total_samples_processed += samples

    def get_start_time(self) -> float:
        """Get start time of current buffer in seconds."""
        return self.total_samples_processed / SAMPLE_RATE

    def extract_frame_aligned_audio(
        self,
        duration_ms: int,
        frame_size_samples: int,
    ) -> np.ndarray | None:
        """
        Extract frame-aligned audio chunk.

        Args:
            duration_ms: Desired duration in milliseconds
            frame_size_samples: Frame size in samples for alignment

        Returns:
            Frame-aligned audio array or None if insufficient audio
        """
        if duration_ms <= 0:
            logger.warning(f"Invalid duration: {duration_ms}ms must be positive")
            return None

        duration_samples = int(SAMPLE_RATE * duration_ms / 1000)

        # Align to frame boundaries (round up)
        aligned_samples = (
            (duration_samples + frame_size_samples - 1) // frame_size_samples
        ) * frame_size_samples
        aligned_bytes = aligned_samples * 2

        if len(self.audio_buffer) < aligned_bytes:
            logger.debug(
                f"Insufficient audio: {len(self.audio_buffer)} bytes < {aligned_bytes} bytes"
            )
            return None

        audio_bytes = self.audio_buffer[0:aligned_bytes]
        return np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0

    def align_to_frame_boundary(
        self,
        byte_offset: int,
        frame_size_samples: int,
    ) -> int:
        """
        Align byte offset to frame boundary.

        Args:
            byte_offset: Original byte offset
            frame_size_samples: Frame size in samples

        Returns:
            Aligned byte offset
        """
        samples = byte_offset // 2
        aligned_samples = (samples // frame_size_samples) * frame_size_samples
        return aligned_samples * 2
