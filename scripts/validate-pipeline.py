#!/usr/bin/env python3
"""
Validate v6 pipeline YAML files and configuration files (prompts.yaml, models.yaml).

Supports both flat and variant directory structures:
    pipelines.local/{domain}/handlers/            # Shared handlers
    pipelines.local/{domain}/{variant}/handlers/  # Variant-specific handlers
    pipelines.local/{domain}/{variant}/prompts.yaml
    pipelines.local/{domain}/{variant}/{pipeline}.yaml

Usage:
    validate-pipeline.py <yaml_file>           # Validate single file
    validate-pipeline.py <directory>           # Validate all YAMLs in directory
    validate-pipeline.py pipelines.local/      # Common usage

What gets validated:
    - Pipeline files (*.yaml except prompts.yaml and models.yaml):
      * Schema version, step structure, dependencies, generation parameters
      * prompt_ref values exist in loaded prompts (namespace-aware)
      * step.type values have registered handlers
    - prompts.yaml files:
      * Proper 'prompts:' wrapper, required 'template' field
      * Catches common mistakes like 'system' instead of 'system_prompt'
    - models.yaml files:
      * Proper 'models:' wrapper, required 'model' field
    - Handler packages:
      * __init__.py MUST have register_handlers() function if present
      * Import errors are FATAL

Exit codes:
    0 - All files valid
    1 - Validation errors found
    2 - Script error (file not found, import error, etc.)

Examples:
    # Validate single pipeline
    ./scripts/validate-pipeline.py pipelines.local/consensus/v4-analytical/basic.yaml

    # Validate all files in a directory
    ./scripts/validate-pipeline.py pipelines.local/consensus/

    # Validate all local pipelines and configs
    ./scripts/validate-pipeline.py pipelines.local/
"""

from validate_pipeline import main

if __name__ == "__main__":
    main()
