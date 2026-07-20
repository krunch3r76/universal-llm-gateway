"""ModelMetadataAdapter facade wiring registry data to API-friendly responses.

Thin orchestration class that loads gateway inference-engine config and delegates
formatting to chat-template, inference-engine, and comprehensive metadata modules.
"""

from typing import Any

from universal_logging import get_logger

from ...core.model_registry import ModelRegistry
from .chat_template import get_chat_template_from_config, has_working_chat_template
from .comprehensive_metadata import get_comprehensive_model_metadata
from .inference_engine import get_inference_engine_info

logger = get_logger(__name__)


class ModelMetadataAdapter:
    """
    Adapter for providing API-friendly model metadata formatting.

    This adapter takes metadata already extracted by the ModelRegistry and:
    - Reshapes it for API/HTTP consumption
    - Adds API-specific logic (overrides, inference engine specs)
    - Provides unified interface for model metadata queries
    - Serves as formatting layer between registry and API endpoints

    Note: This adapter does NOT analyze model files directly - that's done by the
    ModelRegistry.
    """

    def __init__(
        self, model_registry: ModelRegistry, gateway_config: Any | None = None
    ):
        self.registry = model_registry
        self.gateway_config = gateway_config
        self._load_configuration()

    def _load_configuration(self):
        """Load configuration from gateway config or use minimal fallbacks"""
        if self.gateway_config:
            self.inference_engine_specs = getattr(
                self.gateway_config, "inference_engines", {}
            )
            self.model_type_patterns = getattr(
                self.gateway_config, "model_type_patterns", {}
            )
        else:
            logger.warning("No gateway config available, using minimal fallbacks")
            self.inference_engine_specs = {}
            self.model_type_patterns = {}

    def _has_working_chat_template(self, model_path: str, model_format: str) -> bool:
        return has_working_chat_template(self.registry, model_path, model_format)

    def get_chat_template_from_config(
        self, model_id: str, model_config: dict[str, Any]
    ) -> dict[str, Any]:
        return get_chat_template_from_config(model_id, model_config)

    def get_model_type_from_config(
        self, model_id: str, model_config: dict[str, Any]
    ) -> str:
        from src.utils.model_utils import detect_model_type

        model_path = model_config.get("path") if model_config else None
        return detect_model_type(model_id, model_config, model_path)

    def get_inference_engine_info(self, model_id: str) -> dict[str, Any]:
        return get_inference_engine_info(
            self.registry, model_id, self.inference_engine_specs
        )

    def get_comprehensive_model_metadata(self, model_id: str) -> dict[str, Any] | None:
        return get_comprehensive_model_metadata(
            self.registry,
            model_id,
            self.inference_engine_specs,
            self.get_model_type_from_config,
        )

    def get_all_models_metadata(self) -> dict[str, dict[str, Any]]:
        """Get comprehensive metadata for all models."""
        all_metadata = {}

        models_list = self.registry.list_models()
        logger.debug(f"list_models() returned {len(models_list)} models")

        for model_info in models_list:
            logger.debug(f"Processing {model_info.id}")
            metadata = self.get_comprehensive_model_metadata(model_info.id)
            logger.debug(f"Got metadata for {model_info.id}: {metadata is not None}")
            if metadata:
                all_metadata[model_info.id] = metadata

        logger.debug(f"Returning {len(all_metadata)} models")
        return all_metadata
