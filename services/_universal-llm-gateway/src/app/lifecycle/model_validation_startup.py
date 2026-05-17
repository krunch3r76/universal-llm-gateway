"""Startup model/resource validation phase for the gateway lifecycle.

Owns the "availability check disabled but validation still runs" behavior
and profile-resource validation logging. Extracted so that the FastAPI
lifespan remains a thin orchestrator.
"""

import os

from ...core.model_registry.validation import ModelValidator
from .logging_bootstrap import get_gateway_logger


def validate_gateway_models(model_registry):
    """Run model file validation and profile resource validation.

    Always executes `model_registry.validate_model_files(fast_mode=True)`
    regardless of the ENABLE_MODEL_AVAILABILITY_CHECK setting. This prevents
    the gateway from advertising models it cannot actually load, which would
    cause routing failures downstream.

    When the env var is disabled (set to anything other than "true"), a
    warning is emitted explaining that validation still ran. If any models
    are invalid under that setting, an additional warning with counts is logged.

    Profile resource validation (critical for eviction logic) is performed
    unconditionally via ModelValidator and its results are logged.

    The original absolute import `from src.core.model_registry.validation import
    ModelValidator` has been converted to the required package-relative form
    `from ...core.model_registry.validation import ModelValidator` because
    this module lives at src/app/lifecycle/ depth.

    Args:
        model_registry: The initialized ModelRegistry instance.

    Returns:
        The validation_report returned by model_registry.validate_model_files,
        or None if the call is skipped (never happens in current logic).
    """
    gateway_logger = get_gateway_logger()

    # Validate models (can be skipped for faster startup)
    # NOTE: Validation now always runs regardless of this setting to prevent
    # gateways from advertising models they cannot load (prevents routing failures)
    enable_availability_check = (
        os.getenv("ENABLE_MODEL_AVAILABILITY_CHECK", "true").lower() == "true"
    )
    # CRITICAL FIX: Always validate model files to prevent advertising
    # missing models. Ensures gateways advertise loadable models only.
    validation_report = model_registry.validate_model_files(fast_mode=True)

    if not enable_availability_check:
        if gateway_logger is not None:
            gateway_logger.warning(
                "Model availability check was configured as disabled, but validation "
                "still runs to prevent advertising missing model files. "
                f"Validation completed: valid={validation_report.valid_models}/"
                f"{validation_report.total_models} models"
            )
            if validation_report.valid_models < validation_report.total_models:
                invalid_count = (
                    validation_report.total_models - validation_report.valid_models
                )
                gateway_logger.warning(
                    f"Model validation issues detected: "
                    f"valid={validation_report.valid_models}, "
                    f"total={validation_report.total_models}, "
                    f"invalid={invalid_count}"
                )

    # Validate profile resources (critical for eviction to work)
    # Converted from the original `from src.core...` absolute import.
    models_config = model_registry.model_loaders_config.get("models", {})
    profile_issues = ModelValidator.validate_profile_resources(models_config)
    ModelValidator.log_profile_validation_results(profile_issues)

    return validation_report
