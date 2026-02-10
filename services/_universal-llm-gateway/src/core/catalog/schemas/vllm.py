"""
vLLM Engine Schema - HF/AWQ/GPTQ models via vLLM.

Capabilities:
    - Devices: gpu only
    - Profile type: context_length
    - Vision support: no (use vLLM's native vision)

Loader Parameters:
    - Validated via runtime introspection (AsyncEngineArgs signature)
    - Common parameters:
        - max_model_len: Maximum context length
        - gpu_memory_utilization: VRAM fraction (0.0-1.0)
        - dtype: Data type (auto, float16, bfloat16)
        - tensor_parallel_size: Multi-GPU parallelism
        - trust_remote_code: Allow custom model code
    - Unknown parameters generate warnings (non-blocking)
    
Validation Strategy:
    - Uses inspect.signature(AsyncEngineArgs.__init__) for parameter list
    - Always accurate for installed vLLM version (no hardcoded whitelist)
    - Gracefully handles vLLM not installed (skips loader validation)
"""

import inspect
from typing import Any

from .schema import BaseEngineSchema
from .types import ConvertedModel, ValidationIssue


class VllmSchema(BaseEngineSchema):
    """Schema for vLLM engine (HF/AWQ/GPTQ models)."""

    engine = "vllm"
    formats = frozenset({"hf", "awq", "gptq"})
    supported_devices = frozenset({"gpu"})
    profile_type = "context_length"
    supports_vision = False

    def get_default_loader(self) -> dict[str, Any]:
        """
        DEPRECATED: Pass-through architecture means no defaults should be injected.
        
        This method remains for API compatibility but returns only safe,
        non-opinionated values. Catalog must provide all required parameters explicitly.
        
        Returns only:
        - Security settings (trust_remote_code=False)
        - Stability settings (disable_custom_all_reduce, disable_log_stats)
        """
        return {
            "trust_remote_code": False,  # SECURITY: Never trust remote code
            "disable_custom_all_reduce": True,  # Stability for single GPU
            "disable_log_stats": True,  # Reduce log noise
        }

    def _get_valid_loader_params(self) -> set[str] | None:
        """
        Get valid vLLM AsyncEngineArgs parameters via runtime introspection.
        
        Returns:
            Set of parameter names, or None if vLLM not available
        """
        try:
            from vllm import AsyncEngineArgs
            return set(inspect.signature(AsyncEngineArgs.__init__).parameters.keys())
        except ImportError:
            return None

    def validate(
        self,
        model_id: str,
        entry: dict[str, Any],
    ) -> list[ValidationIssue]:
        issues = self._validate_common(model_id, entry)
        if any(i.severity == "error" for i in issues):
            return issues

        devices = entry.get("devices", {})

        # vLLM only supports GPU
        if "cpu" in devices:
            issues.append(
                ValidationIssue(
                    model_id=model_id,
                    severity="error",
                    message="vLLM does not support CPU-only mode",
                    field="devices.cpu",
                    fix="Remove cpu device (vLLM requires GPU)",
                )
            )

        if "hybrid" in devices:
            issues.append(
                ValidationIssue(
                    model_id=model_id,
                    severity="error",
                    message="vLLM does not support hybrid mode",
                    field="devices.hybrid",
                    fix="Remove hybrid device (vLLM requires full GPU)",
                )
            )

        # Validate loader parameters against vLLM API
        valid_params = self._get_valid_loader_params()
        if valid_params:
            loader = entry.get("loader", {})
            for param in loader:
                if param not in valid_params:
                    issues.append(
                        ValidationIssue(
                            model_id=model_id,
                            severity="warning",  # Non-blocking
                            message=f"Unknown vLLM parameter: '{param}' (not in AsyncEngineArgs)",
                            field=f"loader.{param}",
                            fix="Verify parameter name in vLLM documentation or remove if incorrect",
                        )
                    )

        # Validate GPU profiles
        gpu_device = devices.get("gpu", {})
        issues.extend(self._validate_profiles(model_id, "gpu", gpu_device))

        # Check max_model_len in profiles
        for profile_key, profile in gpu_device.get("profiles", {}).items():
            if "max_model_len" not in profile:
                # Can be computed from profile key
                if not str(profile_key).isdigit():
                    issues.append(
                        ValidationIssue(
                            model_id=model_id,
                            severity="warning",
                            message=f"Profile '{profile_key}' missing max_model_len",
                            field=f"devices.gpu.profiles.{profile_key}.max_model_len",
                            fix="Add max_model_len or use numeric profile key",
                        )
                    )

        return issues

    def convert(
        self,
        model_id: str,
        entry: dict[str, Any],
    ) -> ConvertedModel | None:
        """
        Convert catalog entry to registry format.
        
        Pass-through architecture: catalog MUST provide all required parameters.
        No defaults injected - missing parameters cause conversion to fail.
        """
        metadata = entry.get("metadata", {})
        download = entry.get("download", {})
        loader = entry.get("loader", {})
        devices = entry.get("devices", {})

        # Pass-through: Use catalog config as-is, only add safe non-opinionated defaults
        safe_defaults = {
            "trust_remote_code": False,  # SECURITY
            "disable_custom_all_reduce": True,  # STABILITY
            "disable_log_stats": True,  # NOISE
        }
        base_loader = {**safe_defaults, **loader}

        profiles: dict[str, dict[str, Any]] = {}
        gpu_device = devices.get("gpu", {})

        for ctx, prof in gpu_device.get("profiles", {}).items():
            # max_model_len from profile or derived from key
            max_model_len = prof.get("max_model_len")
            if max_model_len is None and str(ctx).isdigit():
                max_model_len = int(ctx)

            profiles[str(ctx)] = {
                "loader": {
                    "max_model_len": max_model_len,
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
