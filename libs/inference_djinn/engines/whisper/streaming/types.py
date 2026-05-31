"""Type definitions for streaming ASR results."""

import time
from dataclasses import dataclass, field


@dataclass(unsafe_hash=True)
class HighResTimedWord:
    """Word with high-resolution timing - handles Whisper's natural timestamp variability."""

    word: str
    start_us: int  # Microseconds since stream start (exact integer)
    end_us: int  # Microseconds since stream start (exact integer)
    probability: float = 0.0  # Word-level probability from faster-whisper

    @property
    def start(self) -> float:
        """Convert to seconds for backward compatibility."""
        return self.start_us / 1_000_000

    @property
    def end(self) -> float:
        """Convert to seconds for backward compatibility."""
        return self.end_us / 1_000_000

    @property
    def duration_us(self) -> int:
        """Duration in microseconds (exact)."""
        return self.end_us - self.start_us

    @property
    def duration(self) -> float:
        """Duration in seconds for compatibility."""
        return self.duration_us / 1_000_000

    def overlaps_with(self, other: "HighResTimedWord") -> bool:
        """Check if this word overlaps with another word (exact comparison)."""
        return not (self.end_us <= other.start_us or self.start_us >= other.end_us)

    def contains_time_us(self, time_us: int) -> bool:
        """Check if the specified time (in microseconds) falls within this word."""
        return self.start_us <= time_us <= self.end_us

    def contains_time(self, time_sec: float) -> bool:
        """Check if the specified time falls within this word."""
        time_us = int(time_sec * 1_000_000)
        return self.contains_time_us(time_us)

    def __str__(self) -> str:
        """String representation showing word, timing, and probability."""
        return (
            f"'{self.word}' {self.start:.3f}s-{self.end:.3f}s "
            f"(dur:{self.duration:.3f}s, prob:{self.probability:.2f})"
        )

    def __repr__(self) -> str:
        """Detailed representation for debugging."""
        return (
            f"HighResTimedWord(word='{self.word}', start_us={self.start_us}, "
            f"end_us={self.end_us}, probability={self.probability:.2f})"
        )

    def to_dict(self) -> dict:
        """Convert word to dictionary for JSON serialization."""
        return {
            "word": self.word,
            "start": self.start,
            "end": self.end,
            "start_us": self.start_us,
            "end_us": self.end_us,
            "duration": self.duration,
            "duration_us": self.duration_us,
            "probability": self.probability,
        }

    def overlap_ratio(self, other: "HighResTimedWord") -> float:
        """Calculate how much these words overlap in time (0.0 to 1.0)."""
        overlap_start = max(self.start_us, other.start_us)
        overlap_end = min(self.end_us, other.end_us)
        overlap_duration = max(0, overlap_end - overlap_start)

        if overlap_duration == 0:
            return 0.0

        total_start = min(self.start_us, other.start_us)
        total_end = max(self.end_us, other.end_us)
        total_duration = total_end - total_start

        return overlap_duration / total_duration if total_duration > 0 else 0.0


@dataclass
class TranscriptionResult:
    """Base class for transcription results from audio processing."""

    text: str
    start_time: float
    end_time: float
    probability: float  # Average probability across all words
    language: str
    words: list[HighResTimedWord] | None = None

    def __str__(self) -> str:
        """String representation showing key result information."""
        duration = self.end_time - self.start_time
        return f"Result({self.start_time:.2f}s-{self.end_time:.2f}s, {duration:.2f}s): '{self.text}'"

    def __repr__(self) -> str:
        """Detailed representation for debugging."""
        return (
            f"TranscriptionResult(start_time={self.start_time:.2f}, "
            f"end_time={self.end_time:.2f}, text='{self.text}', "
            f"probability={self.probability:.2f}, language='{self.language}')"
        )

    @property
    def duration(self) -> float:
        """Duration of the result in seconds."""
        return self.end_time - self.start_time

    def to_dict(self) -> dict:
        """Convert result to dictionary for JSON serialization."""
        result_dict = {
            "text": self.text,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration": self.duration,
            "probability": self.probability,
            "language": self.language,
        }

        if self.words:
            result_dict["words"] = [
                {
                    "word": word.word,
                    "start": word.start,
                    "end": word.end,
                    "duration": word.end - word.start,
                    "probability": word.probability,
                }
                for word in self.words
            ]
            result_dict["average_word_probability"] = sum(
                word.probability for word in self.words
            ) / len(self.words)
        else:
            result_dict["words"] = []
            result_dict["average_word_probability"] = 0.0

        return result_dict

    def get_average_word_probability(self) -> float:
        """Get average probability score across all words."""
        if not self.words:
            return 0.0
        return sum(word.probability for word in self.words) / len(self.words)

    def get_low_probability_words(
        self, threshold: float = 0.5
    ) -> list[HighResTimedWord]:
        """Get words with probability below threshold."""
        if not self.words:
            return []
        return [word for word in self.words if word.probability < threshold]


@dataclass
class StreamingResponse:
    """Clean response structure for WebSocket streaming API."""

    type: str = "transcription"
    text: str = ""
    start_time: float = 0.0
    end_time: float = 0.0
    duration: float = 0.0
    probability: float = 0.0
    language: str = ""
    words: list[dict] = field(default_factory=list)
    service_type: str = ""
    timestamp: float = 0.0

    def __post_init__(self):
        """Initialize default values for computed fields."""
        if self.duration == 0.0:
            self.duration = self.end_time - self.start_time

    @classmethod
    def from_transcription_result(
        cls, result: TranscriptionResult, service_type: str = "natural_boundary"
    ) -> "StreamingResponse":
        """Create StreamingResponse from TranscriptionResult."""
        words_dict = []
        if result.words:
            words_dict = [
                {
                    "word": word.word,
                    "start": word.start,
                    "end": word.end,
                    "duration": word.end - word.start,
                    "probability": word.probability,
                }
                for word in result.words
            ]

        return cls(
            type="transcription",
            text=result.text,
            start_time=result.start_time,
            end_time=result.end_time,
            duration=result.end_time - result.start_time,
            probability=result.probability,
            language=result.language,
            words=words_dict,
            service_type=service_type,
            timestamp=time.time(),
        )

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "type": self.type,
            "text": self.text,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration": self.duration,
            "probability": self.probability,
            "language": self.language,
            "words": self.words,
            "service_type": self.service_type,
            "timestamp": self.timestamp,
        }
