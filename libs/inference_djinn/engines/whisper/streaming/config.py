"""Configuration classes for streaming ASR."""

from pydantic import BaseModel, field_validator

from ..vad import VADMethod
from .overlap_correction import OverlapCorrectionConfig

# Configuration constants
MAX_WINDOW_DURATION_LIMIT = 60.0  # Maximum allowed window duration in seconds


class SileroParams(BaseModel):
    """Parameters specific to the Silero VAD package."""

    threshold: float | None = None
    min_speech_duration_ms: int | None = None
    min_silence_duration_ms: int | None = None


class EnergyParams(BaseModel):
    """Parameters specific to the simple energy-based VAD implementation."""

    threshold: float | None = None
    min_speech_duration_ms: int | None = None
    min_silence_duration_ms: int | None = None
    frame_size_ms: int | None = None


class WebRTCParams(BaseModel):
    """Parameters specific to the WebRTC VAD wrapper."""

    aggressiveness: int | None = 3  # Maximum noise filtering
    frame_duration_ms: int | None = 20  # Good balance
    voice_threshold: float | None = 0.6  # Moderate voice requirement


class WhisperParams(BaseModel):
    """Parameters for Whisper-internal VAD when enabled."""

    threshold: float | None = None
    min_silence_duration_ms: int | None = None
    speech_pad_ms: int | None = None


class BoundaryConfig(BaseModel):
    """Boundary preservation configuration (mutually exclusive modes)."""

    # Preservation modes (mutually exclusive)
    adaptive_preservation_enabled: bool = False  # Default: disabled (exact cutoff)
    max_window_leave_behind_ms: int = 0  # Default: no fixed leave-behind


class SecondPassConfig(BaseModel):
    """Configuration for second-pass boundary search before max-window fallback."""

    enabled: bool = True  # Enable second-pass search
    min_silence_duration_ms: int = (
        600  # Stricter silence requirement (+100ms over primary)
    )
    scan_step_ms: int = 40  # Finer granularity (vs 100ms primary)
    max_search_depth_ms: int = 6000  # Search further back (6s vs ~4s primary)
    leave_behind_ms: int = 200  # Configurable leave-behind for forced fallback
    vad_method: str | None = None  # Optional VAD override (e.g., "silero")
    guard_window_s: float = 0.5  # Guard window after min_window to reject early cuts


class ContextCarryoverConfig(BaseModel):
    """
    Context carryover configuration.

    When enabled, prepends audio from previous segment to give Whisper
    more context at boundaries. Improves accuracy but slightly increases
    processing per segment.
    """

    enabled: bool = False  # Default OFF - opt-in via profile
    duration_s: float = 1.5

    @field_validator("duration_s")
    @classmethod
    def validate_duration(cls, v: float) -> float:
        if not 0.0 <= v <= 3.0:
            raise ValueError(f"duration_s must be between 0.0 and 3.0, got {v}")
        return v


class EnhancedConfig(BaseModel):
    """Natural boundary processing configuration with configurable VAD."""

    # Language settings
    language: str | None = None  # Force specific language (e.g., 'en', 'fa')

    # VAD method selection
    vad_method: VADMethod = VADMethod.SILERO

    # Natural speech boundary parameters
    min_window_duration: float = 2.5  # Minimum window size before pause detection
    max_window_duration: float = 8.0  # Maximum window size before forcing processing

    # Silence handling parameters
    silence_retention_ratio: float = 0.70  # Fraction of silence to retain (0.0-1.0)
    silence_padding_ratio: float = 0.3  # Fraction of silence to add as padding

    # Nested per-method parameter groups
    silero: SileroParams = SileroParams()
    energy: EnergyParams = EnergyParams()
    webrtc: WebRTCParams = WebRTCParams()
    whisper: WhisperParams = WhisperParams()

    # Misc extra settings
    overlap_duration_ms: int = 120  # Trailing overlap for next window
    use_whisper_vad: bool = False  # Enable Whisper's internal VAD

    # Overlap correction configuration
    overlap_cfg: OverlapCorrectionConfig = OverlapCorrectionConfig()

    # Boundary preservation configuration (mutually exclusive modes)
    boundaries: BoundaryConfig = BoundaryConfig()

    # Second-pass boundary search config
    second_pass: SecondPassConfig = SecondPassConfig()

    # Context carryover configuration
    context_carryover: ContextCarryoverConfig = ContextCarryoverConfig()

    @field_validator("boundaries")
    @classmethod
    def validate_boundary_exclusivity(cls, v):
        """Validate that only one boundary preservation mode is active."""
        if v.adaptive_preservation_enabled and v.max_window_leave_behind_ms > 0:
            raise ValueError(
                f"Boundary preservation modes are mutually exclusive: "
                f"adaptive_preservation_enabled={v.adaptive_preservation_enabled}, "
                f"max_window_leave_behind_ms={v.max_window_leave_behind_ms}. "
                "Only one mode may be active."
            )
        return v

    @field_validator("max_window_duration")
    @classmethod
    def validate_max_window_duration(cls, v, info):
        """Validate max_window_duration is reasonable and > min_window_duration."""
        min_duration = info.data.get("min_window_duration", 2.5)
        if v <= min_duration:
            raise ValueError(
                f"max_window_duration ({v}s) must be > min_window_duration ({min_duration}s)"
            )
        if v > MAX_WINDOW_DURATION_LIMIT:
            raise ValueError(
                f"max_window_duration ({v}s) should not exceed {MAX_WINDOW_DURATION_LIMIT} seconds"
            )
        return v

    @field_validator("silence_retention_ratio")
    @classmethod
    def validate_silence_retention_ratio(cls, v):
        """Validate silence_retention_ratio is between 0.0 and 1.0."""
        if not (0.0 <= v <= 1.0):
            raise ValueError(
                f"silence_retention_ratio ({v}) must be between 0.0 and 1.0"
            )
        return v

    @field_validator("silence_padding_ratio")
    @classmethod
    def validate_silence_padding_ratio(cls, v):
        """Validate silence_padding_ratio is between 0.0 and 1.0."""
        if not (0.0 <= v <= 1.0):
            raise ValueError(f"silence_padding_ratio ({v}) must be between 0.0 and 1.0")
        return v

    @field_validator("vad_method", mode="before")
    @classmethod
    def validate_vad_method(cls, v):
        """Validate and convert vad_method to enum."""
        if isinstance(v, str):
            method_map = {
                "energy": VADMethod.ENERGY,
                "silero": VADMethod.SILERO,
                "webrtc": VADMethod.WEBRTC,
            }
            if v.lower() not in method_map:
                raise ValueError(f"vad_method must be one of {list(method_map.keys())}")
            return method_map[v.lower()]
        elif isinstance(v, VADMethod):
            return v
        else:
            raise ValueError("vad_method must be a VADMethod enum or valid string")


class StreamingConfig(BaseModel):
    """Simplified streaming configuration for external API."""

    language: str | None = None
    vad_method: str = "silero"  # String for API compatibility
    min_window_duration: float = 2.5
    max_window_duration: float = 8.0

    # Nested VAD params (optional, passed through from WebSocket)
    silero: SileroParams | dict[str, float] | None = None
    webrtc: WebRTCParams | dict[str, float | int] | None = None
    energy: EnergyParams | dict[str, float | int] | None = None
    whisper: WhisperParams | dict[str, float | int] | None = None

    def to_enhanced_config(self) -> EnhancedConfig:
        """Convert to EnhancedConfig for internal use."""
        kwargs = {
            "language": self.language,
            "vad_method": self.vad_method,
            "min_window_duration": self.min_window_duration,
            "max_window_duration": self.max_window_duration,
        }

        # Pass through VAD params if provided
        if self.silero:
            kwargs["silero"] = (
                SileroParams(**self.silero)
                if isinstance(self.silero, dict)
                else self.silero
            )
        if self.webrtc:
            kwargs["webrtc"] = (
                WebRTCParams(**self.webrtc)
                if isinstance(self.webrtc, dict)
                else self.webrtc
            )
        if self.energy:
            kwargs["energy"] = (
                EnergyParams(**self.energy)
                if isinstance(self.energy, dict)
                else self.energy
            )
        if self.whisper:
            kwargs["whisper"] = (
                WhisperParams(**self.whisper)
                if isinstance(self.whisper, dict)
                else self.whisper
            )

        return EnhancedConfig(**kwargs)
