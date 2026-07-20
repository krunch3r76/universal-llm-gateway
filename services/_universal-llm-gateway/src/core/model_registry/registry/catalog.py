"""Catalog key lookup and raw model configuration path resolution from YAML."""

from typing import Any

from src.core.synthetic_models import SyntheticModelResolver

from .identifiers import normalize_model_id


class CatalogMixin:
    """Resolve OpenAI or synthetic IDs to catalog keys, configs, and paths."""

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
