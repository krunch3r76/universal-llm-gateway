"""
Frame alignment utilities for Silero VAD.
Extracted from silero.py for SLOC compliance.
"""

from typing import Any

import numpy as np

# Constants for 32ms frame alignment (Silero VAD requirement)
SILERO_FRAME_MS = 32
SILERO_FRAME_SAMPLES_16K = 512  # 512 samples at 16kHz
SILERO_FRAME_SAMPLES_8K = 256  # 256 samples at 8kHz
SILERO_FRAME_BYTES_16K = SILERO_FRAME_SAMPLES_16K * 2  # 1024 bytes (16-bit audio)
SILERO_FRAME_BYTES_8K = SILERO_FRAME_SAMPLES_8K * 2  # 512 bytes (16-bit audio)


def calculate_frame_boundaries(
    duration_ms: float, sample_rate: int, frame_samples: int
) -> dict[str, Any]:
    """
    Calculate 32ms frame boundaries for a given duration.

    Args:
        duration_ms: Duration in milliseconds
        sample_rate: Audio sample rate
        frame_samples: Frame size in samples

    Returns:
        Dictionary with boundary calculations
    """
    duration_seconds = duration_ms / 1000
    total_samples = int(sample_rate * duration_seconds)
    total_bytes = total_samples * 2

    # Calculate complete 32ms frames
    complete_frames = total_samples // frame_samples
    complete_frame_samples = complete_frames * frame_samples
    complete_frame_bytes = complete_frame_samples * 2

    # Calculate remainder
    remainder_samples = total_samples - complete_frame_samples
    remainder_bytes = remainder_samples * 2
    remainder_ms = (remainder_samples / sample_rate) * 1000

    return {
        "duration_ms": duration_ms,
        "total_samples": total_samples,
        "total_bytes": total_bytes,
        "complete_frames": complete_frames,
        "complete_frame_samples": complete_frame_samples,
        "complete_frame_bytes": complete_frame_bytes,
        "remainder_samples": remainder_samples,
        "remainder_bytes": remainder_bytes,
        "remainder_ms": remainder_ms,
        "frame_aligned_duration_ms": complete_frames * SILERO_FRAME_MS,
        "frame_size_samples": frame_samples,
        "frame_size_bytes": frame_samples * 2,
    }


def align_to_frame_boundary(
    byte_offset: int, frame_samples: int, align_up: bool = False
) -> int:
    """
    Align a byte offset to the nearest 32ms frame boundary.

    Args:
        byte_offset: Original byte offset
        frame_samples: Frame size in samples
        align_up: If True, round up to next boundary; if False, round down

    Returns:
        Aligned byte offset
    """
    samples = byte_offset // 2

    if align_up:
        # Round up to next frame boundary
        aligned_samples = (
            (samples + frame_samples - 1) // frame_samples
        ) * frame_samples
    else:
        # Round down to previous frame boundary
        aligned_samples = (samples // frame_samples) * frame_samples

    return aligned_samples * 2


def get_audio_frame_info(
    audio: np.ndarray, sample_rate: int, frame_samples: int
) -> dict[str, Any]:
    """
    Get 32ms frame alignment information for audio array.

    Args:
        audio: Audio array to analyze
        sample_rate: Audio sample rate
        frame_samples: Frame size in samples

    Returns:
        Dictionary with frame alignment info
    """
    audio_duration_ms = (len(audio) / sample_rate) * 1000
    return calculate_frame_boundaries(audio_duration_ms, sample_rate, frame_samples)


def split_into_frames(
    audio: np.ndarray, frame_samples: int, require_complete_frames: bool = True
) -> list[np.ndarray]:
    """
    Split audio into 32ms frames suitable for Silero VAD processing.

    Args:
        audio: Input audio array
        frame_samples: Frame size in samples
        require_complete_frames: If True, only return complete frames

    Returns:
        List of audio frames, each exactly frame_samples long
    """
    frames = []
    total_samples = len(audio)

    # Process complete frames
    for i in range(0, total_samples - frame_samples + 1, frame_samples):
        frame = audio[i : i + frame_samples]
        frames.append(frame)

    # Handle remainder if requested
    if not require_complete_frames:
        remainder_start = (total_samples // frame_samples) * frame_samples
        if remainder_start < total_samples:
            remainder = audio[remainder_start:]
            # Pad to complete frame
            padded_frame = np.zeros(frame_samples, dtype=audio.dtype)
            padded_frame[: len(remainder)] = remainder
            frames.append(padded_frame)

    return frames


def analyze_speech_activity(
    speech_timestamps: list[dict[str, Any]],
    audio_duration: float,
    sample_rate: int = 16000,
) -> dict[str, Any]:
    """
    Analyze speech activity from Silero timestamps.

    Args:
        speech_timestamps: List of speech segment dictionaries
        audio_duration: Total audio duration in seconds
        sample_rate: Audio sample rate

    Returns:
        Dictionary with speech activity statistics
    """
    if not speech_timestamps:
        return {
            "total_duration": audio_duration,
            "speech_duration": 0.0,
            "silence_duration": audio_duration,
            "speech_ratio": 0.0,
            "silence_ratio": 1.0,
            "speech_segments": 0,
            "average_speech_duration": 0.0,
            "average_silence_duration": audio_duration,
            "longest_speech_segment": 0.0,
            "longest_silence_segment": audio_duration,
        }

    # Calculate speech statistics
    total_speech_duration = sum(segment["duration"] for segment in speech_timestamps)
    total_silence_duration = audio_duration - total_speech_duration

    speech_durations = [segment["duration"] for segment in speech_timestamps]

    # Calculate silence gaps
    silence_gaps = []
    if len(speech_timestamps) > 1:
        for i in range(len(speech_timestamps) - 1):
            gap = speech_timestamps[i + 1]["start"] - speech_timestamps[i]["end"]
            if gap > 0:
                silence_gaps.append(gap)

    # Add leading/trailing silence
    if speech_timestamps[0]["start"] > 0:
        silence_gaps.append(speech_timestamps[0]["start"])
    if speech_timestamps[-1]["end"] < audio_duration:
        silence_gaps.append(audio_duration - speech_timestamps[-1]["end"])

    return {
        "total_duration": audio_duration,
        "speech_duration": total_speech_duration,
        "silence_duration": total_silence_duration,
        "speech_ratio": total_speech_duration / audio_duration,
        "silence_ratio": total_silence_duration / audio_duration,
        "speech_segments": len(speech_timestamps),
        "average_speech_duration": float(np.mean(speech_durations)),
        "average_silence_duration": float(np.mean(silence_gaps))
        if silence_gaps
        else 0.0,
        "longest_speech_segment": max(speech_durations),
        "longest_silence_segment": max(silence_gaps) if silence_gaps else 0.0,
        "speech_timestamps": speech_timestamps,
    }
