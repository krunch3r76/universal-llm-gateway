"""Second-pass configuration dataclass for Stargate."""

from dataclasses import dataclass


@dataclass
class SecondPassConfig:
    """Second-pass boundary search configuration."""

    enabled: bool
    min_silence_duration_ms: int
    scan_step_ms: int
    max_search_depth_ms: int
    leave_behind_ms: int
    vad_method: str | None = None

    def to_dict(self) -> dict[str, bool | int | str | None]:
        """Convert to dictionary for URL building and API passing."""
        return {
            "enabled": self.enabled,
            "min_silence_duration_ms": self.min_silence_duration_ms,
            "scan_step_ms": self.scan_step_ms,
            "max_search_depth_ms": self.max_search_depth_ms,
            "leave_behind_ms": self.leave_behind_ms,
            "vad_method": self.vad_method,
        }
