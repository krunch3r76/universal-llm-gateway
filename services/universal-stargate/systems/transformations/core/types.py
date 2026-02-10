"""Core types for transformation system."""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class OutputFormat(StrEnum):
    """Valid transformation output formats."""

    MESSAGES = "messages"
    PROMPT = "prompt"


@dataclass(slots=True, kw_only=True)
class TransformationConfig:
    """Configuration for a transformation."""

    name: str
    basename: str
    description: str
    settings: dict[str, Any]
    enabled: bool = True


@dataclass(slots=True, kw_only=True)
class TransformationResult:
    """Result of a transformation operation."""

    format: OutputFormat
    content: str | list[dict[str, Any]]  # Prompt string or messages list
    metadata: dict[str, Any]
    actions: list[str] = field(default_factory=list)

    @property
    def is_prompt_format(self) -> bool:
        """Check if result is in prompt format."""
        return self.format == OutputFormat.PROMPT

    @property
    def is_messages_format(self) -> bool:
        """Check if result is in messages format."""
        return self.format == OutputFormat.MESSAGES

    @property
    def transformation_applied(self) -> bool:
        """Check if any transformation was applied."""
        return self.metadata.get("transformation_applied", False)
