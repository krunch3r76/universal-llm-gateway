"""
CrossEncoder Engine Schema — cross-encoder reranker models via sentence_transformers.

Capabilities:
    - Devices: gpu, cpu
    - Profile type: named (e.g., 'default')
    - Vision support: no (text scoring only)

Loader Parameters:
    - device: cuda or cpu
    - trust_remote_code: bool
    - max_length: optional max sequence length
"""

from typing import Any

from .schema import BaseEngineSchema
from .types import ConvertedModel, ValidationIssue


class CrossEncoderSchema(BaseEngineSchema):
    """Schema for cross-encoder engine (reranker models)."""

    engine = "cross-encoder"
    formats = frozenset({"cross-encoder"})
    supported_devices = frozenset({"gpu", "cpu"})
    profile_type = "named"
    supports_vision = False

    def get_default_loader(self) -> dict[str, Any]:
        return {
            "trust_remote_code": True,
        }

    def get_device_loader_defaults(self, device: str) -> dict[str, Any]:
        if device == "gpu":
            return {"device": "cuda"}
        elif device == "cpu":
            return {"device": "cpu"}
        return {}

    def validate(
        self,
        model_id: str,
        entry: dict[str, Any],
    ) -> list[ValidationIssue]:
        issues = self._validate_common(model_id, entry)
        if any(i.severity == "error" for i in issues):
            return issues

        devices = entry.get("devices", {})

        for device_name in ("gpu", "cpu"):
            if device_name in devices:
                device_config = devices[device_name]
                issues.extend(
                    self._validate_profiles(model_id, device_name, device_config)
                )

        metadata = entry.get("metadata", {})
        capabilities = metadata.get("capabilities", [])
        if "rerank" not in capabilities:
            issues.append(
                ValidationIssue(
                    model_id=model_id,
                    severity="warning",
                    message="Cross-encoder model missing 'rerank' in metadata.capabilities",
                    field="metadata.capabilities",
                    fix="Add 'rerank' to the capabilities list",
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

        # GPU profiles
        profiles: dict[str, dict[str, Any]] = {}
        gpu_device = devices.get("gpu", {})
        gpu_defaults = self.get_device_loader_defaults("gpu")

        for profile_name, prof in gpu_device.get("profiles", {}).items():
            profiles[profile_name] = {
                "loader": {**gpu_defaults, **prof.get("loader", {})},
                "resources": {
                    "vram_mb": prof.get("vram_mb", 0),
                    "ram_mb": prof.get("ram_mb", 0),
                },
            }

        # CPU profiles
        cpu_profiles: dict[str, dict[str, Any]] = {}
        cpu_device = devices.get("cpu", {})
        cpu_defaults = self.get_device_loader_defaults("cpu")

        for profile_name, prof in cpu_device.get("profiles", {}).items():
            cpu_profiles[profile_name] = {
                "loader": {**cpu_defaults, **prof.get("loader", {})},
                "resources": {
                    "ram_mb": prof.get("ram_mb", 0),
                    "vram_mb": 0,
                },
            }

        if not profiles and not cpu_profiles:
            return None

        return ConvertedModel(
            info=self._build_info(model_id, metadata, download),
            base_loader=base_loader,
            profiles=profiles,
            cpu_profiles=cpu_profiles if cpu_profiles else None,
        )
