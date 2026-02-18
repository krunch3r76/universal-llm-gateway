"""
Catalog Validation - Schema-driven validation.

V2 Architecture:
    - Validation logic delegated to engine schemas
    - Each schema knows its own validation rules
    - Strict enforcement: schema field REQUIRED

Invariants:
    ∀ model: validate(model) = schema.validate(model) ∪ common_checks
    ∀ issue: issue.severity ∈ {"error", "warning"}
    ∀ model: model.schema = None ⟹ ERROR (V2 required)
"""

from collections.abc import Callable
from typing import Any

from universal_logging import get_logger

from .schemas import SchemaRegistry, ValidationIssue

logger = get_logger(__name__)

__all__ = [
    "ValidationIssue",
    "validate_model",
    "validate_catalog",
    "log_validation_report",
    "get_validation_summary",
]


def validate_model(
    model_id: str,
    entry: dict[str, Any],
) -> list[ValidationIssue]:
    """
    Validate a single model entry.

    Delegates to the appropriate engine schema for validation.
    Adds common checks that apply to all models.

    Args:
        model_id: Model identifier
        entry: Model entry dict

    Returns:
        List of validation issues (empty if valid)
    """
    issues: list[ValidationIssue] = []

    # V2 requirement: schema field must be present (no derivation)
    schema_name = entry.get("schema")
    if not schema_name:
        issues.append(
            ValidationIssue(
                model_id=model_id,
                severity="error",
                message="Missing 'schema' field (V2 required)",
                field="schema",
                fix=(
                    "Add schema field: llama-cpp, vllm, exllamav3, "
                    "faster-whisper, diffusers, ctranslate2"
                ),
            )
        )
        return issues

    # Get schema (no fallback to format)
    schema = SchemaRegistry.get_for_model(entry)
    if not schema:
        issues.append(
            ValidationIssue(
                model_id=model_id,
                severity="error",
                message=f"Unknown schema '{schema_name}'",
                field="schema",
                fix=f"Use one of: {', '.join(SchemaRegistry.all_engines())}",
            )
        )
        return issues

    # Delegate to schema validation
    issues.extend(schema.validate(model_id, entry))

    return issues


def validate_model_by_id(
    model_id: str,
    get_model_fn: Callable[[str], dict[str, Any] | None],
) -> list[ValidationIssue]:
    """
    Validate a model by ID using a lookup function.

    Args:
        model_id: Model identifier
        get_model_fn: Function to retrieve model by ID

    Returns:
        List of validation issues
    """
    entry = get_model_fn(model_id)
    if not entry:
        return [
            ValidationIssue(
                model_id=model_id,
                severity="error",
                message="Model not found in catalog",
                field=None,
                fix="Check model ID spelling or add model to catalog",
            )
        ]

    return validate_model(model_id, entry)


def validate_catalog(catalog: dict[str, Any]) -> list[ValidationIssue]:
    """
    Validate entire catalog.

    Accepts schema_version 2 or 3 (V3 = static/local split architecture).

    Args:
        catalog: Catalog dict with 'models' key

    Returns:
        List of all validation issues across all models
    """
    issues: list[ValidationIssue] = []
    models = catalog.get("models", {})

    schema_version = catalog.get("schema_version")
    if schema_version not in (2, 3):
        issues.append(
            ValidationIssue(
                model_id="_catalog",
                severity="error",
                message=f"Expected schema_version 2 or 3, got {schema_version}",
                field="schema_version",
                fix="Migrate catalog to V3 format (run scripts/migrate_catalog_v3.py)",
            )
        )

    for model_id, entry in models.items():
        issues.extend(validate_model(model_id, entry))

    return issues


def log_validation_report(catalog: dict[str, Any]) -> int:
    """
    Validate catalog and log a summary report.

    Args:
        catalog: Catalog dict

    Returns:
        Count of error-severity issues
    """
    issues = validate_catalog(catalog)

    if not issues:
        logger.info("✅ Catalog validation passed: all models valid")
        return 0

    errors = [i for i in issues if i.severity == "error"]
    warnings = [i for i in issues if i.severity == "warning"]

    logger.warning(
        f"Catalog validation: {len(errors)} errors, {len(warnings)} warnings"
    )

    for issue in errors:
        logger.error(f"[{issue.model_id}] {issue.message}")
        if issue.fix:
            logger.error(f"  Fix: {issue.fix}")

    for issue in warnings:
        logger.warning(f"[{issue.model_id}] {issue.message}")
        if issue.fix:
            logger.warning(f"  Fix: {issue.fix}")

    return len(errors)


def get_validation_summary(catalog: dict[str, Any]) -> dict[str, Any]:
    """
    Get validation summary for API responses.

    Args:
        catalog: Catalog dict

    Returns:
        Summary dict with counts and issues
    """
    issues = validate_catalog(catalog)
    errors = [i for i in issues if i.severity == "error"]
    warnings = [i for i in issues if i.severity == "warning"]

    return {
        "valid": len(errors) == 0,
        "error_count": len(errors),
        "warning_count": len(warnings),
        "errors": [
            {
                "model_id": i.model_id,
                "message": i.message,
                "field": i.field,
                "fix": i.fix,
            }
            for i in errors
        ],
        "warnings": [
            {
                "model_id": i.model_id,
                "message": i.message,
                "field": i.field,
                "fix": i.fix,
            }
            for i in warnings
        ],
    }
