"""Session management utilities for audio streaming."""

import json
from typing import Any

from universal_logging import get_logger

__all__ = [
    # Constants
    "MAX_CHUNK_BYTES",
    "MIN_CHUNK_BYTES",
    "MAX_CONSECUTIVE_ERRORS",
    "MAX_ALLOWED_SESSION_TIMEOUT_S",
    "MAX_ALLOWED_INACTIVITY_TIMEOUT_S",
    "DEFAULT_SESSION_TIMEOUT_S",
    "DEFAULT_INACTIVITY_TIMEOUT_S",
    "MONITOR_MODE_RECEIVE_TIMEOUT_S",
    # Functions
    "parse_temperature_json",
    "clamp_timeout",
    "build_timeout_info",
    "build_session_config",
    "cleanup_session",
]

logger = get_logger(__name__)


# === HARDENING CONSTANTS ===
MAX_CHUNK_BYTES = 64 * 1024  # 64KB max per chunk (~2 seconds at 16kHz 16-bit)
MIN_CHUNK_BYTES = 32  # Minimum valid chunk (16 samples)
MAX_CONSECUTIVE_ERRORS = 5  # Close after N consecutive errors

# Server-side timeout caps (prevent unbounded resource pinning)
MAX_ALLOWED_SESSION_TIMEOUT_S = 86400  # 24 hours max
MAX_ALLOWED_INACTIVITY_TIMEOUT_S = 3600  # 1 hour max
DEFAULT_SESSION_TIMEOUT_S = 0  # 0 = unlimited (monitor mode)
DEFAULT_INACTIVITY_TIMEOUT_S = 0  # 0 = unlimited (monitor mode)

# Connection liveness check interval for monitor mode (prevents zombie connections)
# This is NOT an inactivity timeout - it just ensures dead connections are detected
MONITOR_MODE_RECEIVE_TIMEOUT_S = 60  # Check connection liveness every 60s


def parse_temperature_json(temperature_json: str) -> list[float]:
    """
    Parse JSON-encoded temperature array from Stargate.

    Stargate serializes temperature list as JSON for URL transport.
    Gateway deserializes back to list[float] for worker.

    Args:
        temperature_json: JSON string e.g. "[0.0, 0.2, 0.4]"

    Returns:
        List of float temperatures
    """
    return json.loads(temperature_json)


def clamp_timeout(value: int | None, default: int, max_allowed: int, name: str) -> int:
    """
    Clamp timeout value to server policy.

    Args:
        value: Client-requested timeout (None = use default, 0 = unlimited)
        default: Server default timeout
        max_allowed: Maximum allowed timeout (server cap)
        name: Timeout name for logging

    Returns:
        Clamped timeout value
    """
    if value is None:
        return default
    if value < 0:
        logger.warning(f"Invalid {name}={value} (negative), using default={default}")
        return default
    if value > max_allowed:
        logger.warning(
            f"Requested {name}={value}s exceeds cap={max_allowed}s, clamping"
        )
        return max_allowed
    return value


def build_timeout_info(
    session_timeout: int, inactivity_timeout: int
) -> tuple[str, dict[str, Any], bool]:
    """
    Build timeout logging string, limits dict, and monitor mode flag.

    Args:
        session_timeout: Effective session timeout (0 = unlimited)
        inactivity_timeout: Effective inactivity timeout (0 = unlimited)

    Returns:
        Tuple of (log_string, limits_dict, monitor_mode)
    """
    is_monitor_mode = session_timeout == 0 and inactivity_timeout == 0

    # Build log description
    if is_monitor_mode:
        log_str = "Monitor mode: no timeouts"
    else:
        parts = []
        if session_timeout > 0:
            parts.append(f"session: {session_timeout}s")
        else:
            parts.append("session: unlimited")
        if inactivity_timeout > 0:
            parts.append(f"inactivity: {inactivity_timeout}s")
        else:
            parts.append("inactivity: unlimited")
        log_str = f"Timeouts - {', '.join(parts)}"

    # Build limits dict for ready message
    limits = {
        "max_chunk_bytes": MAX_CHUNK_BYTES,
        "session_timeout_s": session_timeout,
        "inactivity_timeout_s": inactivity_timeout,
    }

    return log_str, limits, is_monitor_mode


def build_session_config(  # noqa: PLR0913
    language: str | None,
    beam_size: int | None,
    temperature: str | None,
    condition_on_previous_text: bool | None,
    vad_method: str | None,
    min_window_duration: float | None,
    max_window_duration: float | None,
    silero_threshold: float | None,
    silero_min_silence_ms: int | None,
    energy_threshold: float | None,
    speech_pad_ms: int | None,
    silence_retention_ratio: float | None,
    speech_preservation_ms: int | None,
    overlap_duration_ms: int | None,
    use_whisper_vad: bool | None,
    overlap_correction_enabled: bool | None = None,
    overlap_hold_word_count: int | None = None,
    overlap_max_time_gap_ms: int | None = None,
    overlap_min_prefix_ratio: float | None = None,
    second_pass_enabled: bool | None = None,
    second_pass_min_silence_duration_ms: int | None = None,
    second_pass_scan_step_ms: int | None = None,
    second_pass_max_search_depth_ms: int | None = None,
    second_pass_leave_behind_ms: int | None = None,
    second_pass_vad_method: str | None = None,
    min_word_probability: float | None = None,
    adaptive_preservation_enabled: bool | None = None,
    max_window_leave_behind_ms: int | None = None,
) -> dict[str, Any]:
    """
    Build session configuration (passthrough with type parsing).

    Gateway is passthrough only. Stargate normalizes and applies defaults.
    Gateway deserializes JSON-encoded temperature array from URL.

    Args:
        language: Force specific language (e.g., 'en', 'fa')
        beam_size: Beam size for transcription (int, 1-20)
        temperature: Temperature JSON array string (from Stargate)
        condition_on_previous_text: Context flag (bool, from Stargate)
        vad_method: VAD method string (silero/webrtc/energy)
        min_window_duration: Minimum window duration in seconds (1.0-10.0)
        max_window_duration: Maximum window duration in seconds (2.0-60.0)
        silero_threshold: Silero speech probability threshold (0.1-0.9)
        silero_min_silence_ms: Silence duration before utterance cutoff (100-3000ms)
        energy_threshold: Energy-based detection threshold (0.01-0.5)
        speech_pad_ms: Padding around detected speech (0-300ms)
        silence_retention_ratio: Fraction of silence to retain (0.0-1.0)
        speech_preservation_ms: Trailing audio to preserve across windows (50-500ms)
        overlap_duration_ms: Window overlap duration (50-300ms)
        use_whisper_vad: Enable Whisper internal VAD
        overlap_correction_enabled: Enable word overlap correction
        overlap_hold_word_count: Words to hold for overlap comparison
        overlap_max_time_gap_ms: Max time gap for overlap detection
        overlap_min_prefix_ratio: Min prefix ratio for cutoff detection
        second_pass_enabled: Enable second-pass boundary search
        second_pass_min_silence_duration_ms: Stricter silence for second pass
        second_pass_scan_step_ms: Finer scan granularity for second pass
        second_pass_max_search_depth_ms: Max search depth for second pass
        second_pass_leave_behind_ms: Leave-behind for forced fallback
        second_pass_vad_method: VAD method override for second pass
        min_word_probability: Minimum word probability threshold (0.0-1.0)
        adaptive_preservation_enabled: Adaptive preservation at natural boundaries
        max_window_leave_behind_ms: Fixed tail for max-window forced cuts

    Returns:
        Configuration dict ready for worker RPC call (parsed types)
    """
    session_config: dict[str, Any] = {
        "language": language,
        "min_window_duration": (
            min_window_duration if min_window_duration is not None else 2.5
        ),
        "max_window_duration": (
            max_window_duration if max_window_duration is not None else 8.0
        ),
        "vad_method": vad_method if vad_method else "silero",
    }

    # Add Whisper transcription parameters (parse JSON-encoded temperature)
    if beam_size is not None:
        session_config["beam_size"] = beam_size
    if temperature is not None:
        session_config["temperature"] = parse_temperature_json(temperature)
    if condition_on_previous_text is not None:
        session_config["condition_on_previous_text"] = condition_on_previous_text

    # Add Silero params if provided
    if silero_threshold is not None or silero_min_silence_ms is not None:
        session_config["silero"] = {}
        if silero_threshold is not None:
            session_config["silero"]["threshold"] = silero_threshold
        if silero_min_silence_ms is not None:
            session_config["silero"]["min_silence_duration_ms"] = silero_min_silence_ms

    # Add Energy params if provided
    if energy_threshold is not None:
        session_config["energy"] = {"threshold": energy_threshold}

    # Add Whisper internal VAD params (speech padding)
    if speech_pad_ms is not None:
        session_config["whisper"] = {"speech_pad_ms": speech_pad_ms}

    # Add new streaming optimization params
    if silence_retention_ratio is not None:
        session_config["silence_retention_ratio"] = silence_retention_ratio
    if speech_preservation_ms is not None:
        session_config["speech_preservation_ms"] = speech_preservation_ms
    if overlap_duration_ms is not None:
        session_config["overlap_duration_ms"] = overlap_duration_ms

    # Add Whisper internal VAD param
    if use_whisper_vad is not None:
        session_config["use_whisper_vad"] = use_whisper_vad

    # Add overlap correction config (build dict from all provided params)
    overlap_cfg: dict[str, Any] = {}
    if overlap_correction_enabled is not None:
        overlap_cfg["enabled"] = overlap_correction_enabled
    if overlap_hold_word_count is not None:
        overlap_cfg["hold_word_count"] = overlap_hold_word_count
    if overlap_max_time_gap_ms is not None:
        overlap_cfg["max_time_gap_ms"] = overlap_max_time_gap_ms
    if overlap_min_prefix_ratio is not None:
        overlap_cfg["min_prefix_ratio"] = overlap_min_prefix_ratio
    if overlap_cfg:
        session_config["overlap_cfg"] = overlap_cfg

    # Add second-pass config (build dict from all provided params)
    second_pass_cfg: dict[str, Any] = {}
    if second_pass_enabled is not None:
        second_pass_cfg["enabled"] = second_pass_enabled
    if second_pass_min_silence_duration_ms is not None:
        second_pass_cfg["min_silence_duration_ms"] = second_pass_min_silence_duration_ms
    if second_pass_scan_step_ms is not None:
        second_pass_cfg["scan_step_ms"] = second_pass_scan_step_ms
    if second_pass_max_search_depth_ms is not None:
        second_pass_cfg["max_search_depth_ms"] = second_pass_max_search_depth_ms
    if second_pass_leave_behind_ms is not None:
        second_pass_cfg["leave_behind_ms"] = second_pass_leave_behind_ms
    if second_pass_vad_method is not None:
        second_pass_cfg["vad_method"] = second_pass_vad_method
    if second_pass_cfg:
        session_config["second_pass"] = second_pass_cfg

    # Add word probability filtering threshold
    if min_word_probability is not None:
        session_config["min_word_probability"] = min_word_probability

    # Add boundary preservation config
    boundaries: dict[str, Any] = {}
    if adaptive_preservation_enabled is not None:
        boundaries["adaptive_preservation_enabled"] = adaptive_preservation_enabled
    if max_window_leave_behind_ms is not None:
        boundaries["max_window_leave_behind_ms"] = max_window_leave_behind_ms
    if boundaries:
        session_config["boundaries"] = boundaries

    return session_config


async def cleanup_session(
    worker_controller, model: str, session_id: str, request_id: str
) -> list[dict]:
    """
    Close session and return any pending transcription results.

    Args:
        worker_controller: Worker controller for RPC calls
        model: Model identifier
        session_id: Session identifier
        request_id: Request ID for logging

    Returns:
        List of pending transcription results (may be empty)
    """
    try:
        response = await worker_controller.call_rpc(
            model_id=model,
            method="close_stream_session",
            params={"session_id": session_id},
        )
        pending_results = response.get("pending_results", [])
        if pending_results:
            logger.debug(
                f"[{request_id}] Session {session_id} closed with "
                f"{len(pending_results)} pending results"
            )
        else:
            logger.debug(f"[{request_id}] Session {session_id} cleaned up")
        return pending_results
    except Exception as e:
        logger.warning(f"[{request_id}] Session cleanup failed: {e}")
        raise  # Re-raise so caller can handle it
