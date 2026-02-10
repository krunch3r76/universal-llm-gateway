"""Prompt namespace registry and prompts.yaml validation."""

from __future__ import annotations

from pathlib import Path


def discover_prompts_files(root_dir: Path) -> list[Path]:
    """
    Find all prompts.yaml files under root_dir.

    If root_dir is a variant directory, also discovers prompts from sibling
    variants (e.g., if validating consensus/v4-analytical/, also loads
    consensus/v3/prompts.yaml to resolve cross-variant prompt_refs).
    """
    prompts_files = set(root_dir.rglob("prompts.yaml"))

    # Check if root_dir is a variant directory and add sibling prompts
    # A variant directory is one where:
    # 1. Parent has other subdirectories with prompts.yaml
    # 2. We're not at the domain level already
    parent = root_dir.parent
    if parent.name not in ("pipelines.local", "pipelines", "."):
        # This might be a variant directory; check siblings
        for sibling in parent.iterdir():
            if sibling.is_dir() and sibling != root_dir:
                sibling_prompts = sibling / "prompts.yaml"
                if sibling_prompts.exists():
                    prompts_files.add(sibling_prompts)

    return sorted(prompts_files)


def find_pipelines_root(path: Path) -> Path | None:
    """
    Find the pipelines root directory (pipelines.local or config/pipelines).

    Searches upward from path to find a known pipelines root pattern.
    Returns None if no pipelines root found.
    """
    # Check if path itself is a pipelines root
    if path.name in ("pipelines.local", "pipelines"):
        return path

    # Search upward
    for parent in path.parents:
        if parent.name in ("pipelines.local", "pipelines"):
            return parent

    return None


def infer_prompt_namespace(prompts_path: Path, root_dir: Path) -> str | None:
    """
    Infer the prompt namespace from file path.

    Pattern: pipelines.local/{domain}/{variant}/prompts.yaml → {domain}.{variant}
    Pattern: pipelines.local/{domain}/prompts.yaml → {domain}

    The namespace always includes the domain, regardless of what root_dir is.
    Hyphens in directory names are preserved (not converted to underscores).

    Returns None if path doesn't match expected pattern.
    """
    # Find the pipelines root to get the full namespace
    pipelines_root = find_pipelines_root(prompts_path)

    if pipelines_root:
        # Use pipelines root as the reference point
        try:
            rel_path = prompts_path.relative_to(pipelines_root)
        except ValueError:
            return None
    else:
        # Fallback: use the provided root_dir
        try:
            rel_path = prompts_path.relative_to(root_dir)
        except ValueError:
            return None

    parts = rel_path.parts[:-1]  # Exclude 'prompts.yaml'

    if not parts:
        return None

    # Join with dots to form namespace
    # Hyphens are preserved (not converted to underscores)
    return ".".join(parts)


def build_prompt_registry(
    root_dir: Path,
) -> tuple[dict[str, set[str]], list[str]]:
    """
    Build a registry of all prompts organized by namespace.

    Returns:
        (namespace_to_prompts, errors)
        namespace_to_prompts: {"consensus.v4-analytical": {"answer_analytical", ...}}
    """
    import yaml

    registry: dict[str, set[str]] = {}
    errors = []

    for prompts_path in discover_prompts_files(root_dir):
        namespace = infer_prompt_namespace(prompts_path, root_dir)
        if namespace is None:
            errors.append(f"Cannot infer namespace for {prompts_path}")
            continue

        try:
            data = yaml.safe_load(prompts_path.read_text()) or {}
        except yaml.YAMLError as e:
            errors.append(f"YAML error in {prompts_path}: {e}")
            continue

        prompts = data.get("prompts", {})
        if not isinstance(prompts, dict):
            errors.append(f"{prompts_path}: 'prompts' must be a dict")
            continue

        registry[namespace] = set(prompts.keys())

    return (registry, errors)


def validate_prompt_ref(
    prompt_ref: str,
    registry: dict[str, set[str]],
) -> str | None:
    """
    Validate a prompt_ref exists in the registry.

    prompt_ref format: {namespace}.{prompt_name}
    Example: consensus.v4-analytical.answer_analytical

    Returns error message if invalid, None if valid.
    """
    parts = prompt_ref.rsplit(".", 1)
    if len(parts) != 2:
        return f"Invalid prompt_ref format: '{prompt_ref}' (expected namespace.prompt_name)"

    namespace, prompt_name = parts

    if namespace not in registry:
        available = sorted(registry.keys())
        return (
            f"Unknown namespace '{namespace}' in prompt_ref '{prompt_ref}'. "
            f"Available: {available}"
        )

    if prompt_name not in registry[namespace]:
        available = sorted(registry[namespace])
        return (
            f"Unknown prompt '{prompt_name}' in namespace '{namespace}'. "
            f"Available: {available}"
        )

    return None


def validate_prompts_file(yaml_path: Path) -> tuple[bool, list[str]]:
    """
    Validate a prompts.yaml configuration file.

    Returns:
        (is_valid, errors) tuple
    """
    import yaml

    errors = []

    try:
        with yaml_path.open() as f:
            data = yaml.safe_load(f) or {}

        # Check 1: Must have 'prompts:' top-level key
        if "prompts" not in data:
            errors.append(
                "Missing 'prompts:' top-level key. "
                "All prompts must be under 'prompts:' wrapper"
            )
            return (False, errors)

        prompts = data["prompts"]
        if not isinstance(prompts, dict):
            errors.append("'prompts:' must be a dictionary")
            return (False, errors)

        # Check 2: Validate each prompt
        for prompt_name, prompt_config in prompts.items():
            if not isinstance(prompt_config, dict):
                errors.append(
                    f"Prompt '{prompt_name}': Must be a dictionary, "
                    f"got {type(prompt_config).__name__}"
                )
                continue

            # Check required 'template' field
            if "template" not in prompt_config:
                errors.append(
                    f"Prompt '{prompt_name}': Missing required 'template' field"
                )

            # Check for common mistake: 'system' instead of 'system_prompt'
            if "system" in prompt_config:
                errors.append(
                    f"Prompt '{prompt_name}': Found 'system' field. "
                    f"Did you mean 'system_prompt'? "
                    f"Valid fields: template, system_prompt, description, "
                    f"json_schema, generation_parameters"
                )

            # Validate allowed fields
            allowed_fields = {
                "template",
                "system_prompt",
                "description",
                "json_schema",
                "generation_parameters",
            }
            unknown_fields = set(prompt_config.keys()) - allowed_fields
            if unknown_fields:
                errors.append(
                    f"Prompt '{prompt_name}': Unknown fields: {unknown_fields}. "
                    f"Allowed: {allowed_fields}"
                )

            # Validate json_schema if present
            if "json_schema" in prompt_config:
                if not isinstance(prompt_config["json_schema"], dict):
                    errors.append(
                        f"Prompt '{prompt_name}': 'json_schema' must be a dict"
                    )

            # Validate generation_parameters if present
            if "generation_parameters" in prompt_config:
                if not isinstance(prompt_config["generation_parameters"], dict):
                    errors.append(
                        f"Prompt '{prompt_name}': 'generation_parameters' "
                        f"must be a dict"
                    )

        return (len(errors) == 0, errors)

    except Exception as e:
        import yaml as yaml_module

        if isinstance(e, yaml_module.YAMLError):
            return (False, [f"YAML parsing error: {e}"])
        return (False, [f"Unexpected error: {e}"])
