"""PCM audio chunk validation for live transcription WebSocket streams.

Enforces min/max byte limits and 16-bit alignment before chunks are forwarded
to the Whisper worker RPC process_audio_chunk handler.
"""

from dataclasses import dataclass

from ..session_utils import MAX_CHUNK_BYTES, MAX_CONSECUTIVE_ERRORS, MIN_CHUNK_BYTES


@dataclass
class ChunkValidationResult:
    """Outcome of validating an incoming PCM audio chunk."""

    valid: bool
    consecutive_errors: int
    error_code: str | None = None
    error_message: str | None = None
    should_close: bool = False


def validate_audio_chunk(
    audio_bytes: bytes, consecutive_errors: int
) -> ChunkValidationResult:
    """Validate chunk size and PCM alignment, tracking consecutive client errors."""
    if len(audio_bytes) > MAX_CHUNK_BYTES:
        return _invalid_chunk(
            consecutive_errors,
            "chunk_too_large",
            f"Chunk size {len(audio_bytes)} exceeds limit {MAX_CHUNK_BYTES}",
        )

    if len(audio_bytes) < MIN_CHUNK_BYTES:
        return _invalid_chunk(
            consecutive_errors,
            "chunk_too_small",
            f"Chunk size {len(audio_bytes)} below minimum {MIN_CHUNK_BYTES}",
        )

    if len(audio_bytes) % 2 != 0:
        return _invalid_chunk(
            consecutive_errors,
            "invalid_audio_format",
            "Audio bytes must be 16-bit PCM (even number of bytes)",
        )

    return ChunkValidationResult(valid=True, consecutive_errors=0)


def _invalid_chunk(
    consecutive_errors: int, code: str, message: str
) -> ChunkValidationResult:
    next_errors = consecutive_errors + 1
    should_close = next_errors >= MAX_CONSECUTIVE_ERRORS
    error_message = message
    if should_close:
        error_message = "Too many consecutive errors"
        code = "too_many_errors"
    return ChunkValidationResult(
        valid=False,
        consecutive_errors=next_errors,
        error_code=code,
        error_message=error_message,
        should_close=should_close,
    )
