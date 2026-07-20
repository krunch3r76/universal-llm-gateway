"""Main model registry class composing catalog, loader, and availability mixins."""

from typing import Any

from src.schemas.model_info import ModelValidationReport

from .availability import AvailabilityMixin
from .catalog import CatalogMixin
from .identifiers import IdentityMixin
from .info import InfoMixin
from .listings import ListingsMixin
from .loaded import LoadedModelsMixin
from .loaders import LoaderMixin
from .resources import ResourcesMixin


class ModelRegistry(
    IdentityMixin,
    CatalogMixin,
    InfoMixin,
    ListingsMixin,
    ResourcesMixin,
    LoaderMixin,
    AvailabilityMixin,
    LoadedModelsMixin,
):
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
