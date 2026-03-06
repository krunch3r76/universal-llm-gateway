"""Models.yaml configuration validation."""

from __future__ import annotations

from pathlib import Path


def validate_models_file(yaml_path: Path) -> tuple[bool, list[str]]:
    """
    Validate a models.yaml configuration file.

    Returns:
        (is_valid, errors) tuple
    """
    import yaml

    errors = []

    try:
        with yaml_path.open() as f:
            data = yaml.safe_load(f) or {}

        # Check 1: Must have 'models:' top-level key
        if "models" not in data:
            errors.append(
                "Missing 'models:' top-level key. "
                "All models must be under 'models:' wrapper"
            )
            return (False, errors)

        models = data["models"]
        if not isinstance(models, dict):
            errors.append("'models:' must be a dictionary")
            return (False, errors)

        # Check 2: Validate each model
        for model_ref, model_config in models.items():
            if not isinstance(model_config, dict):
                errors.append(
                    f"Model '{model_ref}': Must be a dictionary, "
                    f"got {type(model_config).__name__}"
                )
                continue

            # Check required 'model' field
            if "model" not in model_config:
                errors.append(f"Model '{model_ref}': Missing required 'model' field")

            # Check for common mistake: 'model_id' instead of 'model'
            if "model_id" in model_config:
                errors.append(
                    f"Model '{model_ref}': Found 'model_id' field. "
                    f"Should be 'model' (not 'model_id')"
                )

            # Validate allowed fields — mirrors ModelRef schema (schemas.py).
            # ModelRef has extra="allow" for execution hints (chunk_size, etc.);
            # only explicit schema fields are listed here.
            allowed_fields = {
                "model",
                "system_prompt",
                "description",
                "profile",
                "execution",
                "prompt_override",
            }
            unknown_fields = set(model_config.keys()) - allowed_fields
            if unknown_fields:
                errors.append(
                    f"Model '{model_ref}': Unknown fields: {unknown_fields}. "
                    f"Allowed: {allowed_fields}"
                )

        return (len(errors) == 0, errors)

    except Exception as e:
        import yaml as yaml_module

        if isinstance(e, yaml_module.YAMLError):
            return (False, [f"YAML parsing error: {e}"])
        return (False, [f"Unexpected error: {e}"])
