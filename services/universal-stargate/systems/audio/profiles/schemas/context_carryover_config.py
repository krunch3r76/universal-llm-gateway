"""Context carryover configuration dataclass for Stargate."""

from dataclasses import dataclass


@dataclass
class ContextCarryoverConfig:
    """
    Context carryover configuration.

    When enabled, prepends audio from previous segment to give Whisper
    more context at boundaries. Improves accuracy but slightly increases
    processing per segment.
    """

    enabled: bool = False  # Default OFF - opt-in via profile
    duration_s: float = 1.5

    def __post_init__(self):
        """Validate duration_s is in acceptable range."""
        if not 0.0 <= self.duration_s <= 3.0:
            raise ValueError(
                f"duration_s must be between 0.0 and 3.0, got {self.duration_s}"
            )

    def to_dict(self) -> dict[str, bool | float]:
        """Convert to dictionary for URL building and API passing."""
        return {
            "enabled": self.enabled,
            "duration_s": self.duration_s,
        }
