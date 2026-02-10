"""
Schema Types - Data classes for catalog schema validation and conversion.

Used by all engine schemas for consistent validation and conversion results.
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ValidationIssue:
    """Catalog validation issue."""

    model_id: str
    severity: str  # "error" | "warning"
    message: str
    field: str | None = None
    fix: str | None = None


@dataclass
class ProfileSpec:
    """Specification for a single profile."""

    key: str  # Context length or named key
    resources: dict[str, int]  # vram_mb, ram_mb
    loader_overrides: dict[str, Any] = field(default_factory=dict)


@dataclass
class DeviceConfig:
    """Configuration for a device type."""

    device: str  # gpu, cpu, hybrid
    profiles: dict[str, ProfileSpec] = field(default_factory=dict)
    base_loader: dict[str, Any] = field(default_factory=dict)


@dataclass
class ConvertedModel:
    """Model in registry-ready format."""

    info: dict[str, Any]
    base_loader: dict[str, Any]
    profiles: dict[str, dict[str, Any]]
    cpu_profiles: dict[str, dict[str, Any]] | None = None
