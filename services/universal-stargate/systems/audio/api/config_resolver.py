"""Audio profile and configuration resolution for streaming."""

from typing import Any

from universal_logging import get_logger

from utils.model_basename import get_model_profile_config

from ..profiles.manager import AudioProfileManager
from ..profiles.schemas import SecondPassConfig

logger = get_logger(__name__)


class ResolvedAudioConfig:
    """Resolved audio configuration for streaming session."""

    def __init__(
        self,
        session_id: str,
        effective_vad_params: dict[str, Any],
        effective_whisper_params: dict[str, Any],
        active_vad_profile: str,
        active_whisper_profile: str,
        overlap_cfg: dict[str, Any],
        second_pass_cfg: SecondPassConfig,
        min_word_probability: float,
        boundary_cfg: dict[str, Any],
    ):
        self.session_id = session_id
        self.effective_vad_params = effective_vad_params
        self.effective_whisper_params = effective_whisper_params
        self.active_vad_profile = active_vad_profile
        self.active_whisper_profile = active_whisper_profile
        self.overlap_cfg = overlap_cfg
        self.second_pass_cfg = second_pass_cfg
        self.min_word_probability = min_word_probability
        self.boundary_cfg = boundary_cfg

    @property
    def effective_overlap_enabled(self) -> bool | None:
        """Get overlap correction enabled flag."""
        return self.overlap_cfg.get("enabled")

    def extract_whisper_params(self) -> tuple[int, list[float], bool]:
        """Extract typed Whisper parameters with defaults."""
        _beam = self.effective_whisper_params["beam_size"]
        _temp = self.effective_whisper_params["temperature"]
        _ctx = self.effective_whisper_params["condition_on_previous_text"]

        beam_size: int = _beam if isinstance(_beam, int) else 5
        temperature: list[float] = _temp if isinstance(_temp, list) else [0.0]
        context: bool = _ctx if isinstance(_ctx, bool) else True

        return beam_size, temperature, context

    def log_config(self) -> None:
        """Log resolved configuration for observability."""
        logger.info(
            f"[{self.session_id}] VAD profile='{self.active_vad_profile}', "
            f"params={self.effective_vad_params}"
        )
        logger.info(
            f"[{self.session_id}] Whisper profile='{self.active_whisper_profile}', "
            f"params={self.effective_whisper_params}"
        )
        logger.debug(f"[{self.session_id}] Overlap correction: {self.overlap_cfg}")
        logger.debug(
            f"[{self.session_id}] Second-pass config: {self.second_pass_cfg.to_dict()}"
        )
        # Boundary config with all fields
        adaptive = self.boundary_cfg.get("adaptive_preservation_enabled", False)
        leave_behind = self.boundary_cfg.get("max_window_leave_behind_ms", 0)
        defer_enabled = self.boundary_cfg.get("defer_enabled", True)
        defer_max = self.boundary_cfg.get("defer_max_ms", 250)
        logger.info(
            f"[{self.session_id}] Boundary config: "
            f"adaptive={adaptive}, leave_behind={leave_behind}ms, "
            f"defer={defer_enabled}, defer_max={defer_max}ms"
        )


def resolve_audio_config(
    audio_profiles: AudioProfileManager,
    model: str,
    websocket_id: int,
    profile: str | None,
    whisper_profile: str | None,
    vad_method: str | None,
    silero_threshold: float | None,
    silero_min_silence_ms: int | None,
    webrtc_aggressiveness: int | None,
    webrtc_voice_threshold: float | None,
    energy_threshold: float | None,
    speech_pad_ms: int | None,
    whisper_beam_size: int | None,
    whisper_temperature: str | None,
    whisper_condition_on_previous_text: bool | None,
    overlap_correction_enabled: bool | None,
    min_word_probability: float | None,
) -> ResolvedAudioConfig:
    """
    Resolve audio configuration from profiles and overrides.

    Args:
        audio_profiles: Audio profile manager
        model: Model ID
        websocket_id: WebSocket ID for session tracking
        profile: VAD profile name
        whisper_profile: Whisper quality profile name
        vad_method: VAD method override
        silero_threshold: Silero threshold override
        silero_min_silence_ms: Silero min silence override
        webrtc_aggressiveness: WebRTC aggressiveness override
        webrtc_voice_threshold: WebRTC voice threshold override
        energy_threshold: Energy threshold override
        speech_pad_ms: Speech padding override
        whisper_beam_size: Whisper beam size override
        whisper_temperature: Whisper temperature override
        whisper_condition_on_previous_text: Whisper context override
        overlap_correction_enabled: Overlap correction override
        min_word_probability: Minimum word probability threshold override

    Returns:
        Resolved audio configuration
    """
    session_id = f"{model[:12]}_{websocket_id % 10000:04d}"

    # Resolve model defaults
    config = get_model_profile_config(model)
    vad_model_default: str | None = None
    whisper_model_default: str | None = None

    if isinstance(config, dict):
        vad_model_default = config.get("vad_profile")
        whisper_model_default = config.get("whisper_profile")
        logger.debug(
            f"[{session_id}] Model defaults: vad_profile={vad_model_default}, "
            f"whisper_profile={whisper_model_default}"
        )
    else:
        logger.debug(
            f"[{session_id}] No structured model defaults "
            f"(config type: {type(config).__name__})"
        )

    # Apply VAD profile with overrides
    effective_vad_params = audio_profiles.apply_vad_profile(
        profile=profile,
        model_default=vad_model_default,
        vad_method=vad_method,
        silero_threshold=silero_threshold,
        silero_min_silence_ms=silero_min_silence_ms,
        webrtc_aggressiveness=webrtc_aggressiveness,
        webrtc_voice_threshold=webrtc_voice_threshold,
        energy_threshold=energy_threshold,
        speech_pad_ms=speech_pad_ms,
    )

    # Apply Whisper quality profile with overrides
    effective_whisper_params = audio_profiles.apply_whisper_profile(
        profile=whisper_profile,
        model_default=whisper_model_default,
        beam_size=whisper_beam_size,
        temperature=whisper_temperature,
        condition_on_previous_text=whisper_condition_on_previous_text,
    )

    # Resolve final profile names
    active_vad_profile = audio_profiles.resolve_vad_profile_name(
        profile, vad_model_default
    )
    active_whisper_profile = audio_profiles.resolve_whisper_profile_name(
        whisper_profile, whisper_model_default
    )

    # Resolve overlap correction config (full config, not just enabled flag)
    overlap_cfg = audio_profiles.get_overlap_correction_config(
        profile=active_vad_profile,
        enabled=overlap_correction_enabled,
    )

    # Resolve second-pass config
    second_pass_cfg = audio_profiles.get_second_pass_config(
        profile=active_vad_profile,
    )

    # Resolve min_word_probability (explicit param > profile > default)
    resolved_min_word_probability = (
        min_word_probability
        if min_word_probability is not None
        else audio_profiles.default_min_word_probability
    )
    # Validate and clamp to [0.0, 1.0]
    resolved_min_word_probability = max(0.0, min(1.0, resolved_min_word_probability))

    # Resolve boundary preservation config with mutual exclusivity validation
    boundary_cfg = audio_profiles.get_boundary_preservation_config(
        profile=active_vad_profile,
    )

    return ResolvedAudioConfig(
        session_id=session_id,
        effective_vad_params=effective_vad_params,
        effective_whisper_params=effective_whisper_params,
        active_vad_profile=active_vad_profile,
        active_whisper_profile=active_whisper_profile,
        overlap_cfg=overlap_cfg,
        second_pass_cfg=second_pass_cfg,
        min_word_probability=resolved_min_word_probability,
        boundary_cfg=boundary_cfg,
    )
