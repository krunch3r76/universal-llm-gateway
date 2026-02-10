"""CTranslate2 Schema - Translation models via CTranslate2 engine."""

from typing import Any

from .schema import BaseEngineSchema
from .types import ConvertedModel, ValidationIssue

__all__ = ["CTranslate2Schema"]


class CTranslate2Schema(BaseEngineSchema):
    """
    Schema for CTranslate2 translation models.

    Supports: gpu, cpu devices
    Profile type: named (translation models use named profiles like "default")
    """

    engine = "ctranslate2"
    formats = frozenset({"ct2"})
    supported_devices = frozenset({"gpu", "cpu"})
    profile_type = "named"

    def get_default_loader(self) -> dict[str, Any]:
        """Get default base_loader params for CTranslate2."""
        return {
            "device": "cuda",
            "compute_type": "float16",
            "inter_threads": 1,
            "intra_threads": 4,
        }

    def get_device_loader_defaults(self, device: str) -> dict[str, Any]:
        """Get device-specific loader defaults for CTranslate2."""
        if device == "gpu":
            return {
                "device": "cuda",
                "compute_type": "float16",
            }
        elif device == "cpu":
            return {
                "device": "cpu",
                "compute_type": "int8",
            }
        return {}

    def validate(self, model_id: str, entry: dict[str, Any]) -> list[ValidationIssue]:
        """Validate CTranslate2 model entry."""
        issues = self._validate_common(model_id, entry)

        # Validate each device's profiles
        devices = entry.get("devices", {})
        for device_name in ("gpu", "cpu"):
            if device_name in devices:
                device_config = devices[device_name]
                issues.extend(
                    self._validate_profiles(model_id, device_name, device_config)
                )

        return issues

    def convert(self, model_id: str, entry: dict[str, Any]) -> ConvertedModel | None:
        """Convert CTranslate2 model to registry format."""
        metadata = entry.get("metadata", {})
        download = entry.get("download", {})
        loader = entry.get("loader", {})
        devices = entry.get("devices", {})

        # Merge default + model-specific loader
        base_loader = {**self.get_default_loader(), **loader}

        # GPU profiles
        profiles: dict[str, dict[str, Any]] = {}
        gpu_device = devices.get("gpu", {})
        gpu_defaults = self.get_device_loader_defaults("gpu")

        for ctx_str, prof in gpu_device.get("profiles", {}).items():
            profiles[str(ctx_str)] = {
                "loader": {
                    "max_input_len": int(ctx_str) if ctx_str.isdigit() else 512,
                    **gpu_defaults,
                    **prof.get("loader", {}),
                },
                "resources": {
                    "vram_mb": prof.get("vram_mb", 0),
                    "ram_mb": prof.get("ram_mb", 0),
                },
            }

        # CPU profiles
        cpu_profiles: dict[str, dict[str, Any]] = {}
        cpu_device = devices.get("cpu", {})
        cpu_defaults = self.get_device_loader_defaults("cpu")

        for ctx_str, prof in cpu_device.get("profiles", {}).items():
            cpu_profiles[str(ctx_str)] = {
                "loader": {
                    "max_input_len": int(ctx_str) if ctx_str.isdigit() else 512,
                    **cpu_defaults,
                    **prof.get("loader", {}),
                },
                "resources": {
                    "ram_mb": prof.get("ram_mb", 0),
                    "vram_mb": 0,
                },
            }

        # Require at least one profile
        if not profiles and not cpu_profiles:
            return None

        return ConvertedModel(
            info=self._build_info(model_id, metadata, download),
            base_loader=base_loader,
            profiles=profiles,
            cpu_profiles=cpu_profiles if cpu_profiles else None,
        )
