"""Model validation logic"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from universal_logging import get_logger

from ...schemas.model_info import ModelValidationReport, ModelValidationResult

logger = get_logger(__name__)


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


class ModelValidator:
    """Model validation utilities"""

    @staticmethod
    def validate_model_files(
        models: dict[str, Any], fast_mode: bool = True
    ) -> ModelValidationReport:
        """Validate that model files exist and are accessible

        Args:
            models: Dictionary of model configurations
            fast_mode: If True, skip file size calculations for faster startup
        """
        results = []
        valid_count = 0

        for model_id, metadata in models.items():
            if not metadata.enabled:
                # Skip disabled models
                continue

            result = ModelValidator._validate_single_model(
                model_id, metadata, fast_mode
            )
            results.append(result)

            if result.exists and result.readable:
                valid_count += 1

        return ModelValidationReport(
            total_models=len([m for m in models.values() if m.enabled]),
            valid_models=valid_count,
            results=results,
        )

    @staticmethod
    def _validate_single_model(
        model_id: str, metadata: Any, fast_mode: bool = True
    ) -> ModelValidationResult:
        """Validate a single model file or directory - optimized for fast startup

        Supports:
        - Single file models (GGUF): /path/to/model.gguf
        - Directory models (HF/AWQ/GPTQ/Flux.2): /path/to/model-dir/
        - Vision models: Also validates CLIP model file exists
        """
        path = Path(metadata.path)

        try:
            exists = path.exists()
            readable = False
            size_mb = None
            error = None

            if exists:
                try:
                    # Check if path is a directory (HF/AWQ/GPTQ/Flux models)
                    if path.is_dir():
                        # For directory-based models, validate structure
                        # HF models: config.json
                        # Flux models: model_index.json
                        config_file = path / "config.json"
                        model_index_file = path / "model_index.json"

                        if not config_file.exists() and not model_index_file.exists():
                            error = (
                                "Directory missing config.json or model_index.json "
                                "(invalid model directory)"
                            )
                            readable = False
                        else:
                            # Directory is valid model structure
                            readable = True
                            # For directories, calculate total size if not in fast mode
                            if not fast_mode:
                                total_size = sum(
                                    f.stat().st_size
                                    for f in path.rglob("*")
                                    if f.is_file()
                                )
                                size_mb = total_size / (1024 * 1024)
                    else:
                        # For single file models (GGUF), check if readable
                        with open(path, "rb") as f:
                            f.read(1)  # Try to read first byte
                        readable = True

                        # Only calculate file size if not in fast mode
                        if not fast_mode:
                            size_mb = path.stat().st_size / (1024 * 1024)

                    # For vision models, validate CLIP model file exists
                    if readable and hasattr(metadata, "loader_config"):
                        loader_config = metadata.loader_config
                        clip_path = loader_config.get("clip_model_path")
                        if clip_path:
                            clip_file = Path(clip_path)
                            if not clip_file.exists():
                                error = f"Vision model CLIP file not found: {clip_path}"
                                readable = False
                            elif not clip_file.is_file():
                                error = (
                                    f"Vision model CLIP path is not a file: {clip_path}"
                                )
                                readable = False

                except PermissionError:
                    error = "Permission denied"
                except Exception as e:
                    error = f"Cannot read file: {e}"
            else:
                error = "File does not exist"

            return ModelValidationResult(
                model_id=model_id,
                exists=exists,
                path=str(path),
                size_mb=size_mb,
                readable=readable,
                error=error,
            )

        except Exception as e:
            return ModelValidationResult(
                model_id=model_id,
                exists=False,
                path=str(path),
                size_mb=None,
                readable=False,
                error=f"Validation error: {e}",
            )

    @staticmethod
    def validate_profile_resources(models_config: dict[str, Any]) -> list[ProfileIssue]:
        """
        Validate that all profiles have complete resource sections.

        Checks for:
        - Missing resources section entirely
        - Missing ram_mb or vram_mb fields
        - Null/None values for required fields

        Args:
            models_config: The 'models' section from model_loaders.yaml

        Returns:
            List of ProfileIssue objects describing incomplete profiles
        """
        issues = []

        for model_id, model_data in models_config.items():
            # Skip aliases (string values)
            if not isinstance(model_data, dict):
                continue

            # Check both GPU profiles and CPU profiles
            for profile_type in ["profiles", "cpu_profiles"]:
                profiles = model_data.get(profile_type, {})

                for profile_key, profile_config in profiles.items():
                    if not isinstance(profile_config, dict):
                        continue

                    resources = profile_config.get("resources")

                    # Detect hybrid: n_gpu_layers > 0 means partial GPU offload
                    loader_config = profile_config.get("loader", {})
                    # Handle n_gpu_layers at profile level (YAML format) or under loader (normalized format)
                    n_gpu_layers = profile_config.get("n_gpu_layers")
                    if n_gpu_layers is None:
                        n_gpu_layers = loader_config.get("n_gpu_layers", -1)
                    is_hybrid = n_gpu_layers > 0 and profile_type == "profiles"

                    # Check if resources section exists
                    if resources is None:
                        issues.append(
                            ProfileIssue(
                                model_id=model_id,
                                profile_key=profile_key,
                                profile_type=profile_type,
                                issue="Missing 'resources' section",
                                impact=(
                                    "Model eviction will fail - router cannot "
                                    "calculate freeable resources"
                                ),
                                is_hybrid=is_hybrid,
                            )
                        )
                        continue

                    # Check for CPU profiles - should have vram_mb=0
                    is_cpu_profile = profile_type == "cpu_profiles"

                    # Check required fields
                    ram_mb = resources.get("ram_mb")
                    vram_mb = resources.get("vram_mb")

                    if ram_mb is None:
                        issues.append(
                            ProfileIssue(
                                model_id=model_id,
                                profile_key=profile_key,
                                profile_type=profile_type,
                                issue="Missing 'ram_mb' in resources section",
                                impact=(
                                    "Resource tracking incomplete - may cause "
                                    "incorrect capacity calculations"
                                ),
                                is_hybrid=is_hybrid,
                            )
                        )

                    if vram_mb is None:
                        issues.append(
                            ProfileIssue(
                                model_id=model_id,
                                profile_key=profile_key,
                                profile_type=profile_type,
                                issue="Missing 'vram_mb' in resources section",
                                impact=(
                                    "Resource tracking incomplete - may cause "
                                    "incorrect capacity calculations"
                                ),
                                is_hybrid=is_hybrid,
                            )
                        )

                    # Validate CPU profile expectations
                    if is_cpu_profile and vram_mb is not None and vram_mb != 0:
                        issues.append(
                            ProfileIssue(
                                model_id=model_id,
                                profile_key=profile_key,
                                profile_type=profile_type,
                                issue=(
                                    f"CPU profile has vram_mb={vram_mb} (should be 0)"
                                ),
                                impact=(
                                    "CPU model may be incorrectly considered for "
                                    "GPU resource calculations"
                                ),
                                is_hybrid=is_hybrid,
                            )
                        )

        return issues

    @staticmethod
    def log_profile_validation_results(issues: list[ProfileIssue]) -> None:
        """Log profile validation issues in a readable format"""
        if not issues:
            logger.info(
                "✅ Profile validation: All profiles have complete resource sections"
            )
            return

        logger.warning(f"⚠️  Found {len(issues)} incomplete profile configuration(s):")

        for issue in issues:
            synthetic_id = f"{issue.model_id}-{issue.profile_key}"
            if issue.profile_type == "cpu_profiles":
                synthetic_id += "-cpu"
            elif issue.is_hybrid:
                synthetic_id += "-hybrid"

            logger.warning(
                f"  • {synthetic_id}: {issue.issue}\n"
                f"    Impact: {issue.impact}\n"
                "    Location: models.%s.%s.%s",
                issue.model_id,
                issue.profile_type,
                issue.profile_key,
            )

    @staticmethod
    def validate_catalog_metadata(catalog: dict[str, Any]) -> list[MetadataIssue]:
        """
        Validate V2 catalog metadata for correctness.

        Checks for:
        - Correct quant values (should match model quantization)
        - Missing activated contexts for device types
        - Vision model requirements
        - Device configuration completeness

        Args:
            catalog: Full catalog dictionary with 'models' key (V2 format)

        Returns:
            List of MetadataIssue objects
        """
        issues: list[MetadataIssue] = []
        models = catalog.get("models", {})

        for model_id, model_entry in models.items():
            if not isinstance(model_entry, dict):
                continue

            metadata = model_entry.get("metadata", {})
            devices = model_entry.get("devices", {})

            # Validate quant value
            quant = metadata.get("quant")
            if quant is not None:
                # Check if quant matches quantization in model name
                model_name = metadata.get("name", model_id)
                issues.extend(
                    ModelValidator._validate_quant_consistency(
                        model_id, model_name, quant
                    )
                )

            # V2: Check devices structure (not configurations)
            if not devices:
                issues.append(
                    MetadataIssue(
                        model_id=model_id,
                        field="devices",
                        issue="No devices configured",
                        severity="error",
                    )
                )
                continue

            # Validate activated contexts match device availability
            for device_type in ["gpu", "cpu", "hybrid"]:
                device_config = devices.get(device_type, {})
                profiles = device_config.get("profiles", {})
                activated_key = f"activated_{device_type}_contexts"
                activated = metadata.get(activated_key, [])

                if profiles and not activated:
                    issues.append(
                        MetadataIssue(
                            model_id=model_id,
                            field=activated_key,
                            issue=f"{device_type} profiles exist but {activated_key} is missing",
                            severity="warning",
                        )
                    )

        return issues

    @staticmethod
    def _validate_quant_consistency(
        model_id: str, model_name: str, quant: Any
    ) -> list[MetadataIssue]:
        """Validate quant value matches model quantization."""
        issues: list[MetadataIssue] = []

        # Extract quantization from model name
        name_upper = model_name.upper()

        # Common quantization patterns
        quant_patterns = {
            "Q2_K": 2,
            "Q3_K": 3,
            "Q4_K": 4,
            "Q4_0": 4,
            "Q4_1": 4,
            "Q5_K": 5,
            "Q5_0": 5,
            "Q5_1": 5,
            "Q6_K": 6,
            "Q8_0": 8,
            "Q8_K": 8,
            "IQ1": 1,
            "IQ2": 2,
            "IQ3": 3,
            "IQ4": 4,
        }

        detected_quant = None
        for pattern, bits in quant_patterns.items():
            if pattern in name_upper:
                detected_quant = bits
                break

        # If we detected a quantization but it doesn't match metadata
        if detected_quant is not None:
            try:
                quant_int = int(quant)
                if quant_int != detected_quant:
                    issues.append(
                        MetadataIssue(
                            model_id=model_id,
                            field="quant",
                            issue=f"Quant value {quant} doesn't match detected {detected_quant}-bit quantization in model name",
                            severity="error",
                        )
                    )
            except (ValueError, TypeError):
                issues.append(
                    MetadataIssue(
                        model_id=model_id,
                        field="quant",
                        issue=f"Invalid quant value: {quant} (should be integer)",
                        severity="error",
                    )
                )

        return issues

    @staticmethod
    def log_metadata_validation_results(issues: list[MetadataIssue]) -> None:
        """Log metadata validation issues in a readable format."""
        if not issues:
            logger.info("✅ Metadata validation: All models have correct metadata")
            return

        errors = [i for i in issues if i.severity == "error"]
        warnings = [i for i in issues if i.severity == "warning"]

        if errors:
            logger.error(f"❌ Found {len(errors)} metadata error(s):")
            for issue in errors:
                logger.error(f"  • {issue.model_id}.{issue.field}: {issue.issue}")

        if warnings:
            logger.warning(f"⚠️ Found {len(warnings)} metadata warning(s):")
            for issue in warnings:
                logger.warning(f"  • {issue.model_id}.{issue.field}: {issue.issue}")
