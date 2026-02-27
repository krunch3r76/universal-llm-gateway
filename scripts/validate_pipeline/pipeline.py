"""Pipeline YAML validation."""

from __future__ import annotations

import sys
from pathlib import Path

from .prompts import validate_prompt_ref


def validate_file(
    yaml_path: Path,
    prompt_registry: dict[str, set[str]] | None = None,
    registered_step_types: set[str] | None = None,
) -> tuple[bool, list[str]]:
    """
    Validate a single pipeline YAML file.

    Args:
        yaml_path: Path to pipeline YAML
        prompt_registry: Optional namespace→prompts map for prompt_ref validation
        registered_step_types: Optional set of registered handler step types

    Returns:
        (is_valid, errors) tuple
    """
    errors = []

    try:
        import yaml

        # Add stargate to path for imports
        stargate_path = Path("services/universal-stargate")
        if stargate_path not in [Path(p) for p in sys.path]:
            sys.path.insert(0, str(stargate_path))

        from systems.pipeline.core.schemas import PipelineSpec
        from systems.pipeline.core.validation import PipelineValidator

        # Load and parse YAML
        with yaml_path.open() as f:
            data = yaml.safe_load(f) or {}

        pipeline_data = data.get("pipeline", data)

        if not pipeline_data.get("id"):
            errors.append("Missing 'id' field in pipeline YAML")
            return (False, errors)

        # Parse pipeline using Pydantic (validates schema structure)
        try:
            pipeline = PipelineSpec(**pipeline_data)
        except Exception as e:
            errors.append(f"Failed to parse pipeline YAML: {e}")
            return (False, errors)

        # Run ALL existing validators (comprehensive check)
        validator = PipelineValidator()
        errors.extend(validator.validate(pipeline))

        # v6-specific validation
        errors.extend(_validate_schema_version(pipeline))
        errors.extend(_validate_no_depends_on(pipeline))
        errors.extend(_validate_generation_params(pipeline))
        errors.extend(_validate_handler_inputs(pipeline, validator))
        errors.extend(_validate_handler_outputs(pipeline))
        errors.extend(_validate_output_input_consistency(pipeline))

        # Prompt and step type validation
        if prompt_registry:
            errors.extend(_validate_prompt_refs(pipeline, prompt_registry))
        if registered_step_types is not None:
            errors.extend(_validate_step_types(pipeline, registered_step_types))

        return (len(errors) == 0, errors)

    except ImportError as e:
        return (False, [f"Import error (run from project root with venv active): {e}"])
    except Exception as e:
        return (False, [f"Unexpected error: {e}"])


def _validate_schema_version(pipeline) -> list[str]:
    """Check schema_version is 6."""
    errors = []
    if pipeline.model_extra:
        schema_version = pipeline.model_extra.get("schema_version")
    else:
        schema_version = None
    if schema_version != 6:
        errors.append(
            f"Expected schema_version: 6, got {schema_version}. "
            f"See: services/universal-stargate/systems/pipeline/"
            f"README.md#v6-schema-specification"
        )
    return errors


def _validate_no_depends_on(pipeline) -> list[str]:
    """Check no depends_on usage (computed from handler_inputs in v6)."""
    errors = []
    for step in pipeline.steps:
        if step.depends_on:
            errors.append(
                f"Step '{step.name}': v6 doesn't use 'depends_on' "
                f"(computed automatically from handler_inputs)"
            )
    return errors


def _validate_generation_params(pipeline) -> list[str]:
    """Validate generation parameters."""
    errors = []

    # Check flat generation params not allowed
    for step in pipeline.steps:
        deprecated_fields = []
        if hasattr(step, "temperature") and step.temperature is not None:
            deprecated_fields.append("temperature")
        if hasattr(step, "max_tokens") and step.max_tokens is not None:
            deprecated_fields.append("max_tokens")
        if hasattr(step, "response_format") and step.response_format is not None:
            deprecated_fields.append("response_format")

        if deprecated_fields:
            errors.append(
                f"Step '{step.name}': Flat generation params not supported. "
                f"Found: {', '.join(deprecated_fields)}. "
                f"Use 'generation_parameters' dict instead."
            )

    # Check generation_parameters contains only allowed params
    allowed_params = {
        # OpenAI generation params
        "temperature",
        "max_tokens",
        "top_p",
        "top_k",
        "stop",
        "response_format",
        "seed",
        "stream",
        "presence_penalty",
        "frequency_penalty",
        # Handler-specific params
        "similarity_threshold",
        "embedding_model",
        "model_ref",
        "batch_size",
        "parallel",
        # Consensus filter handler params (Phase 2)
        "decision_mode",
        "category_overrides",
    }

    for step in pipeline.steps:
        if step.generation_parameters:
            unsupported = set(step.generation_parameters.keys()) - allowed_params
            if unsupported:
                errors.append(
                    f"Step '{step.name}': Unsupported generation "
                    f"parameters: {unsupported}. Allowed: {allowed_params}"
                )

    return errors


def _validate_handler_inputs(pipeline, validator) -> list[str]:
    """Validate handler_inputs have valid namespaces."""
    errors = []
    step_names = {s.name for s in pipeline.steps}

    for step in pipeline.steps:
        for field_name, binding in step.handler_inputs.items():
            if binding.namespace in validator.RESERVED_NAMESPACES:
                continue
            elif binding.namespace == "step":
                if binding.step_name not in step_names:
                    errors.append(
                        f"Step '{step.name}', input '{field_name}': "
                        f"References unknown step '{binding.step_name}'"
                    )
            else:
                errors.append(
                    f"Step '{step.name}', input '{field_name}': "
                    f"Unknown namespace '{binding.namespace}'. "
                    f"Expected: sourceNs, optionsNs, loopNs, mapNs, or step reference"
                )

    return errors


def _validate_handler_outputs(pipeline) -> list[str]:
    """Validate handler_outputs bindings."""
    errors = []
    for step in pipeline.steps:
        for field_name, output_binding in step.handler_outputs.items():
            if not output_binding.binding.field_path:
                errors.append(
                    f"Step '{step.name}', output '{field_name}': "
                    f"Empty field_path (must specify where to store output)"
                )
    return errors


def _validate_prompt_refs(
    pipeline,
    prompt_registry: dict[str, set[str]],
) -> list[str]:
    """Validate prompt_ref values exist in prompt registry."""
    errors = []
    for step in pipeline.steps:
        prompt_ref = getattr(step, "prompt_ref", None)
        if prompt_ref:
            error = validate_prompt_ref(prompt_ref, prompt_registry)
            if error:
                errors.append(f"Step '{step.name}': {error}")
    return errors


def _validate_step_types(
    pipeline,
    registered_step_types: set[str],
) -> list[str]:
    """Validate step.type has registered handler."""
    errors = []
    builtin_types = {"generate", "map", "loop", "conditional", "pipeline_call_v1"}
    all_known_types = builtin_types | registered_step_types

    for step in pipeline.steps:
        step_type = step.type
        if step_type and step_type not in all_known_types:
            errors.append(
                f"Step '{step.name}': Unknown step type '{step_type}'. "
                f"Registered: {sorted(all_known_types)}"
            )

    return errors


def _validate_output_input_consistency(pipeline) -> list[str]:
    """
    Validate that handler_inputs reference valid handler_outputs from upstream steps.

    This catches mismatches where:
    - A step declares output to path X but downstream step reads from path Y
    - A handler outputs "json.statements" but config declares "json.candidates"

    Example bug this catches:
        filter_supporting handler outputs to "json.statements"
        but handler_outputs declares "json.candidates"
        → partition step fails to resolve "filter_supporting.json.candidates"

    Invariant: ∀ step A input referencing step B:
        A.handler_inputs[field].field_path ∈ B.declared_outputs

    Note: This validation is conservative - it may miss some valid cases where
    handlers return dynamic JSON structures. False positives are better than
    missing real bugs.
    """
    errors = []

    # Build map of step_name → (set of declared output paths, step_type)
    # Format: "json.field" or "raw" or just "field"
    declared_outputs: dict[str, tuple[set[str], str]] = {}

    for step in pipeline.steps:
        outputs = set()
        for field_name, output_binding in step.handler_outputs.items():
            # Extract the declared output path
            # output_binding.binding.field_path is like "json.statements" or "raw"
            path = output_binding.binding.field_path
            if path:
                outputs.add(path)

        declared_outputs[step.name] = (outputs, step.type)

    # Now check each step's inputs against upstream outputs
    for step in pipeline.steps:
        for field_name, input_binding in step.handler_inputs.items():
            # Only check step namespace (cross-step references)
            if input_binding.namespace != "step":
                continue

            upstream_step = input_binding.step_name
            input_path = input_binding.field_path

            # Skip if upstream step not found (caught by _validate_handler_inputs)
            if upstream_step not in declared_outputs:
                continue

            # Check if the input path matches any declared output
            upstream_outputs, upstream_type = declared_outputs[upstream_step]

            # Path matching logic:
            # - input_path = "json.candidates" should match output "json.candidates"
            # - input_path = "raw" should match output "raw"
            # - Wildcards: output "*.json.claims" matches any keyed access like "qwen.json.claims"
            # - Generate steps: If output declares "json.*", any "json.*" access is valid
            #   (JSON response can have multiple fields not all explicitly declared)
            # - Custom handlers: Strict match - "json.statements" ≠ "json.candidates"

            if not upstream_outputs:
                # No outputs declared for upstream step - cannot validate
                # This might be okay for steps that implicitly output to raw/json
                continue

            # Check for exact match or prefix match
            path_matches = False
            for output_path in upstream_outputs:
                # Exact match
                if input_path == output_path:
                    path_matches = True
                    break

                # JSON object subpath access: output "json" allows "json.field"
                # BUT: only allow if upstream is a generate step (dynamic JSON)
                # Custom handlers must declare exact output paths
                if (
                    upstream_type == "generate"
                    and output_path == "json"
                    and input_path.startswith("json.")
                ):
                    path_matches = True
                    break

                # Wildcard match: "*.json.claims" covers "{key}.json.claims"
                # Extract the key from input path and check pattern
                if output_path.startswith("*."):
                    # output: "*.json.claims"
                    # input: "qwen.json.claims" or "phi.json.claims"
                    pattern_suffix = output_path[2:]  # Remove "*."
                    if "." in input_path:
                        # Split input to get "qwen" and "json.claims"
                        input_suffix = ".".join(input_path.split(".")[1:])
                        if input_suffix == pattern_suffix:
                            path_matches = True
                            break

                # Sibling field match: ONLY for generate steps
                # Allow accessing sibling keys: output "json.rewritten" allows "json.response_hint"
                # Strategy: if output is "json.X" and input is "json.Y", they're siblings
                # But ONLY allow this for generate steps (dynamic JSON responses)
                if (
                    upstream_type == "generate"
                    and "." in output_path
                    and "." in input_path
                ):
                    output_parts = output_path.split(".", 1)
                    input_parts = input_path.split(".", 1)
                    # Same parent namespace (both "json")
                    if output_parts[0] == input_parts[0]:
                        path_matches = True
                        break

                # Parent access: input "json" covers any "json.X" output
                if output_path.startswith(input_path + "."):
                    path_matches = True
                    break

            if not path_matches:
                errors.append(
                    f"Step '{step.name}', input '{field_name}': "
                    f"References '{upstream_step}.{input_path}' but step "
                    f"'{upstream_step}' declares outputs: {sorted(upstream_outputs)}. "
                    f"Possible mismatch between handler output and handler_outputs config."
                )

    return errors
