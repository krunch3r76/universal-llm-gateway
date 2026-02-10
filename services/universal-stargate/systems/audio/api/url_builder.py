"""Gateway URL builder for audio streaming."""

import json
from urllib.parse import urlencode

from universal_logging import get_logger

logger = get_logger(__name__)


def serialize_temperature_for_url(temps: list[float]) -> str:
    """
    Serialize temperature list to JSON string for URL transport.

    Args:
        temps: List of float temperatures

    Returns:
        JSON string for URL parameter
    """
    return json.dumps(temps)


def build_gateway_url(
    host: str,
    port: int,
    model: str,
    language: str | None,
    session_timeout: int | None,
    inactivity_timeout: int | None,
    beam_size: int,
    temperature: list[float],
    condition_on_previous_text: bool,
    # VAD base parameters
    vad_method: str | None = None,
    min_window_duration: float | None = None,
    max_window_duration: float | None = None,
    silero_threshold: float | None = None,
    silero_min_silence_ms: int | None = None,
    webrtc_aggressiveness: int | None = None,
    webrtc_voice_threshold: float | None = None,
    energy_threshold: float | None = None,
    speech_pad_ms: int | None = None,
    # VAD streaming optimization parameters
    silence_retention_ratio: float | None = None,
    speech_preservation_ms: int | None = None,
    overlap_duration_ms: int | None = None,
    # Whisper internal VAD
    use_whisper_vad: bool | None = None,
    # Word overlap correction (minimal - safety net only)
    overlap_correction_enabled: bool | None = None,
    overlap_hold_word_count: int | None = None,
    overlap_max_time_gap_ms: int | None = None,
    overlap_min_prefix_ratio: float | None = None,
    # Second-pass boundary search
    second_pass_enabled: bool | None = None,
    second_pass_min_silence_duration_ms: int | None = None,
    second_pass_scan_step_ms: int | None = None,
    second_pass_max_search_depth_ms: int | None = None,
    second_pass_leave_behind_ms: int | None = None,
    second_pass_vad_method: str | None = None,
    # Word probability filtering
    min_word_probability: float | None = None,
    # Boundary preservation (mutually exclusive)
    adaptive_preservation_enabled: bool | None = None,
    max_window_leave_behind_ms: int | None = None,
    # Boundary defer strategy
    boundary_defer_enabled: bool | None = None,
    boundary_defer_max_ms: int | None = None,
) -> str:
    """
    Build Gateway WebSocket URL with properly encoded query parameters.

    Uses urllib.parse.urlencode to ensure special characters in model IDs,
    language codes, etc. are properly URL-encoded.

    Stargate normalizes Whisper parameter types before forwarding:
    - beam_size: int (always present, 1-20)
    - temperature: list[float] (always present, JSON-encoded for URL)
    - condition_on_previous_text: bool (always present)

    Args:
        host: Gateway host
        port: Gateway port
        model: Model ID (required)
        language: Optional language override
        session_timeout: Session timeout (None = use Gateway default)
        inactivity_timeout: Inactivity timeout (None = use Gateway default)
        beam_size: Beam size (int, validated 1-20)
        temperature: Temperature list (list[float], validated [0,2])
        condition_on_previous_text: Context flag (bool)
        vad_method: VAD detection method
        min_window_duration: Minimum window duration in seconds (1.0-10.0)
        max_window_duration: Maximum window duration in seconds (2.0-60.0)
        silero_threshold: Silero VAD threshold (0.1-0.9)
        silero_min_silence_ms: Silero minimum silence duration (100-3000ms)
        webrtc_aggressiveness: WebRTC VAD aggressiveness (0-3)
        webrtc_voice_threshold: WebRTC voice detection threshold (0.3-0.9)
        energy_threshold: Energy-based VAD threshold (0.01-0.5)
        speech_pad_ms: Speech padding duration (0-300ms)
        silence_retention_ratio: Fraction of silence to retain (0.0-1.0)
        speech_preservation_ms: Trailing audio to preserve (50-500ms)
        overlap_duration_ms: Window overlap duration (50-300ms)
        overlap_correction_enabled: Enable word overlap correction at chunk boundaries
        overlap_hold_word_count: Words to hold for overlap comparison
        overlap_max_time_gap_ms: Max time gap for overlap detection
        overlap_min_prefix_ratio: Min prefix ratio for cutoff detection
        second_pass_enabled: Enable second-pass boundary search
        second_pass_min_silence_duration_ms: Stricter silence for second pass
        second_pass_scan_step_ms: Finer scan granularity for second pass
        second_pass_max_search_depth_ms: Max search depth for second pass
        second_pass_leave_behind_ms: Leave-behind duration for forced fallback
        second_pass_vad_method: VAD method override for second pass
        min_word_probability: Minimum word probability threshold (0.0-1.0)
        adaptive_preservation_enabled: Adaptive preservation at natural boundaries
        max_window_leave_behind_ms: Fixed tail for max-window forced cuts
        boundary_defer_enabled: Enable one-chunk defer when no clear boundary
        boundary_defer_max_ms: Maximum defer time before forcing cut
    """
    params: dict[str, str | float | int | bool] = {"model": model}

    if language:
        params["language"] = language

    # Add timeout parameters if specified (None = use Gateway default)
    if session_timeout is not None:
        params["session_timeout"] = session_timeout
    if inactivity_timeout is not None:
        params["inactivity_timeout"] = inactivity_timeout

    # Add Whisper transcription parameters
    # (normalized types, always present from profile)
    params["beam_size"] = beam_size
    params["temperature"] = serialize_temperature_for_url(temperature)
    params["condition_on_previous_text"] = condition_on_previous_text

    # Add VAD parameters if specified
    if vad_method is not None:
        params["vad_method"] = vad_method
    if min_window_duration is not None:
        params["min_window_duration"] = min_window_duration
    if max_window_duration is not None:
        params["max_window_duration"] = max_window_duration
    if silero_threshold is not None:
        params["silero_threshold"] = silero_threshold
    if silero_min_silence_ms is not None:
        params["silero_min_silence_ms"] = silero_min_silence_ms
    if webrtc_aggressiveness is not None:
        params["webrtc_aggressiveness"] = webrtc_aggressiveness
    if webrtc_voice_threshold is not None:
        params["webrtc_voice_threshold"] = webrtc_voice_threshold
    if energy_threshold is not None:
        params["energy_threshold"] = energy_threshold
    if speech_pad_ms is not None:
        params["speech_pad_ms"] = speech_pad_ms
    if silence_retention_ratio is not None:
        params["silence_retention_ratio"] = silence_retention_ratio
    if speech_preservation_ms is not None:
        params["speech_preservation_ms"] = speech_preservation_ms
    if overlap_duration_ms is not None:
        params["overlap_duration_ms"] = overlap_duration_ms
    if use_whisper_vad is not None:
        params["use_whisper_vad"] = use_whisper_vad

    # Overlap correction parameters (minimal - safety net only)
    if overlap_correction_enabled is not None:
        params["overlap_correction_enabled"] = overlap_correction_enabled
    if overlap_hold_word_count is not None:
        params["overlap_hold_word_count"] = overlap_hold_word_count
    if overlap_max_time_gap_ms is not None:
        params["overlap_max_time_gap_ms"] = overlap_max_time_gap_ms
    if overlap_min_prefix_ratio is not None:
        params["overlap_min_prefix_ratio"] = overlap_min_prefix_ratio

    # Second-pass boundary search parameters
    if second_pass_enabled is not None:
        params["second_pass_enabled"] = second_pass_enabled
    if second_pass_min_silence_duration_ms is not None:
        params["second_pass_min_silence_duration_ms"] = (
            second_pass_min_silence_duration_ms
        )
    if second_pass_scan_step_ms is not None:
        params["second_pass_scan_step_ms"] = second_pass_scan_step_ms
    if second_pass_max_search_depth_ms is not None:
        params["second_pass_max_search_depth_ms"] = second_pass_max_search_depth_ms
    if second_pass_leave_behind_ms is not None:
        params["second_pass_leave_behind_ms"] = second_pass_leave_behind_ms
    if second_pass_vad_method is not None:
        params["second_pass_vad_method"] = second_pass_vad_method

    # Word probability filtering
    if min_word_probability is not None:
        params["min_word_probability"] = min_word_probability

    # Boundary preservation (mutually exclusive modes)
    if adaptive_preservation_enabled is not None:
        params["adaptive_preservation_enabled"] = adaptive_preservation_enabled
    if max_window_leave_behind_ms is not None:
        params["max_window_leave_behind_ms"] = max_window_leave_behind_ms

    # Boundary defer strategy
    if boundary_defer_enabled is not None:
        params["boundary_defer_enabled"] = boundary_defer_enabled
    if boundary_defer_max_ms is not None:
        params["boundary_defer_max_ms"] = boundary_defer_max_ms

    query_string = urlencode(params)
    return f"ws://{host}:{port}/v1/audio/live_transcribe?{query_string}"
