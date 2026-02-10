"""Patch workflow configuration dataclasses."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True, kw_only=True)
class PatchDefinition:
    """
    Single CMake patch definition.

    Patches are text replacements in CMake files that modify vLLM's
    build behavior for architecture-specific optimizations.
    """

    file_path: str
    """Relative path from vLLM source root (e.g., 'csrc/flash_attn/CMakeLists.txt')."""

    old_pattern: str
    """Exact text to find and replace."""

    new_template: str
    """
    Replacement text. Supports template variables:
    - {gpu_arch}: Raw architecture code (e.g., "120")
    - {arch_dotted}: Dotted format (e.g., "12.0")
    """

    description: str
    """Human-readable description of what this patch does."""

    optional: bool = False
    """If True, patch failure is logged as debug (pattern may not exist in version)."""


@dataclass(slots=True, kw_only=True)
class PatchWorkflow:
    """
    Defines patches for a specific vLLM version range.

    Workflows are matched against vLLM versions using `version_pattern`.
    Only workflows with `verified_working=True` are applied by default;
    others require explicit `--apply-patches` opt-in.
    """

    name: str
    """Identifier for this workflow (e.g., 'v0.13')."""

    version_pattern: str
    """
    Regex pattern to match vLLM versions.
    Pattern is matched against normalized version (e.g., "0.13.2", not "v0.13.2").
    """

    patches: list[PatchDefinition] = field(default_factory=list)
    """List of patches to apply for this version."""

    verified_working: bool = False
    """
    If True, patches are applied by default for matching versions.
    If False, patches require --apply-patches flag (experimental).
    """

    notes: str | None = None
    """Documentation about this workflow (why it exists, caveats, etc.)."""

    def __repr__(self) -> str:
        status = "verified" if self.verified_working else "experimental"
        return f"PatchWorkflow({self.name!r}, {status}, {len(self.patches)} patches)"
