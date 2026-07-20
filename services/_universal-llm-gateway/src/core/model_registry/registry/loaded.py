"""Runtime tracking of loaded model instances keyed by canonical model ID."""

from typing import Any

from universal_logging import get_logger

logger = get_logger(__name__)


class LoadedModelsMixin:
    """Register, unregister, and query in-memory loaded model worker instances."""

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
