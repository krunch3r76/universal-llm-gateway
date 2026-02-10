"""Patch workflow configuration for llama-cpp-python builds."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class PatchDefinition:
    """
    Single patch to apply to llama-cpp-python source.

    Attributes:
        file_path: Relative path from source root (e.g., "llama_cpp/llama.py")
        old_pattern: Exact string to find and replace
        new_template: Replacement string (may contain {placeholders})
        description: Human-readable description for logging
        optional: If True, missing file/pattern is not an error
    """

    file_path: str
    old_pattern: str
    new_template: str
    description: str
    optional: bool = False


@dataclass(frozen=True, slots=True)
class PatchWorkflow:
    """
    Collection of patches for a llama-cpp-python version range.

    Attributes:
        name: Workflow identifier (e.g., "v0.3")
        version_pattern: Regex pattern matching version strings
        patches: List of patches to apply
        verified_working: If True, patches are applied by default
        notes: Optional notes about this workflow
    """

    name: str
    version_pattern: str
    patches: list[PatchDefinition] = field(default_factory=list)
    verified_working: bool = False
    notes: str | None = None
