"""
LlamaCpp Engine Schema - GGUF models via NativeGGUFEngine (llama-server).

Capabilities:
    - Devices: gpu, cpu, hybrid
    - Profile type: context_length
    - Vision support: yes (with mmproj)

Loader Parameters:
    - n_ctx: Context window size
    - n_gpu_layers: GPU layers (-1 = all, 0 = none)
    - n_batch: Batch size for prompt processing
    - f16_kv: Use FP16 for KV cache
    - use_mmap: Memory-map model file
    - use_mlock: Lock model in RAM

Vision Extensions:
    - clip_model_path: Path to mmproj.gguf
    - vision_architecture: llava, minicpmv, etc.
"""

from typing import Any

from .schema import BaseEngineSchema
from .types import ConvertedModel, ValidationIssue


class LlamaCppSchema(BaseEngineSchema):
    """Schema for GGUF models via NativeGGUFEngine (llama-server).

    engine="native" is the default (llama-server with parallel batching).
    schema_name="llama-cpp" is the YAML schema: field value (legacy name).
    The old llama-cpp-python sequential backend is deprecated and removed.
    """

    engine = "native"
    schema_name = "llama-cpp"
    formats = frozenset({"gguf"})
    supported_devices = frozenset({"gpu", "cpu", "hybrid"})
    profile_type = "context_length"
    supports_vision = True

    def get_default_loader(self) -> dict[str, Any]:
        return {
            "f16_kv": True,
            "use_mmap": False,
            "use_mlock": True,
            "verbose": False,
            "n_batch": 512,
        }

    def get_device_loader_defaults(self, device: str) -> dict[str, Any]:
        if device == "cpu":
            return {
                "f16_kv": False,
                "use_mmap": True,
                "use_mlock": False,
            }
        return {}

    def validate(
        self,
        model_id: str,
        entry: dict[str, Any],
    ) -> list[ValidationIssue]:
        issues = self._validate_common(model_id, entry)
        if any(i.severity == "error" for i in issues):
            return issues

        metadata = entry.get("metadata", {})
        loader = entry.get("loader", {})
        devices = entry.get("devices", {})

        # Validate each device
        for device_name, device_config in devices.items():
            if device_name not in self.supported_devices:
                continue
            issues.extend(self._validate_profiles(model_id, device_name, device_config))

            # Hybrid-specific validation
            if device_name == "hybrid":
                profiles = device_config.get("profiles", {})
                for profile_key, profile in profiles.items():
                    n_gpu_layers = profile.get("n_gpu_layers")
                    if n_gpu_layers is None:
                        issues.append(
                            ValidationIssue(
                                model_id=model_id,
                                severity="error",
                                message=(
                                    f"Hybrid profile '{profile_key}' "
                                    f"missing n_gpu_layers"
                                ),
                                field=(
                                    f"devices.hybrid.profiles.{profile_key}"
                                    f".n_gpu_layers"
                                ),
                                fix="Add n_gpu_layers (> 0 and < total layers)",
                            )
                        )
                    elif n_gpu_layers == -1 or n_gpu_layers == 0:
                        issues.append(
                            ValidationIssue(
                                model_id=model_id,
                                severity="error",
                                message=(
                                    f"Hybrid n_gpu_layers={n_gpu_layers} invalid "
                                    f"(use gpu/cpu device)"
                                ),
                                field=(
                                    f"devices.hybrid.profiles.{profile_key}"
                                    f".n_gpu_layers"
                                ),
                                fix="Use 0 < n_gpu_layers < total_layers for hybrid",
                            )
                        )

        # Vision model validation
        if metadata.get("is_vision_model"):
            if not loader.get("clip_model_path"):
                issues.append(
                    ValidationIssue(
                        model_id=model_id,
                        severity="error",
                        message="Vision model missing clip_model_path",
                        field="loader.clip_model_path",
                        fix="Add path to mmproj.gguf file",
                    )
                )
            if not metadata.get("vision_architecture"):
                issues.append(
                    ValidationIssue(
                        model_id=model_id,
                        severity="warning",
                        message="Vision model missing vision_architecture",
                        field="metadata.vision_architecture",
                        fix="Add vision_architecture (llava, minicpmv, etc.)",
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

        # Merge default + model-specific loader
        base_loader = {**self.get_default_loader(), **loader}

        # Build GPU profiles
        profiles: dict[str, dict[str, Any]] = {}
        gpu_device = devices.get("gpu", {})
        for ctx, prof in gpu_device.get("profiles", {}).items():
            # Merge profile-specific loader config (e.g., parallel_slots, n_batch)
            profile_loader = prof.get("loader", {})
            profiles[str(ctx)] = {
                "loader": {
                    "n_ctx": int(ctx),
                    "n_gpu_layers": prof.get("n_gpu_layers", -1),
                    **profile_loader,  # Include profile-specific overrides
                },
                "resources": {
                    "vram_mb": prof.get("vram_mb", 0),
                    "ram_mb": prof.get("ram_mb", 0),
                },
            }

        # Build hybrid profiles (merge into GPU profiles with distinct keys)
        hybrid_device = devices.get("hybrid", {})
        for ctx, prof in hybrid_device.get("profiles", {}).items():
            key = f"{ctx}-hybrid"
            # Merge profile-specific loader config
            profile_loader = prof.get("loader", {})
            profiles[key] = {
                "loader": {
                    "n_ctx": int(ctx),
                    "n_gpu_layers": prof.get("n_gpu_layers", 0),
                    **profile_loader,  # Include profile-specific overrides
                },
                "resources": {
                    "vram_mb": prof.get("vram_mb", 0),
                    "ram_mb": prof.get("ram_mb", 0),
                },
            }

        # Build CPU profiles
        cpu_profiles: dict[str, dict[str, Any]] = {}
        cpu_device = devices.get("cpu", {})
        cpu_loader_defaults = self.get_device_loader_defaults("cpu")
        for ctx, prof in cpu_device.get("profiles", {}).items():
            # Merge profile-specific loader config
            profile_loader = prof.get("loader", {})
            cpu_profiles[str(ctx)] = {
                "loader": {
                    "n_ctx": int(ctx),
                    "n_gpu_layers": 0,
                    **cpu_loader_defaults,
                    **profile_loader,  # Profile overrides take precedence
                },
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
