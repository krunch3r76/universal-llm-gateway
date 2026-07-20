"""Validation issue dataclasses for model file, profile, and metadata checks."""

from dataclasses import dataclass


@dataclass
class MetadataIssue:
    """Represents a metadata validation error."""

    model_id: str
    field: str
    issue: str
    severity: str  # 'error' or 'warning'


@dataclass
class ProfileIssue:
    """Represents an incomplete or problematic profile configuration"""

    model_id: str
    profile_key: str
    profile_type: str  # 'profiles' or 'cpu_profiles'
    issue: str
    impact: str
    is_hybrid: bool = False  # True if n_gpu_layers > 0 (partial GPU offload)
