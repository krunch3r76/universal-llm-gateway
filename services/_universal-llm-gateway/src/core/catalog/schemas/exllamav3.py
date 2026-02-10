"""
ExLlamaV3 Engine Schema - EXL3 models via ExLlamaV3.

Capabilities:
    - Devices: gpu only
    - Profile type: context_length
    - Vision support: no

Loader Parameters:
    - max_seq_len: Maximum sequence length
    - max_batch_size: Maximum batch size
    - max_input_len: Maximum input length
    - max_output_len: Maximum output length
"""

from typing import Any

from .schema import BaseEngineSchema
from .types import ConvertedModel, ValidationIssue


class ExllamaV3Schema(BaseEngineSchema):
    """Schema for ExLlamaV3 engine (EXL3 models)."""

    engine = "exllamav3"
    formats = frozenset({"exl3"})
    supported_devices = frozenset({"gpu"})
    profile_type = "context_length"
    supports_vision = False

    def get_default_loader(self) -> dict[str, Any]:
        return {
            "max_batch_size": 1,
            "max_input_len": 8192,
            "max_output_len": 4096,
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

        # ExLlamaV3 only supports GPU
        if "cpu" in devices:
            issues.append(
                ValidationIssue(
                    model_id=model_id,
                    severity="error",
                    message="ExLlamaV3 does not support CPU-only mode",
                    field="devices.cpu",
                    fix="Remove cpu device (ExLlamaV3 requires GPU)",
                )
            )

        # Validate GPU profiles
        gpu_device = devices.get("gpu", {})
        issues.extend(self._validate_profiles(model_id, "gpu", gpu_device))

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

        for ctx, prof in gpu_device.get("profiles", {}).items():
            max_seq_len = prof.get("max_seq_len")
            if max_seq_len is None and str(ctx).isdigit():
                max_seq_len = int(ctx)

            profiles[str(ctx)] = {
                "loader": {
                    "max_seq_len": max_seq_len,
                },
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
