"""
Diffusers Engine Schema - Flux.2 models via diffusers.

Capabilities:
    - Devices: gpu only (with optional CPU offload for VRAM savings)
    - Profile type: named (e.g., 'default', 'offload')
    - Vision support: no (image generation output)
    - Output type: image
    - Caption upsampling: improved prompt adherence (0.15 recommended)

Loader Parameters:
    - torch_dtype: Data type (float16, bfloat16)
    - cpu_offload: Offload layers to CPU RAM (still uses GPU)
    - caption_upsample_temperature: Caption upsampling strength (0.15 recommended)

Note: FLUX.2 is 32B params, requires ~28GB VRAM without offload.
cpu_offload is a GPU optimization, NOT a CPU-only mode.
The model still requires a GPU; offload just reduces VRAM usage
by storing some tensors in system RAM.
"""

from typing import Any

from .schema import BaseEngineSchema
from .types import ConvertedModel, ValidationIssue


class DiffusersSchema(BaseEngineSchema):
    """Schema for diffusers engine (Flux.2 models)."""

    engine = "diffusers"
    formats = frozenset({"flux2"})
    supported_devices = frozenset({"gpu"})  # GPU only, offload is a GPU variant
    profile_type = "named"
    supports_vision = False

    def get_default_loader(self) -> dict[str, Any]:
        return {
            "torch_dtype": "float16",
            "caption_upsample_temperature": 0.15,  # FLUX.2 recommended
        }

    def validate(
        self,
        model_id: str,
        entry: dict[str, Any],
    ) -> list[ValidationIssue]:
        issues = self._validate_common(model_id, entry)
        if any(i.severity == "error" for i in issues):
            return issues

        devices = entry.get("devices", {})

        # Diffusers only supports GPU
        if "cpu" in devices:
            issues.append(
                ValidationIssue(
                    model_id=model_id,
                    severity="error",
                    message="Diffusers does not support CPU-only mode",
                    field="devices.cpu",
                    fix=(
                        "Remove cpu device. "
                        "Use cpu_offload in GPU profiles for VRAM savings."
                    ),
                )
            )

        # Validate GPU profiles
        gpu_device = devices.get("gpu", {})
        issues.extend(self._validate_profiles(model_id, "gpu", gpu_device))

        # Validate profile structure
        for profile_name, profile in gpu_device.get("profiles", {}).items():
            # Check cpu_offload is boolean if present
            cpu_offload = profile.get("cpu_offload")
            if cpu_offload is not None and not isinstance(cpu_offload, bool):
                issues.append(
                    ValidationIssue(
                        model_id=model_id,
                        severity="error",
                        message=f"Profile '{profile_name}' cpu_offload must be boolean",
                        field=f"devices.gpu.profiles.{profile_name}.cpu_offload",
                        fix="Set cpu_offload to true or false",
                    )
                )

        return issues

    def convert(
        self,
        model_id: str,
        entry: dict[str, Any],
    ) -> ConvertedModel | None:
        metadata = entry.get("metadata", {})
        download = entry.get("download", {})
        loader = entry.get("loader", {})
        devices = entry.get("devices", {})

        base_loader = {**self.get_default_loader(), **loader}

        profiles: dict[str, dict[str, Any]] = {}
        gpu_device = devices.get("gpu", {})

        for profile_name, prof in gpu_device.get("profiles", {}).items():
            profile_loader = {}

            # cpu_offload is a loader parameter
            if "cpu_offload" in prof:
                profile_loader["cpu_offload"] = prof["cpu_offload"]

            profiles[profile_name] = {
                "loader": profile_loader,
                "resources": {
                    "vram_mb": prof.get("vram_mb", 0),
                    "ram_mb": prof.get("ram_mb", 0),
                },
            }

        if not profiles:
            return None

        return ConvertedModel(
            info=self._build_info(model_id, metadata, download),
            base_loader=base_loader,
            profiles=profiles,
            cpu_profiles=None,
        )
