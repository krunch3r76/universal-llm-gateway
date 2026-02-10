"""Audio preprocessing utilities for Whisper engine."""

from universal_logging import get_logger
import numpy as np

logger = get_logger(__name__)

# Constants
TARGET_SAMPLE_RATE = 16000
MAX_AUDIO_DURATION_SECONDS = 30 * 60  # 30 minutes max


def load_and_preprocess(
    file_path: str,
    target_sample_rate: int = TARGET_SAMPLE_RATE,
) -> tuple[np.ndarray, float]:
    """
    Load audio file and preprocess for Whisper.

    Args:
        file_path: Path to audio file
        target_sample_rate: Target sample rate (default 16kHz)

    Returns:
        Tuple of (audio_array, duration_seconds)

    Raises:
        ValueError: If audio is too long or invalid
        IOError: If file cannot be read
    """
    import soundfile as sf

    # Read audio file
    audio, sample_rate = sf.read(file_path)

    # Convert to mono if stereo
    if audio.ndim > 1:
        audio = np.mean(audio, axis=1)

    # Resample to target rate if needed
    if sample_rate != target_sample_rate:
        audio = resample_audio(audio, sample_rate, target_sample_rate)

    # Ensure float32
    audio = audio.astype(np.float32)

    # Validate duration
    duration = len(audio) / target_sample_rate
    if duration > MAX_AUDIO_DURATION_SECONDS:
        raise ValueError(
            f"Audio too long: {duration:.1f}s > {MAX_AUDIO_DURATION_SECONDS}s max"
        )

    logger.debug(f"Preprocessed audio: {len(audio)} samples, {duration:.2f}s")
    return audio, duration


def resample_audio(
    audio: np.ndarray,
    orig_sr: int,
    target_sr: int,
) -> np.ndarray:
    """
    Resample audio to target sample rate.

    Args:
        audio: Input audio array
        orig_sr: Original sample rate
        target_sr: Target sample rate

    Returns:
        Resampled audio array
    """
    import scipy.signal

    target_length = int(len(audio) * target_sr / orig_sr)
    resampled = scipy.signal.resample(audio, target_length)

    logger.debug(
        f"Resampled: {orig_sr}Hz → {target_sr}Hz "
        f"({len(audio)} → {len(resampled)} samples)"
    )
    return resampled.astype(np.float32)


def validate_audio_bytes(
    audio_bytes: bytes,
    expected_dtype: np.dtype = np.dtype(np.int16),
) -> np.ndarray:
    """
    Validate and convert raw audio bytes to numpy array.

    Args:
        audio_bytes: Raw PCM audio bytes
        expected_dtype: Expected numpy dtype (default int16)

    Returns:
        Audio as numpy array

    Raises:
        ValueError: If audio bytes are invalid
    """
    if not audio_bytes:
        raise ValueError("Empty audio bytes")

    # Calculate expected samples
    bytes_per_sample = expected_dtype.itemsize
    if len(audio_bytes) % bytes_per_sample != 0:
        raise ValueError(
            f"Audio bytes length {len(audio_bytes)} not divisible by "
            f"sample size {bytes_per_sample}"
        )

    return np.frombuffer(audio_bytes, dtype=expected_dtype)


def normalize_audio(audio: np.ndarray) -> np.ndarray:
    """
    Normalize audio to float32 in range [-1.0, 1.0].

    Args:
        audio: Input audio (any dtype)

    Returns:
        Normalized float32 audio
    """
    if audio.dtype == np.int16:
        return audio.astype(np.float32) / 32768.0
    elif audio.dtype == np.int32:
        return audio.astype(np.float32) / 2147483648.0
    elif audio.dtype == np.float32:
        # Already float32, just clip to valid range
        return np.clip(audio, -1.0, 1.0)
    elif audio.dtype == np.float64:
        return np.clip(audio.astype(np.float32), -1.0, 1.0)
    else:
        raise ValueError(f"Unsupported audio dtype: {audio.dtype}")

