"""
Synthetic Model ID Generation and Resolution

This module provides functionality to generate synthetic model IDs with explicit
context lengths and CPU variants, and to resolve synthetic IDs back to base model
configurations and profile selections.
"""

from dataclasses import dataclass
from typing import Any

from universal_logging import get_logger

from .formats import is_named_profile_format

logger = get_logger(__name__)


@dataclass
class SyntheticModel:
    """Synthetic model variant with context length and CPU/GPU/Hybrid specification."""

    synthetic_id: str
    """Synthetic model ID (e.g., 'model-131072-cpu' or 'model-8192-hybrid')"""

    base_model_id: str
    """Base model ID from YAML (e.g., 'gpt-oss-20b-mxfp4')"""

    context_length: int
    """Context length for this profile"""

    is_cpu: bool
    """True if this is a CPU profile, False for GPU/Hybrid profile"""

    is_hybrid: bool
    """True if n_gpu_layers > 0 (partial GPU offload), False for full GPU or CPU"""

    profile_type: str
    """Either 'profiles' or 'cpu_profiles'"""

    openai_fields: dict[str, Any]
    """OpenAI API fields for this model variant"""


class SyntheticModelResolver:
    """
    Generates and resolves synthetic model IDs.

    Synthetic model IDs follow the pattern:
    - GPU (full): {base-id}-{context_length}
    - CPU: {base-id}-{context_length}-cpu
    - Hybrid (partial GPU): {base-id}-{context_length}-hybrid
    """

    @staticmethod
    def generate_synthetic_id(
        base_model_id: str,
        context_length: int,
        is_cpu: bool = False,
        is_hybrid: bool = False,
    ) -> str:
        """
        Generate a synthetic model ID from base model ID and context length.

        Args:
            base_model_id: Base model identifier from YAML
            context_length: Context length for this profile
            is_cpu: Whether this is a CPU profile
            is_hybrid: Whether this is a hybrid profile (partial GPU offload)

        Returns:
            Synthetic model ID (e.g., 'model-32768', 'model-32768-cpu',
            or 'model-32768-hybrid')

        Raises:
            ValueError: If both is_cpu and is_hybrid are True (mutually exclusive)
        """
        if is_cpu and is_hybrid:
            raise ValueError("is_cpu and is_hybrid are mutually exclusive")

        synthetic_id = f"{base_model_id}-{context_length}"
        if is_cpu:
            synthetic_id += "-cpu"
        elif is_hybrid:
            synthetic_id += "-hybrid"
        return synthetic_id

    @staticmethod
    def resolve_synthetic_id(synthetic_id: str) -> tuple[str, int, bool, bool] | None:
        """
        Resolve a synthetic model ID to its components.

        Extracts (base_id, context_length, is_cpu, is_hybrid) from synthetic IDs
        that follow the pattern: {base}-{context}[-cpu|-hybrid]

        Args:
            synthetic_id: Synthetic model ID to resolve

        Returns:
            Tuple of (base_model_id, context_length, is_cpu, is_hybrid) or None
            if the ID doesn't follow the synthetic ID pattern.

        Note:
            Named-profile models (audio/vision) don't use synthetic IDs.
            Returns None for models that don't match the context-length pattern.
        """
        is_cpu = synthetic_id.endswith("-cpu")
        is_hybrid = synthetic_id.endswith("-hybrid")

        working_id = synthetic_id
        if is_cpu:
            working_id = working_id[:-4]  # Remove "-cpu"
        elif is_hybrid:
            working_id = working_id[:-7]  # Remove "-hybrid"

        parts = working_id.rsplit("-", 1)
        if len(parts) != 2:
            # No hyphen-number pattern - not a synthetic ID
            return None

        base_model_id, context_str = parts

        try:
            context_length = int(context_str)
        except ValueError:
            # Last segment isn't a number - not a synthetic ID
            return None

        return (base_model_id, context_length, is_cpu, is_hybrid)

    @staticmethod
    def _is_cpu_configuration(model_config: dict[str, Any]) -> bool:
        """
        Determine if a model configuration is CPU-based.

        V2 Format: Checks devices.cpu existence (no configurations key).

        Args:
            model_config: Model configuration dictionary (V2 format)

        Returns:
            True if this is a CPU configuration, False otherwise
        """
        # V2: Check devices structure directly
        devices = model_config.get("devices", {})

        # Has CPU device configured
        if "cpu" in devices:
            return True

        # Only has CPU profiles in converted format
        if "cpu_profiles" in model_config and "profiles" not in model_config:
            return True

        return False

    @staticmethod
    def get_profile_type(is_cpu: bool, is_hybrid: bool) -> str:
        """
        Derive profile type from CPU and hybrid flags.

        Args:
            is_cpu: Whether this is a CPU profile
            is_hybrid: Whether this is a hybrid profile

        Returns:
            'cpu_profiles' if CPU, 'profiles' otherwise (GPU or hybrid)
        """
        return "cpu_profiles" if is_cpu else "profiles"

    @staticmethod
    def generate_synthetic_models(config: dict[str, Any]) -> list[SyntheticModel]:
        """
        Generate all synthetic models from configuration.

        For text models (GGUF, HF, AWQ, etc.): generates context-length suffixed IDs
        For audio/vision models: generates base model IDs without context suffix
        """
        synthetic_models = []
        models = config.get("models", {})

        for base_model_id, model_config in models.items():
            if not isinstance(model_config, dict):
                continue

            model_info = model_config.get("info", {})
            if not model_info:
                continue

            format_type = model_info.get("format")

            # Named profile formats: expose base model ID only (no context suffix)
            if is_named_profile_format(format_type):
                synthetic_models.extend(
                    SyntheticModelResolver._generate_named_profile_models(
                        base_model_id, model_config, model_info
                    )
                )
                continue

            # Context-based formats: generate context-suffixed IDs
            if format_type == "gguf":
                synthetic_models.extend(
                    SyntheticModelResolver._generate_gguf_models(
                        base_model_id, model_config, model_info
                    )
                )
            else:
                # Non-GGUF text formats (HF, AWQ, GPTQ, etc.)
                synthetic_models.extend(
                    SyntheticModelResolver._generate_vllm_models(
                        base_model_id, model_config, model_info
                    )
                )

        return synthetic_models

    @staticmethod
    def _generate_named_profile_models(
        base_model_id: str,
        model_config: dict[str, Any],
        model_info: dict[str, Any],
    ) -> list[SyntheticModel]:
        """Generate synthetic models for named-profile formats (audio, vision)."""
        openai_fields = model_info.get("openai_api_fields", {}).copy()
        openai_fields["id"] = base_model_id

        # Detect CPU variant by inspecting configurations
        is_cpu = SyntheticModelResolver._is_cpu_configuration(model_config)
        profile_type = "cpu_profiles" if is_cpu else "profiles"

        synthetic_model = SyntheticModel(
            synthetic_id=base_model_id,
            base_model_id=base_model_id,
            context_length=0,  # Not applicable for non-text models
            is_cpu=is_cpu,
            is_hybrid=False,
            profile_type=profile_type,
            openai_fields=openai_fields,
        )

        return [synthetic_model]

    @staticmethod
    def _generate_gguf_models(
        base_model_id: str,
        model_config: dict[str, Any],
        model_info: dict[str, Any],
    ) -> list[SyntheticModel]:
        """Generate synthetic models for GGUF format (GPU, CPU, hybrid variants)."""
        synthetic_models = []
        profiles = model_config.get("profiles", {})
        cpu_profiles = model_config.get("cpu_profiles", {})

        # Generate GPU/Hybrid profiles
        for context_str, profile_config in profiles.items():
            try:
                context_length = int(context_str)
            except ValueError:
                logger.warning(
                    "Invalid context length '%s' in model '%s'",
                    context_str,
                    base_model_id,
                )
                continue

            loader_config = profile_config.get("loader", {})
            # Handle n_gpu_layers at profile level (YAML format) or under loader (normalized format)
            n_gpu_layers = profile_config.get("n_gpu_layers")
            if n_gpu_layers is None:
                n_gpu_layers = loader_config.get("n_gpu_layers", -1)
            is_hybrid = n_gpu_layers > 0

            synthetic_id = SyntheticModelResolver.generate_synthetic_id(
                base_model_id, context_length, is_cpu=False, is_hybrid=is_hybrid
            )

            openai_fields = model_info.get("openai_api_fields", {}).copy()
            openai_fields["id"] = synthetic_id

            synthetic_models.append(
                SyntheticModel(
                    synthetic_id=synthetic_id,
                    base_model_id=base_model_id,
                    context_length=context_length,
                    is_cpu=False,
                    is_hybrid=is_hybrid,
                    profile_type="profiles",
                    openai_fields=openai_fields,
                )
            )

        # Generate CPU profiles
        for context_str, profile_config in cpu_profiles.items():
            try:
                context_length = int(context_str)
            except ValueError:
                logger.warning(
                    "Invalid context length '%s' in CPU profiles for model '%s'",
                    context_str,
                    base_model_id,
                )
                continue

            synthetic_id = SyntheticModelResolver.generate_synthetic_id(
                base_model_id, context_length, is_cpu=True, is_hybrid=False
            )

            openai_fields = model_info.get("openai_api_fields", {}).copy()
            openai_fields["id"] = synthetic_id

            synthetic_models.append(
                SyntheticModel(
                    synthetic_id=synthetic_id,
                    base_model_id=base_model_id,
                    context_length=context_length,
                    is_cpu=True,
                    is_hybrid=False,
                    profile_type="cpu_profiles",
                    openai_fields=openai_fields,
                )
            )

        return synthetic_models

    @staticmethod
    def _generate_vllm_models(
        base_model_id: str,
        model_config: dict[str, Any],
        model_info: dict[str, Any],
    ) -> list[SyntheticModel]:
        """Generate synthetic models for vLLM formats (HF, AWQ, GPTQ, etc.)."""
        synthetic_models = []
        profiles = model_config.get("profiles", {})

        for context_str, profile_config in profiles.items():
            try:
                context_length = int(context_str)
            except ValueError:
                logger.warning(
                    "Invalid context length '%s' in model '%s'",
                    context_str,
                    base_model_id,
                )
                continue

            synthetic_id = SyntheticModelResolver.generate_synthetic_id(
                base_model_id, context_length, is_cpu=False, is_hybrid=False
            )

            openai_fields = model_info.get("openai_api_fields", {}).copy()
            openai_fields["id"] = synthetic_id

            synthetic_models.append(
                SyntheticModel(
                    synthetic_id=synthetic_id,
                    base_model_id=base_model_id,
                    context_length=context_length,
                    is_cpu=False,
                    is_hybrid=False,
                    profile_type="profiles",
                    openai_fields=openai_fields,
                )
            )

        return synthetic_models

    @staticmethod
    def get_default_synthetic_models(config: dict[str, Any]) -> list[SyntheticModel]:
        """
        Get synthetic models to expose in /v1/models.

        Returns ALL synthetic models. Stargate handles filtering based on
        activated_gpu_contexts/activated_cpu_contexts from the catalog.

        Args:
            config: Complete model_loaders.yaml configuration dictionary

        Returns:
            List of all synthetic model objects
        """
        return SyntheticModelResolver.get_all_synthetic_models(config)

    @staticmethod
    def get_all_synthetic_models(config: dict[str, Any]) -> list[SyntheticModel]:
        """
        Get all synthetic models (all context variants).

        Args:
            config: Complete model_loaders.yaml configuration dictionary

        Returns:
            List of all synthetic model objects
        """
        return SyntheticModelResolver.generate_synthetic_models(config)

    @staticmethod
    def get_model_config_for_synthetic_id(
        config: dict[str, Any], synthetic_id: str
    ) -> tuple[dict[str, Any], dict[str, Any]] | None:
        """
        Get base model config and profile config for a synthetic model ID.

        Args:
            config: Complete model_loaders.yaml configuration dictionary
            synthetic_id: Synthetic model ID to resolve

        Returns:
            Tuple of (base_model_config, profile_config) or None if not found
        """
        models = config.get("models", {})

        # Try resolving as synthetic ID (context-based model)
        resolution = SyntheticModelResolver.resolve_synthetic_id(synthetic_id)
        if resolution:
            base_model_id, context_length, is_cpu, is_hybrid = resolution
            base_model_config = models.get(base_model_id)
            if not base_model_config:
                return None

            profile_type = SyntheticModelResolver.get_profile_type(is_cpu, is_hybrid)
            profiles = base_model_config.get(profile_type, {})
            context_str = str(context_length)
            profile_config = profiles.get(context_str)

            if not profile_config:
                return None

            return (base_model_config, profile_config)

        # Not a synthetic ID - try as named-profile model (direct catalog ID)
        base_model_config = models.get(synthetic_id)
        if not base_model_config:
            return None

        # Detect profile type from configuration
        is_cpu = SyntheticModelResolver._is_cpu_configuration(base_model_config)
        profile_type = "cpu_profiles" if is_cpu else "profiles"

        profile_config = SyntheticModelResolver.get_named_profile(
            config,
            synthetic_id,
            profile_type=profile_type,
        )

        if not profile_config:
            return None

        return (base_model_config, profile_config)

    @staticmethod
    def get_named_profile(
        config: dict[str, Any],
        model_id: str,
        profile_name: str = "default",
        profile_type: str = "profiles",
    ) -> dict[str, Any] | None:
        """
        Get a named profile for non-context-based models.

        Args:
            config: Complete model_loaders.yaml configuration
            model_id: Base model ID
            profile_name: Profile name to fetch (default: "default")
            profile_type: Either 'profiles' (GPU) or 'cpu_profiles' (CPU)

        Returns:
            Profile configuration dict or None if not found
        """
        models = config.get("models", {})
        model_config = models.get(model_id)
        if not model_config:
            return None

        profiles = model_config.get(profile_type, {})

        # Try requested profile name
        if profile_name in profiles:
            return profiles[profile_name]

        # Fallback to "default" if exists
        if "default" in profiles:
            return profiles["default"]

        # Last resort: first available profile
        if profiles:
            return next(iter(profiles.values()))

        return None
