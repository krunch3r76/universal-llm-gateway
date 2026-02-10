"""FastAPI query parameter definitions for audio streaming endpoint."""

from enum import Enum

from fastapi import Query

from .session_utils import (
    MAX_ALLOWED_INACTIVITY_TIMEOUT_S,
    MAX_ALLOWED_SESSION_TIMEOUT_S,
)

__all__ = [
    "VADMethod",
    "get_model_param",
    "get_session_timeout_param",
    "get_inactivity_timeout_param",
    "get_language_param",
    "get_beam_size_param",
    "get_temperature_param",
    "get_condition_on_previous_text_param",
    "get_vad_method_param",
    "get_min_window_duration_param",
    "get_max_window_duration_param",
    "get_silero_threshold_param",
    "get_silero_min_silence_ms_param",
    "get_energy_threshold_param",
    "get_speech_pad_ms_param",
    "get_silence_retention_ratio_param",
    "get_speech_preservation_ms_param",
    "get_overlap_duration_ms_param",
    "get_use_whisper_vad_param",
    "get_overlap_correction_enabled_param",
    "get_overlap_hold_word_count_param",
    "get_overlap_max_time_gap_ms_param",
    "get_overlap_min_prefix_ratio_param",
    "get_second_pass_enabled_param",
    "get_second_pass_min_silence_duration_ms_param",
    "get_second_pass_scan_step_ms_param",
    "get_second_pass_max_search_depth_ms_param",
    "get_second_pass_leave_behind_ms_param",
    "get_second_pass_vad_method_param",
    "get_min_word_probability_param",
]


class VADMethod(str, Enum):
    """Available VAD methods with fallback chain."""

    SILERO = "silero"  # Best quality, requires CUDA
    WEBRTC = "webrtc"  # Good quality, CPU-only
    ENERGY = "energy"  # Always available, basic


# === Query Parameter Definitions ===
# These are extracted to reduce clutter in the main endpoint function


def get_model_param():
    """Model ID parameter."""
    return Query(..., description="Whisper model ID (e.g., whisper-large-v3)")


def get_session_timeout_param():
    """Session timeout parameter."""
    return Query(
        None,
        description=(
            "Maximum session duration in seconds. "
            "None = server default (0 = unlimited), "
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
            "None = server default (0 = unlimited), "
            "0 = explicit unlimited, "
            f"max = {MAX_ALLOWED_INACTIVITY_TIMEOUT_S}s (1h)"
        ),
    )


def get_language_param():
    """Language parameter."""
    return Query(None, description="Force language (e.g., en, fa)")


def get_beam_size_param():
    """Beam size parameter."""
    return Query(
        None, ge=1, le=20, description="Beam size (int, normalized by Stargate)"
    )


def get_temperature_param():
    """Temperature parameter."""
    return Query(None, description="Temperature JSON array (serialized by Stargate)")


def get_condition_on_previous_text_param():
    """Context flag parameter."""
    return Query(None, description="Context flag (bool, normalized by Stargate)")


def get_vad_method_param():
    """VAD method parameter."""
    return Query(
        None,
        description=(
            "VAD method: silero (GPU), webrtc (CPU), energy (fallback). "
            "Default: silero with automatic fallback."
        ),
    )


def get_min_window_duration_param():
    """Minimum window duration parameter."""
    return Query(
        None,
        ge=1.0,
        le=10.0,
        description=(
            "Minimum window duration before pause detection (seconds). Default: 2.5"
        ),
    )


def get_max_window_duration_param():
    """Maximum window duration parameter."""
    return Query(
        None,
        ge=2.0,
        le=60.0,
        description="Maximum window duration before forced cut (seconds). Default: 8.0",
    )


def get_silero_threshold_param():
    """Silero threshold parameter."""
    return Query(
        None,
        ge=0.1,
        le=0.9,
        description=(
            "Speech probability threshold (0.1-0.9). Lower=more sensitive. Default: 0.5"
        ),
    )


def get_silero_min_silence_ms_param():
    """Silero minimum silence parameter."""
    return Query(
        None,
        ge=100,
        le=3000,
        description="Silence duration before utterance cutoff (ms). Default: 500",
    )


def get_energy_threshold_param():
    """Energy threshold parameter."""
    return Query(
        None,
        ge=0.01,
        le=0.5,
        description="Energy threshold (0.01-0.5). Lower=more sensitive. Default: 0.1",
    )


def get_speech_pad_ms_param():
    """Speech padding parameter."""
    return Query(
        None,
        ge=0,
        le=300,
        description="Padding around detected speech (ms). Default: 100",
    )


def get_silence_retention_ratio_param():
    """Silence retention ratio parameter."""
    return Query(
        None,
        ge=0.0,
        le=1.0,
        description=(
            "Fraction of silence to retain (0.0-1.0). "
            "Lower = split earlier into silence."
        ),
    )


def get_speech_preservation_ms_param():
    """Speech preservation parameter."""
    return Query(
        None,
        ge=50,
        le=500,
        description=(
            "Trailing audio to preserve across windows (ms). Higher = more overlap."
        ),
    )


def get_overlap_duration_ms_param():
    """Overlap duration parameter."""
    return Query(
        None,
        ge=50,
        le=300,
        description="Window overlap duration (ms). Higher = more redundant processing.",
    )


def get_use_whisper_vad_param():
    """Whisper internal VAD parameter."""
    return Query(
        None,
        description="Enable Whisper internal VAD in worker",
    )


def get_overlap_correction_enabled_param():
    """Overlap correction enabled parameter."""
    return Query(
        None,
        description="Enable word overlap correction at chunk boundaries",
    )


def get_overlap_hold_word_count_param():
    """Overlap hold word count parameter."""
    return Query(
        None,
        ge=1,
        le=10,
        description="Number of trailing words to hold for overlap comparison (1-10)",
    )


def get_overlap_max_time_gap_ms_param():
    """Overlap max time gap parameter."""
    return Query(
        None,
        ge=50,
        le=2000,
        description="Max time gap for overlap detection in ms (50-2000)",
    )


def get_overlap_min_prefix_ratio_param():
    """Overlap min prefix ratio parameter."""
    return Query(
        None,
        ge=0.1,
        le=0.9,
        description="Min prefix ratio for cutoff detection (0.1-0.9)",
    )


def get_second_pass_enabled_param():
    """Second-pass boundary search enabled parameter."""
    return Query(
        None,
        description="Enable second-pass boundary search before forced cut",
    )


def get_second_pass_min_silence_duration_ms_param():
    """Second-pass min silence duration parameter."""
    return Query(
        None,
        ge=100,
        le=3000,
        description="Stricter silence requirement for second pass (ms, 100-3000)",
    )


def get_second_pass_scan_step_ms_param():
    """Second-pass scan step parameter."""
    return Query(
        None,
        ge=10,
        le=200,
        description="Finer scan granularity for second pass (ms, 10-200)",
    )


def get_second_pass_max_search_depth_ms_param():
    """Second-pass max search depth parameter."""
    return Query(
        None,
        ge=1000,
        le=15000,
        description="Max search depth for second pass (ms, 1000-15000)",
    )


def get_second_pass_leave_behind_ms_param():
    """Second-pass leave-behind parameter."""
    return Query(
        None,
        ge=50,
        le=1000,
        description="Leave-behind duration for forced fallback (ms, 50-1000)",
    )


def get_second_pass_vad_method_param():
    """Second-pass VAD method override parameter."""
    return Query(
        None,
        description="VAD method override for second pass (silero, webrtc, energy)",
    )


def get_min_word_probability_param():
    """Minimum word probability threshold parameter."""
    return Query(
        None,
        ge=0.0,
        le=1.0,
        description="Minimum word probability threshold (0.0-1.0). Default: 0.15",
    )


def get_adaptive_preservation_enabled_param():
    """Adaptive preservation enabled (mutually exclusive with leave_behind)."""
    return Query(
        None,
        description=(
            "Enable adaptive preservation at natural speech boundaries. "
            "Mutually exclusive with max_window_leave_behind_ms."
        ),
    )


def get_max_window_leave_behind_ms_param():
    """Max-window leave-behind (mutually exclusive with adaptive)."""
    return Query(
        None,
        ge=0,
        le=2000,
        description=(
            "Fixed tail (ms) for max-window forced cuts (default: 0). "
            "Mutually exclusive with adaptive_preservation_enabled."
        ),
    )
