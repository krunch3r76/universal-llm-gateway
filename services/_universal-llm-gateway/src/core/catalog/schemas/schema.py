"""
Base Engine Schema - Abstract interface for engine-specific catalog handling.

Schema-per-engine pattern:
    ∀ engine E, ∃! schema S where:
        - S.validate(model) → issues[]
        - S.convert(model) → registry_format
        - S.get_default_loader() → loader_params

Invariants:
    - ∀ schema: engine ∈ {native, vllm, exllamav3, faster-whisper, diffusers}
    - ∀ format F: |{S | F ∈ S.formats}| = 1  (unique schema per format)
    - ∀ schema: supported_devices ⊆ {gpu, cpu, hybrid}

Profile Key Semantics (ModelId alignment):
    - context_length profiles: Keys are numeric strings ('8192', '32768')
    - Hybrid profiles: Keys append '-hybrid' suffix ('32768-hybrid')
    - CPU profiles: Stored separately in cpu_profiles dict
    - Mapping: Model requests with '-hybrid' suffix use hybrid profiles
    - Mapping: Model requests with '-cpu' suffix use cpu_profiles
"""

from abc import ABC, abstractmethod
from typing import Any

from .types import ConvertedModel, ValidationIssue


class BaseEngineSchema(ABC):
    """
    Abstract base class for engine-specific schema handlers.

    Each engine implements:
        - validate(): Schema-specific validation rules
        - convert(): Transform to registry format
        - get_default_loader(): Engine default parameters

    Profile Types:
        - context_length: Keys are numeric context sizes ('8192', '32768')
        - named: Keys are descriptive names ('default', 'offload')
    """

    # Engine dispatch type (used in model info, engine factory, config builder)
    engine: str

    # YAML schema: field value for registry lookup; defaults to engine
    # Override when schema name differs from engine type
    # (e.g., schema_name="llama-cpp" but engine="native")
    schema_name: str = ""

    # Supported model formats
    formats: frozenset[str]

    # Supported device types
    supported_devices: frozenset[str]

    # Profile key type: "context_length" or "named"
    profile_type: str

    # Whether this engine supports vision models
    supports_vision: bool = False

    @abstractmethod
    def validate(
        self,
        model_id: str,
        entry: dict[str, Any],
    ) -> list[ValidationIssue]:
        """
        Validate model entry against this engine's schema.

        Args:
            model_id: Model identifier
            entry: Raw catalog entry dict

        Returns:
            List of validation issues (empty if valid)
        """
        ...

    @abstractmethod
    def convert(
        self,
        model_id: str,
        entry: dict[str, Any],
    ) -> ConvertedModel | None:
        """
        Convert catalog entry to registry format.

        Args:
            model_id: Model identifier
            entry: Raw catalog entry dict

        Returns:
            ConvertedModel or None if conversion fails
        """
        ...

    @abstractmethod
    def get_default_loader(self) -> dict[str, Any]:
        """
        Get default base_loader parameters for this engine.

        Returns:
            Dict of default loader parameters
        """
        ...

    def get_device_loader_defaults(self, device: str) -> dict[str, Any]:
        """
        Get device-specific loader defaults.

        Override in subclass if engine has device-specific defaults.

        Args:
            device: Device type (gpu, cpu, hybrid)

        Returns:
            Dict of device-specific loader parameters
        """
        return {}

    def supports_device(self, device: str) -> bool:
        """
        Check if this engine supports a device type.

        Args:
            device: Device type to check

        Returns:
            True if device is supported
        """
        return device in self.supported_devices

    def _validate_common(
        self,
        model_id: str,
        entry: dict[str, Any],
    ) -> list[ValidationIssue]:
        """
        Common validation logic shared by all schemas.

        Checks:
            - metadata.format matches schema.formats
            - devices dict exists (warning only for V3 static entries)
            - At least one device configuration

        V3 static entries (catalog_schema >= 3, no devices) are valid metadata-only
        entries and do not require a devices section.
        """
        issues: list[ValidationIssue] = []
        metadata = entry.get("metadata", {})
        devices = entry.get("devices", {})

        # Validate format
        model_format = metadata.get("format")
        if not model_format:
            issues.append(
                ValidationIssue(
                    model_id=model_id,
                    severity="error",
                    message="Missing metadata.format",
                    field="metadata.format",
                    fix=f"Add format field: one of {', '.join(self.formats)}",
                )
            )
        elif model_format not in self.formats:
            issues.append(
                ValidationIssue(
                    model_id=model_id,
                    severity="error",
                    message=f"Format '{model_format}' not supported by {self.engine}",
                    field="metadata.format",
                    fix=f"Expected one of: {', '.join(self.formats)}",
                )
            )

        # V3 static entries have no devices section — this is valid
        catalog_schema = entry.get("catalog_schema", 0)
        is_v3_static = catalog_schema >= 3 and not devices

        if not devices:
            if not is_v3_static:
                issues.append(
                    ValidationIssue(
                        model_id=model_id,
                        severity="error",
                        message="No devices configured",
                        field="devices",
                        fix="Add at least one device configuration (gpu/cpu)",
                    )
                )
            return issues

        for device_name in devices:
            if device_name not in self.supported_devices:
                issues.append(
                    ValidationIssue(
                        model_id=model_id,
                        severity="error",
                        message=(
                            f"Device '{device_name}' not supported by {self.engine}"
                        ),
                        field=f"devices.{device_name}",
                        fix=(f"Supported devices: {', '.join(self.supported_devices)}"),
                    )
                )

        return issues

    def _validate_profiles(
        self,
        model_id: str,
        device_name: str,
        device_config: dict[str, Any],
    ) -> list[ValidationIssue]:
        """
        Validate profiles for a device configuration.

        Checks:
            - profiles dict exists and non-empty
            - Profile keys match profile_type (numeric vs named)
            - Required resource fields present
        """
        issues: list[ValidationIssue] = []
        profiles = device_config.get("profiles", {})

        if not profiles:
            issues.append(
                ValidationIssue(
                    model_id=model_id,
                    severity="warning",
                    message=f"No profiles in {device_name} configuration",
                    field=f"devices.{device_name}.profiles",
                    fix="Add at least one profile",
                )
            )
            return issues

        for profile_key, profile in profiles.items():
            # Validate profile key format
            if self.profile_type == "context_length":
                if not str(profile_key).isdigit():
                    issues.append(
                        ValidationIssue(
                            model_id=model_id,
                            severity="error",
                            message=(
                                f"Profile key '{profile_key}' must be numeric "
                                f"(context length)"
                            ),
                            field=f"devices.{device_name}.profiles.{profile_key}",
                            fix=("Use numeric context length (e.g., '8192', '32768')"),
                        )
                    )

            # Validate resources
            vram = profile.get("vram_mb")
            ram = profile.get("ram_mb")

            if device_name in ("gpu", "hybrid") and vram is None:
                issues.append(
                    ValidationIssue(
                        model_id=model_id,
                        severity="error",
                        message=f"Profile '{profile_key}' missing vram_mb",
                        field=f"devices.{device_name}.profiles.{profile_key}.vram_mb",
                        fix="Add vram_mb resource specification",
                    )
                )

            if device_name in ("cpu", "hybrid") and ram is None:
                issues.append(
                    ValidationIssue(
                        model_id=model_id,
                        severity="warning",
                        message=f"Profile '{profile_key}' missing ram_mb",
                        field=f"devices.{device_name}.profiles.{profile_key}.ram_mb",
                        fix="Add ram_mb resource specification",
                    )
                )

        return issues

    def _build_info(
        self,
        model_id: str,
        metadata: dict[str, Any],
        download: dict[str, Any],
    ) -> dict[str, Any]:
        """Build info section for converted model."""
        hf_info = download.get("huggingface", {})
        model_format = metadata.get("format", "")

        # Determine path
        if model_format == "gguf":
            path = hf_info.get("file") or f"{model_id}.gguf"
        elif model_format == "whisper":
            path = hf_info.get("file") or f"{model_id}.pt"
        else:
            repo = hf_info.get("repo") or model_id
            path = repo.split("/")[-1] if "/" in repo else repo

        quant_value = metadata.get("quant")
        quant_str = str(quant_value) if quant_value is not None else None

        return {
            "name": metadata.get("name", model_id),
            "format": model_format,
            "engine": self.engine,
            "path": path,
            "enabled": True,
            "family": metadata.get("family"),
            "arch": metadata.get("arch"),
            "quant": quant_str,
            "license": metadata.get("license"),
            "parameters": (metadata.get("parameters_m") or 0) * 1_000_000,
            "training_context_length": metadata.get("training_context_length"),
            "supports_chat_history": metadata.get("supports_chat_history", True),
            "input_schema": metadata.get("input_schema", "messages"),
            "description": metadata.get("description"),
            "activated_gpu_contexts": metadata.get("activated_gpu_contexts", []),
            "activated_cpu_contexts": metadata.get("activated_cpu_contexts", []),
            "openai_api_fields": {
                "id": model_id,
                "object": "model",
                "owned_by": "universal-llm-gateway",
                "permission": ["generate"],
            },
        }
