"""Categories.yaml validation for consensus pipelines."""

from __future__ import annotations

import re
from pathlib import Path

import yaml


def validate_categories_yaml(pipeline_dir: Path) -> tuple[bool, list[str]]:
    """
    Validate categories.yaml if present.

    Args:
        pipeline_dir: Directory containing categories.yaml

    Returns:
        (valid, errors) — valid=True if no errors
    """
    yaml_path = pipeline_dir / "categories.yaml"

    # Domain-based pipelines (e.g. v3.3) do not use categories.yaml
    if not yaml_path.exists():
        return True, []

    errors = []

    try:
        data = yaml.safe_load(yaml_path.read_text())
    except Exception as e:
        return False, [f"YAML parse error: {e}"]

    # Schema validation only when categories handler exists (e.g. v3.2)
    categories_module_path = pipeline_dir / "handlers" / "categories.py"
    if not categories_module_path.exists():
        return True, []

    try:
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "categories", categories_module_path
        )
        if spec and spec.loader:
            categories_module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(categories_module)
            categories_module.CategoriesConfig.model_validate(data)
        else:
            errors.append("Could not load CategoriesConfig module")
    except ImportError as e:
        errors.append(f"Could not import CategoriesConfig: {e}")
    except Exception as e:
        errors.append(f"Schema validation failed: {e}")

    return len(errors) == 0, errors


def validate_categories_prompt_alignment(
    pipeline_dir: Path,
) -> tuple[bool, list[str]]:
    """
    Cross-validate categories.yaml against classification prompt.

    Ensures categories in classification prompt match categories.yaml definitions.

    Args:
        pipeline_dir: Directory containing categories.yaml and prompts.yaml

    Returns:
        (valid, warnings) — warnings for mismatches (not errors)
    """
    categories_path = pipeline_dir / "categories.yaml"
    prompts_path = pipeline_dir / "prompts.yaml"

    if not categories_path.exists() or not prompts_path.exists():
        return True, []

    warnings = []

    try:
        categories_data = yaml.safe_load(categories_path.read_text())
        prompts_data = yaml.safe_load(prompts_path.read_text())
    except Exception as e:
        return True, [f"Could not load YAML for cross-validation: {e}"]

    # Extract categories from categories.yaml
    yaml_categories = set(categories_data.get("categories", {}).keys())

    # Extract categories from classification prompt
    # Look for ALL_CAPS words (4+ letters) in classify prompt
    prompts = prompts_data.get("prompts", {})
    classify_prompt = prompts.get("classify", {})

    # Check both system_prompt and template
    prompt_text = ""
    if isinstance(classify_prompt, dict):
        prompt_text = classify_prompt.get("system_prompt", "")
        prompt_text += " " + classify_prompt.get("template", "")
    elif isinstance(classify_prompt, str):
        prompt_text = classify_prompt

    # Pattern: ALL_CAPS words 4+ letters that look like categories
    # Filter out common words like "TRUE", "FALSE", "MUST", "ONLY"
    all_caps_words = set(re.findall(r"\b([A-Z]{4,})\b", prompt_text))
    common_words = {
        "TRUE",
        "FALSE",
        "MUST",
        "ONLY",
        "THAT",
        "THIS",
        "WITH",
        "FROM",
        "EACH",
        "WHEN",
        "THEN",
        "ELSE",
        "JSON",
        "YAML",
        "NULL",
        "NONE",
        "IMPORTANT",
        "NOTE",
    }
    prompt_categories = all_caps_words - common_words

    # Check for mismatches
    in_prompt_not_yaml = prompt_categories - yaml_categories
    in_yaml_not_prompt = yaml_categories - prompt_categories

    # Only warn about likely category names (ignore noise)
    likely_categories = {"FACT", "MECHANISM", "FRAMEWORK", "CAVEAT", "EXCLUSION"}

    if in_prompt_not_yaml & likely_categories:
        warnings.append(
            f"Categories in prompt but not in categories.yaml: "
            f"{in_prompt_not_yaml & likely_categories}"
        )

    if in_yaml_not_prompt:
        warnings.append(
            f"Categories in categories.yaml but not in prompt: {in_yaml_not_prompt}"
        )

    return len(warnings) == 0, warnings


def validate_generation_params_thresholds(
    pipeline_data: dict,
    categories_config_path: Path | None,
) -> list[str]:
    """
    Validate threshold-related generation_parameters in pipeline YAML.

    Checks that decision_mode and category_overrides reference valid policies.

    Args:
        pipeline_data: Parsed pipeline YAML
        categories_config_path: Path to categories.yaml (if present)

    Returns:
        List of errors
    """
    if categories_config_path is None or not categories_config_path.exists():
        return []  # Can't validate without config

    errors = []

    try:
        categories_data = yaml.safe_load(categories_config_path.read_text())
        policies = set(categories_data.get("threshold_policies", {}).keys())
    except Exception:
        return []  # Config loading issue handled elsewhere

    # Check each step's generation_parameters
    steps = pipeline_data.get("steps", [])
    for step in steps:
        gen_params = step.get("generation_parameters", {})
        step_id = step.get("id", step.get("name", "unknown"))

        # Validate decision_mode
        if "decision_mode" in gen_params:
            mode = gen_params["decision_mode"]
            if mode not in policies:
                errors.append(
                    f"Step '{step_id}': decision_mode '{mode}' not in policies. "
                    f"Valid: {policies}"
                )

        # Validate category_overrides
        for cat, policy in gen_params.get("category_overrides", {}).items():
            if policy not in policies:
                errors.append(
                    f"Step '{step_id}': category_overrides[{cat}] = '{policy}' "
                    f"not in policies. Valid: {policies}"
                )

    return errors
