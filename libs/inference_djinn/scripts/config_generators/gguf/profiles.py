"""
Profile Data Models

Defines BaseProfile, SubProfile, and WholeProfile data classes for
representing model configurations at different stages of generation.
"""

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class BaseProfile:
    """
    GGUF model metadata and base information extracted from model file.

    This represents the static information about a model that doesn't
    depend on testing or context-specific parameters.
    """

    name: str
    family: str
    arch: str
    format: str = "gguf"
    path: str | None = None
    enabled: bool = True
    quant: str | None = None
    license: str | None = None
    parameters: int | None = None
    training_context_length: int | None = None
    release_date: str | None = None
    supports_chat_history: bool = True
    input_schema: str = "messages"
    training_cutoff_year: int | None = None
    description: str | None = None
    capabilities: list[str] | None = None
    safety_info: dict[str, Any] | None = None
    default_gpu_context: int | None = None
    default_cpu_context: int | None = None
    # Vision model fields
    vision_architecture: str | None = None
    clip_model_path: str | None = None
    openai_api_fields: dict[str, Any] = field(
        default_factory=lambda: {
            "id": "unknown",
            "object": "model",
            "owned_by": "universal-llm-gateway",
            "permission": ["generate"],
        }
    )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary, excluding None values."""
        return {k: v for k, v in asdict(self).items() if v is not None}


@dataclass
class ProfileData:
    """Single context profile measurement data."""

    n_ctx: int
    n_gpu_layers: int
    ram_mb: int | None = None
    vram_mb: int | None = None


@dataclass
class SubProfile:
    """
    Resource measurements for profiles or cpu_profiles.

    Contains the measured resource usage (RAM/VRAM) for each context
    length in either profiles or cpu_profiles mode.
    """

    profile_type: str  # 'profiles' or 'cpu_profiles'
    measurements: dict[str, ProfileData] = field(default_factory=dict)

    def add_measurement(
        self,
        context: int,
        n_gpu_layers: int,
        ram_mb: int | None = None,
        vram_mb: int | None = None,
    ) -> None:
        """Add a measurement for a specific context."""
        # Import here to avoid circular imports
        from .utils import to_native_int

        # Convert numpy types to native Python int
        context_int = to_native_int(context) or int(context)
        n_gpu_layers_int = to_native_int(n_gpu_layers) or int(n_gpu_layers)
        ram_mb_int = to_native_int(ram_mb) if ram_mb is not None else None
        vram_mb_int = to_native_int(vram_mb) if vram_mb is not None else None

        self.measurements[str(context_int)] = ProfileData(
            n_ctx=context_int,
            n_gpu_layers=n_gpu_layers_int,
            ram_mb=ram_mb_int,
            vram_mb=vram_mb_int,
        )

    def has_valid_measurements(self) -> bool:
        """Check if this SubProfile has at least one measurement with valid resource data."""
        for data in self.measurements.values():
            if data.ram_mb is not None:
                return True
        return False

    def get_failed_contexts(self) -> list[int]:
        """Get list of contexts that failed testing (have None for ram_mb)."""
        failed = []
        for data in self.measurements.values():
            if data.ram_mb is None:
                failed.append(data.n_ctx)
        return sorted(failed)

    def to_dict(self) -> dict[str, dict[str, Any]]:
        """Convert measurements to YAML-compatible format."""
        result = {}
        for ctx_str, data in self.measurements.items():
            result[ctx_str] = {
                "loader": {"n_ctx": data.n_ctx, "n_gpu_layers": data.n_gpu_layers},
                "resources": {"ram_mb": data.ram_mb, "vram_mb": data.vram_mb},
            }
        return result


@dataclass
class WholeProfile:
    """
    Complete model configuration ready for YAML output.

    Combines BaseProfile metadata with SubProfile measurements to create
    the final configuration structure matching the gateway schema.
    """

    info: dict[str, Any]
    base_loader: dict[str, Any]
    profiles: dict[str, dict[str, Any]] | None = None
    cpu_profiles: dict[str, dict[str, Any]] | None = None

    def has_valid_measurements(self) -> bool:
        """Check if this WholeProfile has at least one valid measurement with resource data."""
        # Check profiles section
        if self.profiles:
            for profile_data in self.profiles.values():
                if profile_data.get("resources", {}).get("ram_mb") is not None:
                    return True

        # Check cpu_profiles section
        if self.cpu_profiles:
            for profile_data in self.cpu_profiles.values():
                if profile_data.get("resources", {}).get("ram_mb") is not None:
                    return True

        return False

    def get_failed_contexts(self) -> list[int]:
        """Get list of contexts that failed testing (have None for ram_mb)."""
        failed = []

        # Check profiles section
        if self.profiles:
            for profile_data in self.profiles.values():
                if profile_data.get("resources", {}).get("ram_mb") is None:
                    failed.append(profile_data.get("loader", {}).get("n_ctx"))

        # Check cpu_profiles section
        if self.cpu_profiles:
            for profile_data in self.cpu_profiles.values():
                if profile_data.get("resources", {}).get("ram_mb") is None:
                    failed.append(profile_data.get("loader", {}).get("n_ctx"))

        return sorted([ctx for ctx in failed if ctx is not None])

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for YAML output."""
        result = {
            "info": self.info,
            "base_loader": self.base_loader,
        }
        if self.profiles:
            result["profiles"] = self.profiles
        if self.cpu_profiles:
            result["cpu_profiles"] = self.cpu_profiles
        return result


def merge_profiles(
    base_profile: BaseProfile,
    sub_profile: SubProfile,
    base_loader: dict[str, Any] | None = None,
) -> WholeProfile:
    """
    Merge BaseProfile with SubProfile to create WholeProfile.

    Args:
        base_profile: Model metadata
        sub_profile: Resource measurements
        base_loader: Base loader configuration (optional)

    Returns:
        Complete WholeProfile ready for output
    """
    if base_loader is None:
        base_loader = {
            "n_batch": 512,
            "f16_kv": True,
            "use_mmap": True,
            "use_mlock": True,
            "verbose": False,
        }

    info_dict = base_profile.to_dict()
    measurements_dict = sub_profile.to_dict()

    if sub_profile.profile_type == "profiles":
        return WholeProfile(
            info=info_dict,
            base_loader=base_loader,
            profiles=measurements_dict,
            cpu_profiles=None,
        )
    elif sub_profile.profile_type == "cpu_profiles":
        return WholeProfile(
            info=info_dict,
            base_loader=base_loader,
            profiles=None,
            cpu_profiles=measurements_dict,
        )
    else:
        raise ValueError(f"Invalid profile_type: {sub_profile.profile_type}")


def merge_whole_profiles(
    gpu_profile: WholeProfile | None, cpu_profile: WholeProfile | None
) -> WholeProfile:
    """
    Merge GPU and CPU WholeProfiles into a single profile with both sections.

    Used for incremental builds where GPU and CPU testing are done separately.
    """
    if gpu_profile is None and cpu_profile is None:
        raise ValueError("At least one profile must be provided")

    if gpu_profile is None:
        return cpu_profile

    if cpu_profile is None:
        return gpu_profile

    # Merge both - use GPU's info/base_loader as primary
    merged = WholeProfile(
        info=gpu_profile.info,
        base_loader=gpu_profile.base_loader,
        profiles=gpu_profile.profiles,
        cpu_profiles=cpu_profile.cpu_profiles,
    )

    return merged
