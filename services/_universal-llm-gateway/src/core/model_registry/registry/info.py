"""ModelInfo construction, enablement checks, and catalog count statistics."""

from src.core.model_registry.metadata import ModelMetadata
from src.schemas.model_info import ModelInfo

from .identifiers import normalize_model_id


class InfoMixin:
    """Build ModelInfo schemas and query enabled status from catalog entries."""

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
