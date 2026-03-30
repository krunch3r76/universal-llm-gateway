"""Main model registry class"""

from typing import Any

from model_id import ModelId
from universal_logging import get_logger

from src.core.gpu_detection import GPUCapabilities
from src.core.model_registry.metadata import ModelMetadata
from src.core.model_registry.validation import ModelValidator
from src.core.synthetic_models import SyntheticModelResolver
from src.schemas.model_info import ModelInfo, ModelValidationReport

logger = get_logger(__name__)


def normalize_model_id(model_id: str) -> str:
    """
    Normalize model ID by stripping informational suffixes.

    The -hybrid suffix is informational only and stripped for operations.
    The -cpu suffix is preserved as it affects resource allocation.

    No instance suffix (`:N`) support - Gateway uses canonical model IDs only.
    Stargate rejects `:N` at the API boundary.

    Args:
        model_id: Model ID to normalize (must be canonical, no `:N`)

    Returns:
        Normalized model ID (e.g., 'model-8192-hybrid' -> 'model-8192')
    """
    return ModelId.parse(model_id).normalized


def _validate_model_id_suffixes(model_id: str) -> str | None:
    """
    Validate model ID does not have conflicting or malformed suffixes.

    Instance suffix (`:N`) is no longer supported - Gateway uses canonical
    model IDs only. Stargate rejects `:N` at the API boundary.

    INTENTIONAL DUPLICATION: This function is duplicated from Stargate's
    `proxy/utils/model_id_utils.py` to avoid cross-service Python dependencies.
    These services are deployed independently and cannot share code imports.

    MAINTENANCE: When updating validation rules, update both:
    - services/universal-stargate/proxy/utils/model_id_utils.py
    - services/_universal-llm-gateway/src/core/model_registry/registry.py
    """
    # Reject instance suffix (`:N`) - Gateway uses canonical model IDs only
    if ":" in model_id:
        return "Model ID must not include an instance suffix like ':1' or ':2'"

    cpu_count = model_id.count("-cpu")
    hybrid_count = model_id.count("-hybrid")

    if cpu_count > 1:
        return "Model ID contains duplicated -cpu suffix"
    if hybrid_count > 1:
        return "Model ID contains duplicated -hybrid suffix"

    if cpu_count > 0 and hybrid_count > 0:
        return "Model ID cannot have both -cpu and -hybrid suffixes"

    if cpu_count == 1 and not model_id.endswith("-cpu"):
        return "Invalid -cpu suffix: must be at end of model ID"
    if hybrid_count == 1 and not model_id.endswith("-hybrid"):
        return "Invalid -hybrid suffix: must be at end of model ID"

    return None


class ModelRegistry:
    """Model registry for managing model metadata and validation"""

    def __init__(self, model_loaders_config: dict[str, Any]):
        """Initialize the model registry"""
        self.model_loaders_config = model_loaders_config
        self.models_to_metadata = {}  # Keep for compatibility but won't be used
        self.loaded_models = {}
        self._validation_report: ModelValidationReport | None = None
        self._models_with_available_paths: set[str] = set()

        # No longer extracting metadata - all data comes from config

    def __repr__(self) -> str:
        """Return a string representation of the ModelRegistry"""
        model_count = self.get_model_count()
        loaded_count = len(self.loaded_models)
        return (
            f"ModelRegistry(total_models={model_count['total']}, "
            f"enabled_models={model_count['enabled']}, "
            f"loaded_models={loaded_count})"
        )

    def find_config_key_for_openai_id(
        self, model_id: str, _seen: set[str] | None = None
    ) -> str | None:
        """
        Resolve synthetic or base model ID to YAML config key.

        Accepts both synthetic IDs (e.g., 'model-name-32768-cpu') and base model IDs.
        Returns the base model config key from YAML.
        """
        _seen = _seen or set()
        if model_id in _seen:
            raise ValueError(f"Cyclic model alias detected: {model_id}")
        _seen.add(model_id)

        # Normalize to strip -hybrid suffix (informational only)
        model_id = normalize_model_id(model_id)
        models_data = self.model_loaders_config.get("models", {})

        # Direct lookup by YAML key first to avoid false matches
        # with the synthetic ID pattern
        if model_id in models_data:
            model_entry = models_data[model_id]
            # Check if it's an alias (string value)
            if isinstance(model_entry, str):
                # Recursively resolve the alias
                return self.find_config_key_for_openai_id(model_entry, _seen)
            return model_id

        # Try to resolve as synthetic ID (only if direct lookup failed)
        resolution = SyntheticModelResolver.resolve_synthetic_id(model_id)
        if resolution:
            base_model_id, _, _, _ = resolution
            # Recursively resolve the base model ID (may be an alias)
            return self.find_config_key_for_openai_id(base_model_id, _seen)

        # Search by OpenAI API ID in standardized structure (for base model IDs)
        for config_key, model_config in models_data.items():
            if isinstance(model_config, dict):
                model_info = model_config.get("info", {})
                if model_info:
                    openai_fields = model_info.get("openai_api_fields", {})
                    if openai_fields.get("id") == model_id:
                        return config_key

        return None

    def get_model_info(self, model_id: str) -> ModelInfo | None:
        """
        Get model information as ModelInfo schema from configuration.

        Normalizes model_id for catalog lookup (strips -hybrid suffix).
        Accepts synthetic model IDs and returns ModelInfo with the synthetic ID.
        """
        # Normalize for catalog lookup (strips -hybrid suffix)
        canonical_id = normalize_model_id(model_id)

        # Check if this is a synthetic ID - if so, use it directly
        synthetic_info = self._resolve_synthetic_id_info(canonical_id)
        if synthetic_info:
            # It's a synthetic ID, use it as-is
            synthetic_id = canonical_id
        else:
            # It's a base model ID, try to resolve it
            synthetic_id = None

        model_config_key = self.find_config_key_for_openai_id(canonical_id)
        if not model_config_key:
            return None

        # Get model configuration from catalog (in legacy format)
        models_data = self.model_loaders_config.get("models", {})
        model_config = models_data.get(model_config_key)
        if not model_config or not isinstance(model_config, dict):
            return None

        # All models now use standardized 'info' section structure
        model_info = model_config.get("info", {})
        if not model_info:
            raise ValueError(
                f"Model '{model_config_key}' missing required 'info' section"
            )

        openai_fields = model_info.get("openai_api_fields", {})
        if not openai_fields:
            raise ValueError(
                f"Model '{model_config_key}' missing required 'openai_api_fields'"
            )

        # Use synthetic ID if available, else openai id or normalized canonical_id
        model_id_to_use = (
            synthetic_id if synthetic_id else openai_fields.get("id", canonical_id)
        )

        capabilities = model_info.get("capabilities", {})
        input_schema = model_info.get("input_schema") or capabilities.get(
            "input_schema", "messages"
        )

        return ModelInfo(
            id=model_id_to_use,  # Use synthetic ID if available
            name=model_info.get("name", model_id_to_use),  # Needed fallback logic
            format=model_info.get("format", "unknown"),  # Needed fallback
            enabled=model_info.get("enabled"),  # Let schema default=True handle None
            training_context_length=model_info.get("training_context_length"),
            estimated_vram_mb=model_info.get("vram_usage"),
            input_schema=input_schema,
            capabilities=capabilities if capabilities else None,
            # All other fields use schema defaults automatically!
        )

    def get_model_metadata(self, model_id: str) -> ModelMetadata | None:
        """Get model metadata - now returns None since we use config data directly"""
        # No longer extracting metadata - all data comes from config
        return None

    def get_model_resources(self, model_id: str) -> dict[str, int] | None:
        """
        Get model resource requirements (RAM and VRAM) from profile configuration.

        For synthetic model IDs (e.g., 'model-name-131072'), extracts resources from
        the specific profile. Returns None if profile or resources not found.

        Args:
            model_id: Model ID (can be synthetic with context length)

        Returns:
            Dict with 'ram_mb' and 'vram_mb' keys, or None if not found
        """
        # Try to resolve as synthetic ID to get context length
        synthetic_info = self._resolve_synthetic_id_info(model_id)
        if synthetic_info:
            base_model_id, context_length, is_cpu, is_hybrid = synthetic_info
            requested_ctx = context_length
        else:
            # Not a synthetic ID, no specific context to look up
            requested_ctx = None
            is_cpu = False

        model_config = self.get_model_config(model_id)
        if not model_config or not isinstance(model_config, dict):
            return None

        # Determine profile type
        profile_type = "cpu_profiles" if is_cpu else "profiles"
        profiles = model_config.get(profile_type, {})

        if not profiles:
            return None

        # If we have a specific context from synthetic ID, look it up
        if requested_ctx is not None:
            ctx_key = str(requested_ctx)
            profile = profiles.get(ctx_key)
            if profile:
                resources = profile.get("resources", {})
                ram_mb = resources.get("ram_mb")
                vram_mb = resources.get("vram_mb")
                if ram_mb is not None and vram_mb is not None:
                    return {"ram_mb": ram_mb, "vram_mb": vram_mb}
        else:
            # No specific context - use profile with highest context that has resources
            profile_keys = [key for key in profiles.keys() if key.isdigit()]
            if profile_keys:
                # Filter for profiles with non-null resources
                valid_keys = []
                for key in profile_keys:
                    profile = profiles[key]
                    resources = profile.get("resources", {})
                    if is_cpu:
                        # For CPU profiles, vram_mb should be 0
                        if (
                            resources.get("vram_mb") == 0
                            and resources.get("ram_mb") is not None
                        ):
                            valid_keys.append(key)
                    else:
                        # For GPU profiles, both should be non-null
                        if (
                            resources.get("ram_mb") is not None
                            and resources.get("vram_mb") is not None
                        ):
                            valid_keys.append(key)

                # Use highest key among valid profiles
                if valid_keys:
                    selected_key = max(valid_keys, key=int)
                    profile = profiles[selected_key]
                    resources = profile.get("resources", {})
                    return {
                        "ram_mb": resources.get("ram_mb"),
                        "vram_mb": resources.get("vram_mb"),
                    }

            # Named-profile models (for example cross-encoder/default) do not
            # expose numeric context keys. Use the first profile that has valid
            # resource fields so INIT/GATEWAY_SNAPSHOT can advertise them.
            for profile in profiles.values():
                resources = profile.get("resources", {})
                ram_mb = resources.get("ram_mb")
                vram_mb = resources.get("vram_mb")
                if ram_mb is None or vram_mb is None:
                    continue
                if is_cpu and vram_mb != 0:
                    continue
                return {"ram_mb": ram_mb, "vram_mb": vram_mb}

        return None

    def is_model_enabled(self, model_id: str) -> bool:
        """Check if a model is enabled"""
        model_config_key = self.find_config_key_for_openai_id(model_id)
        if not model_config_key:
            return False

        models_data = self.model_loaders_config.get("models", {})
        model_data = models_data.get(model_config_key, {})

        # Handle case where model_data might be a string (alias) or dict
        if isinstance(model_data, str):
            # If it's a string, it's an alias - check if the aliased model is enabled
            return self.is_model_enabled(model_data)
        elif isinstance(model_data, dict):
            # All models now use standardized 'info' section structure
            model_info = model_data.get("info", {})
            if not model_info:
                raise ValueError(
                    f"Model '{model_config_key}' missing required 'info' section"
                )
            enabled = model_info.get("enabled", False)
            return enabled
        else:
            return False

    def get_available_synthetic_model_ids(
        self, enabled_only: bool = True, available_only: bool = True
    ) -> list[str]:
        """
        Get list of synthetic model IDs with optional filtering.

        This is the canonical method for getting model IDs - used by both
        HTTP /v1/models endpoint and WebSocket INIT message to ensure
        consistent behavior.

        Filters models based on:
        1. Enabled status (if enabled_only=True)
        2. File availability (if available_only=True)
        3. Hardware capabilities (CPU-only gateways exclude GPU/hybrid models)

        Args:
            enabled_only: Only return enabled models (default: True)
            available_only: Only return models with available file paths (default: True)

        Returns:
            List of synthetic model IDs that pass the filters
        """
        # Get all synthetic models from catalog
        config = self.model_loaders_config

        synthetic_models = SyntheticModelResolver.get_all_synthetic_models(config)

        # Use the same GPU detection as apply_availability_report() so both code
        # paths agree. pynvml (get_vram_info) can return 0 even when a GPU is
        # present (e.g. NVML not accessible inside a container), which would
        # silently exclude all GPU model variants from the INIT message.
        is_cpu_only = not GPUCapabilities.is_hardware_gpu_available()

        # Apply filters
        model_ids = []
        for sm in synthetic_models:
            # Filter by enabled status
            if enabled_only:
                enabled = self.is_model_enabled(sm.synthetic_id)
                if not enabled:
                    continue

            # Filter by file availability
            if available_only:
                path_available = self.is_model_path_available(sm.synthetic_id)
                if not path_available:
                    continue

            # Filter by hardware capabilities
            # CPU-only gateways (vram_total_mb == 0) can only run CPU models
            # GPU and hybrid models require GPU support
            if is_cpu_only:
                try:
                    parsed = ModelId.parse(sm.synthetic_id)
                    # Only include CPU models (-cpu suffix)
                    # Exclude GPU models (no suffix) and hybrid models (-hybrid suffix)
                    if not parsed.is_cpu:
                        continue
                except ValueError:
                    # Not a synthetic ID, include as-is (pipeline IDs, etc.)
                    pass

            model_ids.append(sm.synthetic_id)

        return model_ids

    def list_models(self, enabled_only: bool = False) -> list[ModelInfo]:
        """
        List all synthetic models or only enabled models.

        Returns synthetic model IDs with explicit context lengths.
        """
        # Get all synthetic models
        all_synthetic_models = SyntheticModelResolver.get_default_synthetic_models(
            self.model_loaders_config
        )

        models = []
        for synthetic_model in all_synthetic_models:
            if enabled_only and not self.is_model_enabled(synthetic_model.synthetic_id):
                continue

            # Create ModelInfo from synthetic model
            model_info = self.get_model_info(synthetic_model.synthetic_id)
            if model_info:
                models.append(model_info)

        return models

    def get_model_max_tokens(
        self, model_id: str, requested_ctx: int | None = None
    ) -> int | None:
        """Get maximum tokens for a model from active loader configuration"""
        model_config_key = self.find_config_key_for_openai_id(model_id)
        if not model_config_key:
            return None

        models_data = self.model_loaders_config.get("models", {})
        model_config = models_data.get(model_config_key, {})

        if not isinstance(model_config, dict):
            return None

        # For simple loader config format
        if "loader" in model_config:
            loader_config = model_config.get("loader", {})
            return loader_config.get("max_model_len") or loader_config.get("n_ctx")

        # Check if this is a synthetic CPU ID to determine which profiles to use
        synthetic_info = self._resolve_synthetic_id_info(model_id)
        is_cpu = synthetic_info[2] if synthetic_info else False
        # is_hybrid uses GPU profiles, so we only need is_cpu here

        # Determine which profile type to check
        if is_cpu:
            # For CPU synthetic IDs, check cpu_profiles first
            profiles = model_config.get("cpu_profiles", {})
            if not profiles:
                # Fallback to regular profiles if cpu_profiles not found
                profiles = model_config.get("profiles", {})
        else:
            # For GPU or base model IDs, check profiles first
            profiles = model_config.get("profiles", {})
            if not profiles:
                # Fallback to cpu_profiles if profiles not found
                profiles = model_config.get("cpu_profiles", {})

        # For profile-based format
        if profiles:
            # Use context length from synthetic ID if available,
            # otherwise use requested_ctx
            effective_ctx = synthetic_info[1] if synthetic_info else requested_ctx

            if effective_ctx is None:
                # Use the profile with the highest integer key that
                # has non-null resource values
                profile_keys = [key for key in profiles.keys() if key.isdigit()]
                if profile_keys:
                    # Filter for profiles with non-null ram_mb and vram_mb
                    valid_keys = []
                    for key in profile_keys:
                        profile = profiles[key]
                        resources = profile.get("resources", {})
                        if (
                            resources.get("ram_mb") is not None
                            and resources.get("vram_mb") is not None
                        ):
                            valid_keys.append(key)

                    # Use highest key among valid profiles, or fall back to highest key
                    selected_key = (
                        max(valid_keys, key=int)
                        if valid_keys
                        else max(profile_keys, key=int)
                    )
                    profile = profiles[selected_key]
                    loader_config = profile.get("loader", {})
                    return loader_config.get("max_model_len") or loader_config.get(
                        "n_ctx"
                    )

                # If no numeric keys found, use the first profile
                first_profile = list(profiles.values())[0]
                loader_config = first_profile.get("loader", {})
                return loader_config.get("max_model_len") or loader_config.get("n_ctx")

            # Exact match required — no silent substitution.
            ctx_key = str(effective_ctx)
            if ctx_key in profiles:
                loader_config = profiles[ctx_key].get("loader", {})
                return loader_config.get("max_model_len") or loader_config.get("n_ctx")
            else:
                return None

        # Legacy fallback - try metadata (will be removed)
        info = model_config.get("info", {})
        context_length = info.get("training_context_length")
        if context_length:
            return context_length

        # Also check metadata for backward compatibility
        metadata = model_config.get("metadata", {})
        context_length = metadata.get("training_context_length")
        if context_length:
            return context_length

        return model_config.get("context_length")

    def get_model_limits(
        self, model_id: str, requested_ctx: int | None = None
    ) -> dict[str, int] | None:
        """Get model limits from active loader configuration"""
        context_length = self.get_model_max_tokens(model_id, requested_ctx)

        if not context_length:
            return None

        return {"max_tokens": context_length, "max_input_tokens": context_length}

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

    def get_model_count(self) -> dict[str, int]:
        """Get model count statistics"""
        models_data = self.model_loaders_config.get("models", {})
        total_real_models = 0
        enabled_real_models = 0

        # Only count real model definitions (dicts), skip aliases (strings)
        for _model_id, model_entry in models_data.items():
            if isinstance(model_entry, dict):
                total_real_models += 1
                if model_entry.get("enabled", False):
                    enabled_real_models += 1

        return {
            "total": total_real_models,
            "enabled": enabled_real_models,
            "disabled": total_real_models - enabled_real_models,
        }

    def register_loaded_model(self, model_id: str, model_instance: Any) -> None:
        """Register a loaded model instance"""
        self.loaded_models[model_id] = model_instance
        logger.info(f"Registered loaded model: {model_id}")

    def unregister_loaded_model(self, model_id: str) -> None:
        """Unregister a loaded model instance"""
        if model_id in self.loaded_models:
            del self.loaded_models[model_id]
            logger.info(f"Unregistered loaded model: {model_id}")

    def is_model_loaded(self, model_id: str) -> bool:
        """Check if a model is currently loaded"""
        return model_id in self.loaded_models

    def get_loaded_model(self, model_id: str) -> Any | None:
        """Get a loaded model instance"""
        return self.loaded_models.get(model_id)

    def get_model_config(self, model_id: str) -> dict[str, Any] | None:
        """
        Get raw model configuration from catalog.

        Normalizes model_id for catalog lookup (strips -hybrid suffix).
        The catalog doesn't store hybrid variants separately.

        Accepts both synthetic IDs and base model IDs.
        Returns the base model configuration in legacy format.
        """
        # Normalize for catalog lookup - strips -hybrid (informational only)
        canonical_id = normalize_model_id(model_id)

        model_config_key = self.find_config_key_for_openai_id(canonical_id)
        if not model_config_key:
            return None

        models_data = self.model_loaders_config.get("models", {})
        model_config = models_data.get(model_config_key)

        if isinstance(model_config, dict):
            return model_config

        return None

    def _resolve_synthetic_id_info(
        self, model_id: str
    ) -> tuple[str, int, bool, bool] | None:
        """
        Resolve synthetic ID to (base_model_id, context_length, is_cpu, is_hybrid).

        Returns None if not a synthetic ID.
        """
        return SyntheticModelResolver.resolve_synthetic_id(model_id)

    def get_model_path(self, model_id: str) -> str | None:
        """Get model path from configuration.

        Normalizes model_id for catalog lookup (strips -hybrid suffix).
        """
        # Normalization happens in get_model_config
        model_config = self.get_model_config(model_id)
        if model_config:
            # All models now use standardized 'info' section structure
            model_info = model_config.get("info", {})
            if not model_info:
                raise ValueError(f"Model '{model_id}' missing required 'info' section")
            return model_info.get("path")
        return None

    @staticmethod
    def _select_profile_loader(
        profiles: dict[str, Any],
        requested_ctx: int | None,
        is_cpu: bool = False,
    ) -> dict[str, Any] | None:
        """
        Select appropriate profile loader configuration.

        For GGUF profiles that lack an explicit context-length key, injects
        ``n_ctx`` from the numeric profile key (e.g. ``'65536'`` → ``n_ctx=65536``).
        Skipped when ``max_model_len`` is already present (vLLM models).

        Args:
            profiles: Dictionary of profile configurations
            requested_ctx: Requested context length (None = use highest valid)
            is_cpu: Whether selecting from CPU profiles

        Returns:
            Profile loader config dict, or None if requested_ctx has no exact match.
            None signals a hard reject — callers must not attempt to load the model.
        """
        if not profiles:
            return {}

        selected_key: str | None = None
        loader_config: dict[str, Any] = {}

        if requested_ctx is None:
            # Use the profile with the highest integer key that has valid resources
            profile_keys = [key for key in profiles.keys() if key.isdigit()]
            if profile_keys:
                # Filter for profiles with non-null ram_mb and vram_mb
                valid_keys = []
                for key in profile_keys:
                    profile = profiles[key]
                    resources = profile.get("resources", {})
                    if is_cpu:
                        # For CPU profiles, vram_mb should be 0
                        if resources.get("vram_mb") == 0:
                            valid_keys.append(key)
                    else:
                        # For GPU profiles, both should be non-null
                        if (
                            resources.get("ram_mb") is not None
                            and resources.get("vram_mb") is not None
                        ):
                            valid_keys.append(key)

                # Use highest key among valid profiles, or fall back to highest
                selected_key = (
                    max(valid_keys, key=int)
                    if valid_keys
                    else max(profile_keys, key=int)
                )
                loader_config = profiles[selected_key].get("loader", {}).copy()
            else:
                # No numeric keys - use first profile (named profiles like "default")
                first_profile = list(profiles.values())[0]
                loader_config = first_profile.get("loader", {}).copy()
        else:
            # Find the best matching context length
            ctx_key = str(requested_ctx)
            if ctx_key in profiles:
                selected_key = ctx_key
                loader_config = profiles[ctx_key].get("loader", {}).copy()
            else:
                # ∀ synthetic model ID with context suffix: exact profile key required.
                # Stargate owns context selection; Gateway must not silently substitute.
                numeric_keys = sorted(
                    int(k) for k in profiles.keys() if k.isdigit()
                )
                logger.error(
                    f"[registry] Context {requested_ctx} not found in profiles. "
                    f"Available: {numeric_keys}. "
                    f"Re-run model measurement to add this context profile."
                )
                return None

        # Profile key is the authoritative context length for llama-cpp GGUF profiles.
        # ∀ numeric profile key k: loader["n_ctx"] must equal int(k).
        # An explicit n_ctx in the loader (from a stale measurement pass) must not
        # override the profile key — the synthetic model ID encodes the intended context.
        # vLLM models use max_model_len instead; skip for those.
        if selected_key and selected_key.isdigit():
            ctx_value = int(selected_key)
            if "max_model_len" not in loader_config:
                stale_value = loader_config.get("n_ctx")
                if stale_value is not None and stale_value != ctx_value:
                    logger.error(
                        f"[registry] Stale n_ctx={stale_value} in profile '{selected_key}' "
                        f"loader overridden to ctx_value={ctx_value}. "
                        f"Re-run model measurement on this edge node to correct the catalog."
                    )
                loader_config["n_ctx"] = ctx_value

        logger.info(
            f"[registry] _select_profile_loader: selected_key={selected_key}, "
            f"profile_loader keys={list(loader_config.keys())}"
        )
        return loader_config

    def resolve_model_id(
        self, model_id: str
    ) -> tuple[str, dict[str, Any] | None, str | None]:
        """
        Resolve model ID to config, validating suffixes and normalizing.

        This is the SINGLE ENTRYPOINT for suffix validation in Gateway.
        Returns normalized model ID (strips -hybrid suffix) for consistent
        operations across Gateway and Stargate.

        Args:
            model_id: Model ID to resolve (can be synthetic or base)

        Returns:
            Tuple of (normalized_model_id, config, error_message)
            - If error_message is not None, request should be rejected with 400
            - normalized_model_id is the input model_id after suffix normalization
              (e.g., 'model-8192-hybrid' -> 'model-8192')
            - config is the model configuration dict, or None if not found

        Examples:
            >>> resolve_model_id('model-8192-hybrid')
            ('model-8192', {...config...}, None)
            >>> resolve_model_id('model-8192-cpu')
            ('model-8192-cpu', {...config...}, None)
        """
        error = _validate_model_id_suffixes(model_id)
        if error:
            return model_id, None, error

        # Normalize by stripping -hybrid suffix (informational only)
        canonical_id = normalize_model_id(model_id)

        resolution = SyntheticModelResolver.resolve_synthetic_id(canonical_id)
        if resolution:
            base_model_id, context_length, is_cpu, is_hybrid = resolution
            config = self.get_model_config(base_model_id)
            return canonical_id, config, None

        config = self.get_model_config(canonical_id)
        return canonical_id, config, None

    def get_model_loader_config(
        self, model_id: str, requested_ctx: int | None = None
    ) -> dict[str, Any] | None:
        """
        Get model loader configuration compiled from base_loader and specific profile.

        Normalizes model_id for catalog lookup (strips -hybrid suffix).
        Accepts synthetic model IDs (e.g., 'model-name-32768-cpu') and extracts
        context length and CPU/GPU specification from the ID.
        """
        # Normalize for catalog lookup
        canonical_id = normalize_model_id(model_id)

        # Try to resolve as synthetic ID
        synthetic_info = self._resolve_synthetic_id_info(canonical_id)
        if synthetic_info:
            base_model_id, context_length, is_cpu, is_hybrid = synthetic_info
            # Use the context length from synthetic ID, override requested_ctx
            requested_ctx = context_length
        else:
            # Not a synthetic ID, use requested_ctx as-is
            is_cpu = False

        model_config = self.get_model_config(canonical_id)
        if not model_config:
            return None

        # For simple loader format
        if "loader" in model_config:
            return model_config.get("loader", {})

        # For profile-based format
        base_loader = model_config.get("base_loader", {})

        # Determine which profile type to use
        if synthetic_info and is_cpu:
            # Use cpu_profiles for CPU synthetic IDs
            profiles = model_config.get("cpu_profiles", {})
        else:
            # Use regular profiles for GPU or base model IDs
            profiles = model_config.get("profiles", {})

        # Select profile-specific loader overrides if profiles exist.
        # Returns None when requested_ctx has no exact profile match — hard reject.
        profile_loader = self._select_profile_loader(profiles, requested_ctx, is_cpu)
        if profile_loader is None:
            return None

        # Merge base_loader with profile-specific overrides
        # base_loader contains shared config (including vision params)
        # profile_loader contains context-specific overrides (n_ctx, n_gpu_layers)
        merged = {**base_loader, **profile_loader}
        logger.info(
            f"[registry] Merged loader config: base keys={list(base_loader.keys())}, "
            f"profile keys={list(profile_loader.keys())}, "
            f"merged keys={list(merged.keys())}"
        )
        return merged
