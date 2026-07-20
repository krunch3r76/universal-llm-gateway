"""V2 catalog metadata validation for quant values, devices, and activated contexts."""

from typing import Any

from universal_logging import get_logger

from .types import MetadataIssue

logger = get_logger(__name__)


def validate_catalog_metadata(catalog: dict[str, Any]) -> list[MetadataIssue]:
    """Validate V2 catalog metadata for correctness."""
    issues: list[MetadataIssue] = []
    models = catalog.get("models", {})

    for model_id, model_entry in models.items():
        if not isinstance(model_entry, dict):
            continue

        metadata = model_entry.get("metadata", {})
        devices = model_entry.get("devices", {})

        quant = metadata.get("quant")
        if quant is not None:
            model_name = metadata.get("name", model_id)
            issues.extend(validate_quant_consistency(model_id, model_name, quant))

        if not devices:
            issues.append(
                MetadataIssue(
                    model_id=model_id,
                    field="devices",
                    issue="No devices configured",
                    severity="error",
                )
            )
            continue

        for device_type in ["gpu", "cpu", "hybrid"]:
            device_config = devices.get(device_type, {})
            profiles = device_config.get("profiles", {})
            activated_key = f"activated_{device_type}_contexts"
            activated = metadata.get(activated_key, [])

            if profiles and not activated:
                issues.append(
                    MetadataIssue(
                        model_id=model_id,
                        field=activated_key,
                        issue=f"{device_type} profiles exist but {activated_key} is missing",
                        severity="warning",
                    )
                )

    return issues


def validate_quant_consistency(
    model_id: str, model_name: str, quant: Any
) -> list[MetadataIssue]:
    """Validate quant value matches model quantization."""
    issues: list[MetadataIssue] = []
    name_upper = model_name.upper()

    quant_patterns = {
        "Q2_K": 2,
        "Q3_K": 3,
        "Q4_K": 4,
        "Q4_0": 4,
        "Q4_1": 4,
        "Q5_K": 5,
        "Q5_0": 5,
        "Q5_1": 5,
        "Q6_K": 6,
        "Q8_0": 8,
        "Q8_K": 8,
        "IQ1": 1,
        "IQ2": 2,
        "IQ3": 3,
        "IQ4": 4,
    }

    detected_quant = None
    for pattern, bits in quant_patterns.items():
        if pattern in name_upper:
            detected_quant = bits
            break

    if detected_quant is not None:
        try:
            quant_int = int(quant)
            if quant_int != detected_quant:
                issues.append(
                    MetadataIssue(
                        model_id=model_id,
                        field="quant",
                        issue=f"Quant value {quant} doesn't match detected {detected_quant}-bit quantization in model name",
                        severity="error",
                    )
                )
        except (ValueError, TypeError):
            issues.append(
                MetadataIssue(
                    model_id=model_id,
                    field="quant",
                    issue=f"Invalid quant value: {quant} (should be integer)",
                    severity="error",
                )
            )

    return issues


def log_metadata_validation_results(issues: list[MetadataIssue]) -> None:
    """Log metadata validation issues in a readable format."""
    if not issues:
        logger.info("✅ Metadata validation: All models have correct metadata")
        return

    errors = [i for i in issues if i.severity == "error"]
    warnings = [i for i in issues if i.severity == "warning"]

    if errors:
        logger.error(f"❌ Found {len(errors)} metadata error(s):")
        for issue in errors:
            logger.error(f"  • {issue.model_id}.{issue.field}: {issue.issue}")

    if warnings:
        logger.warning(f"⚠️ Found {len(warnings)} metadata warning(s):")
        for issue in warnings:
            logger.warning(f"  • {issue.model_id}.{issue.field}: {issue.issue}")
