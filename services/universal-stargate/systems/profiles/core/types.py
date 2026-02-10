"""Core types for profile system."""

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True, kw_only=True)
class ProfileData:
    """Complete profile data for application to requests.

    Attributes:
        name: Resolved profile name (highest priority that was applied)
        params: Engine-specific generation parameters
        system_prompt: System prompt from profile (if defined)
        actions: List of actions taken during profile resolution
        warnings: List of warnings generated during resolution
    """

    name: str | None
    params: dict[str, Any]
    system_prompt: str | None
    actions: list[str]
    warnings: list[str]

    def has_system_prompt(self) -> bool:
        """Check if profile includes a system prompt."""
        return self.system_prompt is not None and len(self.system_prompt.strip()) > 0

    @property
    def has_params(self) -> bool:
        """Check if profile has any parameters."""
        return bool(self.params)
