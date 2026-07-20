"""ModelValidator facade delegating to file, profile, and metadata validation modules."""

from typing import Any

from ....schemas.model_info import ModelValidationReport
from . import file_validation, metadata_validation, profile_validation
from .types import MetadataIssue, ProfileIssue


class ModelValidator:
    """Model validation utilities"""

    @staticmethod
    def validate_model_files(
        models: dict[str, Any], fast_mode: bool = True
    ) -> ModelValidationReport:
        return file_validation.validate_model_files(models, fast_mode)

    @staticmethod
    def _validate_single_model(model_id: str, metadata: Any, fast_mode: bool = True):
        return file_validation.validate_single_model(model_id, metadata, fast_mode)

    @staticmethod
    def validate_profile_resources(models_config: dict[str, Any]) -> list[ProfileIssue]:
        return profile_validation.validate_profile_resources(models_config)

    @staticmethod
    def log_profile_validation_results(issues: list[ProfileIssue]) -> None:
        profile_validation.log_profile_validation_results(issues)

    @staticmethod
    def validate_catalog_metadata(catalog: dict[str, Any]) -> list[MetadataIssue]:
        return metadata_validation.validate_catalog_metadata(catalog)

    @staticmethod
    def _validate_quant_consistency(
        model_id: str, model_name: str, quant: Any
    ) -> list[MetadataIssue]:
        return metadata_validation.validate_quant_consistency(
            model_id, model_name, quant
        )

    @staticmethod
    def log_metadata_validation_results(issues: list[MetadataIssue]) -> None:
        metadata_validation.log_metadata_validation_results(issues)
