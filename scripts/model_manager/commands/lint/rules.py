"""
V2 Schema Compliance Rules.

Source of truth: services/_universal-llm-gateway/src/core/catalog/constants.py
These are duplicated here to avoid cross-domain imports (model-manager → Gateway).
If constants change, update both locations.
"""

from typing import Any, TypedDict


class LintIssue(TypedDict):
    """Lint validation issue."""

    severity: str  # "error" | "warning"
    model_id: str
    message: str
    fix: str


# V2 valid values (mirror Gateway's constants.py)
VALID_SCHEMAS = {
    "llama-cpp",
    "vllm",
    "exllamav3",
    "faster-whisper",
    "diffusers",
    "ctranslate2",
}
VALID_DEVICES = {"gpu", "cpu", "hybrid"}

# Schema → valid formats mapping
SCHEMA_FORMATS = {
    "llama-cpp": {"gguf"},
    "vllm": {"hf", "awq", "gptq"},
    "exllamav3": {"exl3"},
    "faster-whisper": {"whisper"},
    "diffusers": {"flux2"},
    "ctranslate2": {"ct2"},
}

# Schemas that support CPU device
SCHEMAS_WITH_CPU = {"llama-cpp", "faster-whisper", "ctranslate2"}

# Schemas that require numeric context profiles (vs named profiles)
SCHEMAS_WITH_CONTEXT_PROFILES = {"llama-cpp", "vllm", "exllamav3"}

# V1 keys that must not exist (fail-fast detection)
V1_KEYS = {"configurations", "base_loader"}


def lint_catalog(catalog: dict[str, Any]) -> list[LintIssue]:
    """
    Lint catalog for V2 schema compliance.

    Returns list of issues with severity, model_id, message, fix.
    """
    issues: list[LintIssue] = []

    # Type guard: catalog must be dict
    if not isinstance(catalog, dict):
        issues.append(
            {
                "severity": "error",
                "model_id": "_catalog",
                "message": f"Catalog must be dict, got {type(catalog).__name__}",
                "fix": "Ensure YAML structure is a dictionary",
            }
        )
        return issues

    # Check schema version
    schema_version = catalog.get("schema_version")
    if schema_version != 2:
        issues.append(
            {
                "severity": "error",
                "model_id": "_catalog",
                "message": f"Expected schema_version=2, got {schema_version}",
                "fix": "Set schema_version: 2",
            }
        )

    # Check for V1 top-level keys
    for v1_key in V1_KEYS:
        if v1_key in catalog:
            issues.append(
                {
                    "severity": "error",
                    "model_id": "_catalog",
                    "message": f"V1 key '{v1_key}' at catalog level",
                    "fix": f"Remove '{v1_key}' (V2 uses 'models' only)",
                }
            )

    models = catalog.get("models", {})

    # Type guard: models must be dict
    if not isinstance(models, dict):
        issues.append(
            {
                "severity": "error",
                "model_id": "_catalog",
                "message": f"'models' must be dict, got {type(models).__name__}",
                "fix": "Ensure 'models' is a dictionary",
            }
        )
        return issues

    for model_id, entry in models.items():
        if not isinstance(entry, dict):
            issues.append(
                {
                    "severity": "error",
                    "model_id": str(model_id),
                    "message": f"Model entry must be dict, got {type(entry).__name__}",
                    "fix": "Ensure model entry is a dictionary",
                }
            )
            continue
        issues.extend(_lint_model(str(model_id), entry))

    return issues


def _lint_model(model_id: str, entry: dict[str, Any]) -> list[LintIssue]:
    """Lint single model entry."""
    issues: list[LintIssue] = []

    # Check for V1 keys
    for v1_key in V1_KEYS:
        if v1_key in entry:
            issues.append(
                {
                    "severity": "error",
                    "model_id": model_id,
                    "message": f"V1 key '{v1_key}' found",
                    "fix": "Use 'devices' instead of 'configurations', 'loader' instead of 'base_loader'",
                }
            )

    # Check schema field (REQUIRED in V2)
    schema = entry.get("schema")
    if not schema:
        issues.append(
            {
                "severity": "error",
                "model_id": model_id,
                "message": "Missing 'schema' field (required in V2)",
                "fix": f"Add schema: <engine> (one of {sorted(VALID_SCHEMAS)})",
            }
        )
        return issues  # Can't validate further without schema

    if schema not in VALID_SCHEMAS:
        issues.append(
            {
                "severity": "error",
                "model_id": model_id,
                "message": f"Unknown schema '{schema}'",
                "fix": f"Use one of: {sorted(VALID_SCHEMAS)}",
            }
        )
        return issues

    # Check format compatibility
    metadata = entry.get("metadata", {})
    if not isinstance(metadata, dict):
        issues.append(
            {
                "severity": "error",
                "model_id": model_id,
                "message": f"'metadata' must be dict, got {type(metadata).__name__}",
                "fix": "Ensure metadata is a dictionary",
            }
        )
        return issues

    model_format = metadata.get("format")

    if not model_format:
        issues.append(
            {
                "severity": "error",
                "model_id": model_id,
                "message": "Missing metadata.format",
                "fix": "Add format to metadata",
            }
        )
    elif model_format not in SCHEMA_FORMATS.get(schema, set()):
        expected = SCHEMA_FORMATS.get(schema, set())
        issues.append(
            {
                "severity": "error",
                "model_id": model_id,
                "message": f"Format '{model_format}' incompatible with schema '{schema}'",
                "fix": f"Expected: {sorted(expected)}",
            }
        )

    # Check metadata.engine removed (V2: derived from schema)
    if "engine" in metadata:
        issues.append(
            {
                "severity": "warning",
                "model_id": model_id,
                "message": "metadata.engine is deprecated (derived from schema)",
                "fix": "Remove engine from metadata",
            }
        )

    # Check devices
    devices = entry.get("devices", {})
    if not devices:
        issues.append(
            {
                "severity": "error",
                "model_id": model_id,
                "message": "No devices configured",
                "fix": "Add devices section with gpu/cpu profiles",
            }
        )
        return issues

    # Type guard: devices must be dict
    if not isinstance(devices, dict):
        issues.append(
            {
                "severity": "error",
                "model_id": model_id,
                "message": f"'devices' must be dict, got {type(devices).__name__}",
                "fix": "Ensure devices is a dictionary",
            }
        )
        return issues

    for device_name, device_config in devices.items():
        if device_name not in VALID_DEVICES:
            issues.append(
                {
                    "severity": "error",
                    "model_id": model_id,
                    "message": f"Invalid device '{device_name}'",
                    "fix": f"Use one of: {sorted(VALID_DEVICES)}",
                }
            )
            continue

        # Check CPU support
        if device_name == "cpu" and schema not in SCHEMAS_WITH_CPU:
            issues.append(
                {
                    "severity": "error",
                    "model_id": model_id,
                    "message": f"Schema '{schema}' does not support CPU",
                    "fix": "Remove cpu device (GPU-only engine)",
                }
            )

        # Check profiles
        if not isinstance(device_config, dict):
            issues.append(
                {
                    "severity": "error",
                    "model_id": model_id,
                    "message": f"Device '{device_name}' must be a dict",
                    "fix": "Device should have profiles key",
                }
            )
            continue

        profiles = device_config.get("profiles", {})
        if not profiles:
            # Missing profiles is a warning (models may not be measured yet)
            issues.append(
                {
                    "severity": "warning",
                    "model_id": model_id,
                    "message": f"Device '{device_name}' has no profiles",
                    "fix": "Add profiles or run measurement",
                }
            )
        elif not isinstance(profiles, dict):
            issues.append(
                {
                    "severity": "error",
                    "model_id": model_id,
                    "message": f"Device '{device_name}' profiles must be dict",
                    "fix": "Ensure profiles is a dictionary",
                }
            )

    return issues
