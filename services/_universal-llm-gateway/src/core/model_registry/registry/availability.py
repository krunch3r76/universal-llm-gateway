"""Model file validation, availability reporting, and path availability tracking."""

from universal_logging import get_logger

from src.core.gpu_detection import GPUCapabilities
from src.core.model_registry.validation import ModelValidator
from src.schemas.model_info import ModelValidationReport

logger = get_logger(__name__)


class AvailabilityMixin:
    """Validate model files on disk and track which catalog entries are loadable."""

    def validate_model_files(self, fast_mode: bool = True) -> ModelValidationReport:
        """
        Validate all model files and track which models have available paths.

        This is a convenience method that combines check_model_availability()
        and apply_availability_report() for backward compatibility.

        Args:
            fast_mode: If True, skip file size calculations for faster startup

        Returns:
            ModelValidationReport with validation results
        """
        report = self.check_model_availability(fast_mode=fast_mode)
        self.apply_availability_report(report)
        return report

    def check_model_availability(self, fast_mode: bool = True) -> ModelValidationReport:
        """
        Check availability of model files (pure function - no state mutation).

        Args:
            fast_mode: If True, skip file size calculations for faster startup

        Returns:
            ModelValidationReport with validation results
        """
        models_data = self.model_loaders_config.get("models", {})

        # Convert the models data to the format expected by ModelValidator
        models_dict = {}
        for model_id, model_data in models_data.items():
            # Skip aliases (string values) - they don't have model files to validate
            if isinstance(model_data, str):
                continue

            # Create a simple object with the required attributes
            class ModelData:
                def __init__(self, data):
                    import os

                    self.enabled = data.get("enabled", True)
                    # Path is in the 'info' section (resolved by ConfigLoader)
                    info = data.get("info", {})
                    self.path = info.get("path") or data.get("path") or ""
                    self.format = info.get("format", "gguf")

                    # Include loader_config for vision model validation
                    # Prefer base_loader (catalog) over loader_config (legacy)
                    self.loader_config = data.get(
                        "base_loader", data.get("loader_config", {})
                    )

                    # Resolve clip_model_path if present and relative
                    if "clip_model_path" in self.loader_config:
                        clip_path = self.loader_config["clip_model_path"]
                        if clip_path and not clip_path.startswith("/"):
                            # Resolve relative to MODEL_PATH_ROOT
                            model_root = os.getenv("MODEL_PATH_ROOT")
                            if model_root:
                                from pathlib import Path

                                resolved_clip = str(Path(model_root) / clip_path)
                                # Create a copy to avoid mutating original config
                                self.loader_config = self.loader_config.copy()
                                self.loader_config["clip_model_path"] = resolved_clip

            model_obj = ModelData(model_data)
            # Skip models with no path
            if not model_obj.path:
                logger.debug(
                    f"Skipping model validation for {model_id} (no local path)"
                )
                continue

            models_dict[model_id] = model_obj

        # Use the static validator method
        return ModelValidator.validate_model_files(models_dict, fast_mode=fast_mode)

    def apply_availability_report(self, report: ModelValidationReport) -> None:
        """
        Apply validation report by storing results and updating internal tracking.

        Filters models based on:
        1. File availability (exists and readable)
        2. Platform compatibility (GPU availability vs model requirements)

        Args:
            report: ModelValidationReport from check_model_availability()
        """
        # Store validation report and track models with available paths
        self._validation_report = report
        self._models_with_available_paths.clear()

        # Detect backend availability
        llama_installed = GPUCapabilities.is_llama_server_available()
        hardware_gpu_available = GPUCapabilities.is_hardware_gpu_available()
        vllm_available = GPUCapabilities.is_vllm_available()

        # Log detection results
        if llama_installed:
            if hardware_gpu_available:
                logger.info(
                    "✅ llama-server + GPU hardware - all GGUF models supported"
                )
            else:
                logger.info(
                    "ℹ️ llama-server CPU-only (no GPU hardware) - "
                    "GGUF models limited to cpu_profiles"
                )
        else:
            logger.info("⚠️ llama-server not found - GGUF models not available")

        if vllm_available:
            logger.info("✅ vLLM available - HF/AWQ/GPTQ models supported")
        else:
            logger.info("ℹ️ vLLM not available - HF/AWQ/GPTQ models filtered")

        # Track filtering reasons
        path_unavailable = 0
        vllm_required = 0
        llama_required = 0
        cpu_profile_missing = 0

        # Track specific model IDs for detailed logging
        models_missing_files: list[str] = []
        models_missing_vllm: list[str] = []
        models_missing_llama: list[str] = []
        models_missing_cpu_profiles: list[str] = []

        for result in report.results:
            # Get model format for format-specific validation
            # Default to "gguf" if format not specified (most common case)
            model_config = self.get_model_config(result.model_id)
            model_format = "gguf"  # Default format
            if model_config:
                info = model_config.get("info", {})
                model_format = info.get("format") or "gguf"

            # HF/AWQ/GPTQ models: require local directory + vLLM
            if model_format in ["hf", "awq", "gptq"]:
                if not vllm_available:
                    logger.debug(
                        "Model requires vLLM: %s (format: %s)",
                        result.model_id,
                        model_format,
                    )
                    vllm_required += 1
                    models_missing_vllm.append(result.model_id)
                    continue
                # Check if directory exists (vLLM models use directories)
                if not (result.exists and result.readable):
                    logger.debug(
                        "Model directory unavailable: %s - %s",
                        result.model_id,
                        result.error,
                    )
                    path_unavailable += 1
                    models_missing_files.append(result.model_id)
                    continue
                # vLLM model with local directory
                self._models_with_available_paths.add(result.model_id)
                logger.debug(f"Model available (vLLM): {result.model_id}")
                continue

            # GGUF/Whisper models: require local file to exist
            if not (result.exists and result.readable):
                logger.debug(
                    f"Model path unavailable: {result.model_id} - {result.error}"
                )
                path_unavailable += 1
                models_missing_files.append(result.model_id)
                continue

            # GGUF models require llama-server
            if model_format == "gguf":
                if not llama_installed:
                    logger.debug(f"Model requires llama-server: {result.model_id}")
                    llama_required += 1
                    models_missing_llama.append(result.model_id)
                    continue

                # If no GPU hardware, check for cpu_profiles
                if not hardware_gpu_available and model_config:
                    has_cpu_profiles = bool(model_config.get("cpu_profiles"))
                    has_gpu_profiles = bool(model_config.get("profiles"))

                    if not has_cpu_profiles and has_gpu_profiles:
                        logger.debug(
                            "Model missing cpu_profiles: %s (GPU-only GGUF)",
                            result.model_id,
                        )
                        cpu_profile_missing += 1
                        models_missing_cpu_profiles.append(result.model_id)
                        continue

            # Model passed all checks
            self._models_with_available_paths.add(result.model_id)
            logger.debug(f"Model available: {result.model_id}")

        # Log summary
        available_count = len(self._models_with_available_paths)
        logger.info(
            "Model availability check complete: %s/%s models available",
            available_count,
            report.total_models,
        )

        if report.total_models > available_count:
            logger.info("Filtered models breakdown:")
            if path_unavailable > 0:
                missing_files_str = ", ".join(models_missing_files)
                logger.info(
                    f"  • {path_unavailable} model(s) - files not found: "
                    f"{missing_files_str}"
                )
            if vllm_required > 0:
                missing_vllm_str = ", ".join(models_missing_vllm)
                logger.info(
                    f"  • {vllm_required} model(s) - require vLLM "
                    f"(HF/AWQ/GPTQ format): {missing_vllm_str}"
                )
            if llama_required > 0:
                missing_llama_str = ", ".join(models_missing_llama)
                logger.info(
                    f"  • {llama_required} model(s) - require llama-server "
                    f"(GGUF): {missing_llama_str}"
                )
            if cpu_profile_missing > 0:
                missing_cpu_str = ", ".join(models_missing_cpu_profiles)
                logger.info(
                    f"  • {cpu_profile_missing} model(s) - missing cpu_profiles "
                    f"(GGUF): {missing_cpu_str}"
                )
            logger.info("Filtered models will be hidden from /v1/models")

    def is_model_path_available(self, model_id: str) -> bool:
        """
        Check if a model's file path is available (exists and is readable).

        Returns True if:
        - Validation has not run yet (optimistic - assume available)
        - Model passed validation (file exists and is readable)

        Returns False if:
        - Model explicitly failed validation

        Args:
            model_id: Model ID (can be synthetic or base model ID)

        Returns:
            True if model path is available, False otherwise
        """
        # If validation hasn't run yet, assume available (optimistic)
        if self._validation_report is None:
            return True

        # Resolve synthetic IDs to base model ID for validation check
        base_model_id = self.find_config_key_for_openai_id(model_id)
        if not base_model_id:
            # Unknown model - let it through (will fail at load time)
            return True

        # Check if base model passed validation
        available = base_model_id in self._models_with_available_paths
        return available
