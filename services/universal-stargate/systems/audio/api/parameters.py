"""Query parameter definitions for audio streaming endpoint."""

from fastapi import Query
from universal_logging import get_logger

logger = get_logger(__name__)

# Server-side timeout caps (match Gateway policy)
MAX_ALLOWED_SESSION_TIMEOUT_S = 86400  # 24 hours max
MAX_ALLOWED_INACTIVITY_TIMEOUT_S = 3600  # 1 hour max


def clamp_timeout(value: int | None, max_allowed: int, name: str) -> int | None:
    """
    Clamp timeout value to server cap before forwarding to Gateway.

    Args:
        value: Client-requested timeout (None = use Gateway default)
        max_allowed: Maximum allowed timeout (server cap)
        name: Timeout name for logging

    Returns:
        Clamped timeout value or None (to use Gateway default)
    """
    if value is None:
        return None
    if value < 0:
        logger.warning(f"Invalid {name}={value} (negative), passing None to Gateway")
        return None
    if value > max_allowed:
        logger.warning(
            f"Requested {name}={value}s exceeds cap={max_allowed}s, clamping"
        )
        return max_allowed
    return value


def get_model_param():
    """Model ID parameter."""
    return Query(..., description="Whisper model ID")


def get_session_timeout_param():
    """Session timeout parameter."""
    return Query(
        None,
        description=(
            "Maximum session duration in seconds. "
            "None = Gateway default (0 = unlimited), "
            "0 = explicit unlimited, "
            f"max = {MAX_ALLOWED_SESSION_TIMEOUT_S}s (24h)"
        ),
    )


def get_inactivity_timeout_param():
    """Inactivity timeout parameter."""
    return Query(
        None,
        description=(
            "Timeout for inactivity/silence in seconds. "
            "None = Gateway default (0 = unlimited), "
            "0 = explicit unlimited, "
            f"max = {MAX_ALLOWED_INACTIVITY_TIMEOUT_S}s (1h)"
        ),
    )


def get_language_param():
    """Language parameter."""
    return Query(None, description="Force language")


def get_whisper_profile_param():
    """Whisper quality profile parameter."""
    return Query(
        None,
        description=(
            "Whisper quality profile. "
            "Streaming (default): 'quality', 'balanced', 'fast'. "
            "File-optimized: 'quality-file', 'balanced-file'. "
            "Overridable by whisper_* parameters."
        ),
    )


def get_whisper_beam_size_param():
    """Whisper beam size parameter."""
    return Query(
        None,
        ge=1,
        le=20,
        description="Beam size for transcription (1-20). Overrides profile default.",
    )


def get_whisper_temperature_param():
    """Whisper temperature parameter."""
    return Query(
        None,
        description=(
            "Temperature for transcription. Single value (e.g., '0.0') or "
            "multi-temperature fallback (e.g., '0.0,0.2,0.4,0.6,0.8,1.0'). "
            "Overrides profile default."
        ),
    )


def get_whisper_condition_on_previous_text_param():
    """Whisper condition on previous text parameter."""
    return Query(
        None,
        description=(
            "Use context from previous segments. "
            "Overrides profile default (true on all profiles)."
        ),
    )


def get_vad_profile_param():
    """VAD profile parameter."""
    return Query(
        None,
        description="VAD profile (sensitive/balanced/aggressive)",
    )


def get_vad_method_param():
    """VAD method parameter."""
    return Query(None, description="VAD method")


def get_silero_threshold_param():
    """Silero threshold parameter."""
    return Query(None, ge=0.1, le=0.9)


def get_silero_min_silence_ms_param():
    """Silero min silence parameter."""
    return Query(None, ge=100, le=3000)


def get_webrtc_aggressiveness_param():
    """WebRTC aggressiveness parameter."""
    return Query(None, ge=0, le=3)


def get_webrtc_voice_threshold_param():
    """WebRTC voice threshold parameter."""
    return Query(None, ge=0.3, le=0.9)


def get_energy_threshold_param():
    """Energy threshold parameter."""
    return Query(None, ge=0.01, le=0.5)


def get_speech_pad_ms_param():
    """Speech padding parameter."""
    return Query(None, ge=0, le=300)


def get_use_whisper_vad_param():
    """Use Whisper VAD parameter."""
    return Query(
        None,
        description="Enable Whisper internal VAD in worker",
    )


def get_overlap_correction_enabled_param():
    """Overlap correction enabled parameter."""
    return Query(
        None,
        description="Enable word overlap correction (profile/default if None)",
    )


def get_min_word_probability_param():
    """Minimum word probability threshold parameter."""
    return Query(
        None,
        ge=0.0,
        le=1.0,
        description="Minimum word probability threshold (0.0-1.0). Default: 0.15",
    )
