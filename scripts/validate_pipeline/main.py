"""Main entry point for pipeline validation."""

from __future__ import annotations

import sys
from pathlib import Path

from .categories import (
    validate_categories_prompt_alignment,
    validate_categories_yaml,
    validate_generation_params_thresholds,
)
from .handlers import validate_all_handler_packages
from .models import validate_models_file
from .pipeline import validate_file
from .prompts import build_prompt_registry, validate_prompts_file

USAGE = """
Validate v6 pipeline YAML files and configuration files (prompts.yaml, models.yaml).

Supports both flat and variant directory structures:
    pipelines.local/{domain}/handlers/            # Shared handlers
    pipelines.local/{domain}/{variant}/handlers/  # Variant-specific handlers
    pipelines.local/{domain}/{variant}/prompts.yaml
    pipelines.local/{domain}/{variant}/{pipeline}.yaml

Usage:
    validate-pipeline.py <yaml_file>           # Validate single file
    validate-pipeline.py <directory>           # Validate all YAMLs in directory
    pipelines.local/                           # Common usage

What gets validated:
    - Pipeline files (*.yaml except prompts.yaml and models.yaml):
      * Schema version, step structure, dependencies, generation parameters
      * prompt_ref values exist in loaded prompts (namespace-aware)
      * step.type values have registered handlers
    - prompts.yaml files:
      * Proper 'prompts:' wrapper, required 'template' field
    - models.yaml files:
      * Proper 'models:' wrapper, required 'model' field
    - Handler packages:
      * __init__.py MUST have register_handlers() function if present
      * Import errors are FATAL

Exit codes:
    0 - All files valid
    1 - Validation errors found
    2 - Script error (file not found, import error, etc.)
"""


def main() -> None:
    """Main entry point."""
    # Ensure running from project root
    if not Path("services").exists():
        print("Error: Must run from project root", file=sys.stderr)
        sys.exit(2)

    if len(sys.argv) < 2:
        print(USAGE)
        sys.exit(2)

    path = Path(sys.argv[1])

    # Check path exists
    if not path.exists():
        print(f"Error: Path not found: {path}", file=sys.stderr)
        sys.exit(2)

    # Phase 1: Validate handler packages (FATAL errors)
    print("Validating handler packages...")
    handlers_valid, handler_errors, registered_step_types = (
        validate_all_handler_packages(path)
    )

    if handler_errors:
        for error in handler_errors:
            print(f"  ✗ {error}")

    if not handlers_valid:
        print("\n✗ FATAL: Handler package validation failed")
        print("  Policy: __init__.py present but no register_handlers() → FATAL")
        sys.exit(1)

    if registered_step_types:
        print(f"  ✓ Found {len(registered_step_types)} registered step types")

    # Phase 2: Build prompt registry
    print("\nBuilding prompt registry...")
    prompt_registry, registry_errors = build_prompt_registry(path)

    for error in registry_errors:
        print(f"  ⚠ {error}")

    if prompt_registry:
        total_prompts = sum(len(p) for p in prompt_registry.values())
        print(
            f"  ✓ Loaded {total_prompts} prompts from {len(prompt_registry)} namespace(s)"
        )
        for ns in sorted(prompt_registry.keys()):
            print(f"    - {ns}: {len(prompt_registry[ns])} prompts")

    # Phase 3: Collect and validate YAML files
    print("\nValidating YAML files...")
    passed, total = _validate_yaml_files(
        path,
        prompt_registry if prompt_registry else None,
        registered_step_types if registered_step_types else None,
    )

    # Final result
    if passed == total and handlers_valid:
        print("\n✓ All files valid")
        sys.exit(0)
    else:
        print(f"\n✗ {total - passed} file(s) have errors")
        print(
            "\nSee: services/universal-stargate/systems/pipeline/"
            "README.md#v6-schema-specification"
        )
        sys.exit(1)


def _validate_yaml_files(
    path: Path,
    prompt_registry: dict[str, set[str]] | None,
    registered_step_types: set[str] | None,
) -> tuple[int, int]:
    """Validate all YAML files and return (passed, total) counts."""
    # Collect YAML files
    if path.is_file():
        if path.suffix not in (".yaml", ".yml"):
            print(f"Error: Not a YAML file: {path}", file=sys.stderr)
            sys.exit(2)
        yaml_files = [path]
    else:
        all_yaml = sorted(path.rglob("*.yaml")) + sorted(path.rglob("*.yml"))
        yaml_files = all_yaml
        if not yaml_files:
            print(f"Error: No YAML files found in: {path}", file=sys.stderr)
            sys.exit(2)

    # Config files co-located with pipelines that are not pipeline YAMLs
    _SKIP_FILENAMES: set[str] = {"retrieval-profiles.yaml"}

    # Separate into pipelines and config files
    pipeline_files = []
    prompts_files = []
    models_files = []
    categories_files = []
    skipped_files = []

    for yaml_file in yaml_files:
        if yaml_file.name == "prompts.yaml":
            prompts_files.append(yaml_file)
        elif yaml_file.name == "models.yaml":
            models_files.append(yaml_file)
        elif yaml_file.name == "categories.yaml":
            categories_files.append(yaml_file)
        elif yaml_file.name in _SKIP_FILENAMES:
            skipped_files.append(yaml_file)
        else:
            pipeline_files.append(yaml_file)

    total = len(yaml_files) - len(skipped_files)
    passed = 0

    for yaml_file in skipped_files:
        print(f"✓ {_rel_path(yaml_file)} [config — skipped]")

    print(f"\nValidating {total} file(s)...\n")

    # Phase 2.5: Validate categories.yaml files (if present)
    print("\nValidating categories configuration...")
    for categories_file in categories_files:
        # Schema validation
        valid, errors = validate_categories_yaml(categories_file.parent)

        rel_path = _rel_path(categories_file)
        if valid:
            print(f"✓ {rel_path} [categories config]")
            passed += 1
        else:
            print(f"✗ {rel_path} [categories config]")
            for error in errors:
                print(f"  │ {error}")
            print()

        # Cross-validation with prompts (warnings only)
        _, warnings = validate_categories_prompt_alignment(categories_file.parent)
        for warning in warnings:
            print(f"  ⚠ {warning}")

    # Validate pipeline files
    for yaml_file in pipeline_files:
        valid, errors = validate_file(
            yaml_file,
            prompt_registry=prompt_registry,
            registered_step_types=registered_step_types,
        )

        # Validate generation_params threshold references if categories.yaml exists
        categories_config_path = yaml_file.parent / "categories.yaml"
        if categories_config_path.exists():
            import yaml

            try:
                with yaml_file.open() as f:
                    pipeline_data = yaml.safe_load(f) or {}
                pipeline_data = pipeline_data.get("pipeline", pipeline_data)
                threshold_errors = validate_generation_params_thresholds(
                    pipeline_data, categories_config_path
                )
                errors.extend(threshold_errors)
                valid = valid and len(threshold_errors) == 0
            except yaml.YAMLError:
                pass  # YAML loading errors reported by validate_file
            except Exception as e:
                print(
                    f"  ⚠ Unexpected error during generation_params threshold validation for {_rel_path(yaml_file)}: {e}",
                    file=sys.stderr,
                )

        rel_path = _rel_path(yaml_file)
        if valid:
            print(f"✓ {rel_path} [pipeline]")
            passed += 1
        else:
            print(f"✗ {rel_path} [pipeline]")
            for error in errors:
                print(f"  │ {error}")
            print()

    # Validate prompts.yaml files
    for yaml_file in prompts_files:
        valid, errors = validate_prompts_file(yaml_file)

        rel_path = _rel_path(yaml_file)
        if valid:
            print(f"✓ {rel_path} [prompts config]")
            passed += 1
        else:
            print(f"✗ {rel_path} [prompts config]")
            for error in errors:
                print(f"  │ {error}")
            print()

    # Validate models.yaml files
    for yaml_file in models_files:
        valid, errors = validate_models_file(yaml_file)

        rel_path = _rel_path(yaml_file)
        if valid:
            print(f"✓ {rel_path} [models config]")
            passed += 1
        else:
            print(f"✗ {rel_path} [models config]")
            for error in errors:
                print(f"  │ {error}")
            print()

    # Summary
    print(f"\n{'─' * 50}")
    print(f"Results: {passed}/{total} passed")
    print(f"  - {len(pipeline_files)} pipeline(s)")
    print(f"  - {len(prompts_files)} prompts config(s)")
    print(f"  - {len(models_files)} models config(s)")
    if categories_files:
        print(f"  - {len(categories_files)} categories config(s)")
    if skipped_files:
        print(f"  - {len(skipped_files)} config file(s) skipped")
    if registered_step_types:
        print(f"  - {len(registered_step_types)} registered handler step types")

    return (passed, total)


def _rel_path(yaml_file: Path) -> Path:
    """Get relative path for display."""
    return (
        yaml_file.relative_to(Path.cwd())
        if yaml_file.is_relative_to(Path.cwd())
        else yaml_file
    )


if __name__ == "__main__":
    main()
