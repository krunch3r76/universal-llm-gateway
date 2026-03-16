"""
Parse-time validation for pipeline configuration.

Validates namespace usage, binding references, step dependencies,
type compatibility, and reads_from declarations.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .schemas import PipelineSpec, StepConfig

logger = logging.getLogger(__name__)


class PipelineValidator:
    """Validates pipeline configuration at parse time."""

    RESERVED_NAMESPACES = frozenset({"sourceNs", "optionsNs", "loopNs", "mapNs"})

    def validate(self, pipeline: PipelineSpec) -> list[str]:
        """
        Run all validations.

        Returns list of error messages (empty if valid).
        """
        errors = []
        errors.extend(self.validate_namespace_usage(pipeline))
        errors.extend(self.validate_step_order(pipeline))
        errors.extend(self.validate_bindings(pipeline))
        errors.extend(self.validate_circular_dependencies(pipeline))
        errors.extend(self.validate_map_steps(pipeline))
        errors.extend(self.validate_type_compatibility(pipeline))
        errors.extend(self.validate_reads_from(pipeline))
        return errors

    def validate_namespace_usage(self, pipeline: PipelineSpec) -> list[str]:
        """Validate reserved namespaces used in appropriate contexts."""
        errors = []

        for step in pipeline.steps:
            for field_name, binding in step.handler_inputs.items():
                if binding.namespace == "loopNs":
                    if not self._is_inside_loop_body(step, pipeline):
                        errors.append(
                            f"Step '{step.name}': handler_inputs field "
                            f"'{field_name}' uses loopNs namespace but step "
                            f"is not inside a loop body"
                        )

                if binding.namespace == "mapNs":
                    if not self._is_map_step(step):
                        errors.append(
                            f"Step '{step.name}': handler_inputs field "
                            f"'{field_name}' uses mapNs namespace but step "
                            f"is not a map step"
                        )

        return errors

    def validate_step_order(self, pipeline: PipelineSpec) -> list[str]:
        """Validate step references exist AND appear before the referencing step.

        Subsumes the former validate_step_references (existence-only) check —
        ordering is strictly stronger.
        """
        errors = []
        seen_steps: set[str] = set()

        for step in pipeline.steps:
            for field_name, binding in step.handler_inputs.items():
                if binding.namespace == "step" and binding.step_name:
                    if binding.step_name not in seen_steps:
                        if any(s.name == binding.step_name for s in pipeline.steps):
                            errors.append(
                                f"Step '{step.name}': handler_inputs field "
                                f"'{field_name}' references step "
                                f"'{binding.step_name}' which appears later "
                                f"in the pipeline (must be defined before use)"
                            )
                        else:
                            errors.append(
                                f"Step '{step.name}': handler_inputs field "
                                f"'{field_name}' references unknown step "
                                f"'{binding.step_name}'"
                            )

            for rf in step.reads_from:
                if rf.step not in seen_steps:
                    if any(s.name == rf.step for s in pipeline.steps):
                        errors.append(
                            f"Step '{step.name}': reads_from references "
                            f"step '{rf.step}' which appears later in the "
                            f"pipeline (must be defined before use)"
                        )
                    else:
                        errors.append(
                            f"Step '{step.name}': reads_from references "
                            f"unknown step '{rf.step}'"
                        )

            seen_steps.add(step.name)

        return errors

    def validate_type_compatibility(self, pipeline: PipelineSpec) -> list[str]:
        """Cross-reference declared input types against output declarations.

        ∀ binding b with declared_type ≠ None:
            referenced step has output_declarations → match type
            no output_declarations → warn (legacy compat)

        Warnings only — does not produce errors for legacy pipelines.
        """
        warnings: list[str] = []
        binding_types_by_step: dict[str, dict[str, tuple[str, str]]] = {}

        for step in pipeline.steps:
            if step.output_declarations:
                for name, decl in step.output_declarations.items():
                    binding_types_by_step.setdefault(step.name, {})[decl.binding] = (
                        name,
                        decl.declared_type,
                    )

        for step in pipeline.steps:
            for field_name, binding in step.handler_inputs.items():
                if binding.declared_type is None:
                    continue
                if binding.namespace != "step" or not binding.step_name:
                    continue

                upstream_bindings = binding_types_by_step.get(
                    binding.step_name,
                )
                if upstream_bindings is None:
                    logger.debug(
                        "Step '%s' input '%s': typed reference to '%s'"
                        " but upstream has no output_declarations",
                        step.name,
                        field_name,
                        binding.step_name,
                    )
                    continue

                matched = self._find_matching_output(
                    binding.field_path,
                    upstream_bindings,
                )
                if matched is None:
                    continue

                decl_name, upstream_type = matched
                if upstream_type != "any" and binding.declared_type != "any":
                    if upstream_type != binding.declared_type:
                        warnings.append(
                            f"Step '{step.name}': input '{field_name}' "
                            f"declares type '{binding.declared_type}' "
                            f"but step '{binding.step_name}' output "
                            f"'{decl_name}' declares "
                            f"type '{upstream_type}'"
                        )

        return warnings

    @staticmethod
    def _find_matching_output(
        field_path: str,
        upstream_bindings: dict[str, tuple[str, str]],
    ) -> tuple[str, str] | None:
        """Match an input field_path against upstream output bindings.

        Handles exact match ("json" → "json") and prefix match
        ("json.field" → "json").
        """
        if field_path in upstream_bindings:
            return upstream_bindings[field_path]

        for binding_path, (name, dtype) in upstream_bindings.items():
            if field_path.startswith(binding_path + "."):
                return (name, dtype)
            if binding_path.startswith(field_path + "."):
                return (name, dtype)

        return None

    def validate_reads_from(self, pipeline: PipelineSpec) -> list[str]:
        """Validate reads_from declarations for ordering and internal consistency."""
        errors = []
        declared_steps = {step.name for step in pipeline.steps}
        seen_steps: set[str] = set()
        seen_declarations: set[tuple[str, str, tuple[str, ...]]] = set()

        for step in pipeline.steps:
            for rf in step.reads_from:
                if rf.step not in seen_steps:
                    if rf.step in declared_steps:
                        errors.append(
                            f"Step '{step.name}': reads_from references step "
                            f"'{rf.step}' which appears later in the pipeline "
                            "(must be defined before use)"
                        )
                    else:
                        errors.append(
                            f"Step '{step.name}': reads_from references unknown "
                            f"step '{rf.step}'"
                        )
                if rf.step == step.name:
                    errors.append(
                        f"Step '{step.name}': reads_from cannot reference itself"
                    )
                if not rf.fields:
                    errors.append(
                        f"Step '{step.name}': reads_from for '{rf.step}' "
                        "must declare at least one field"
                    )
                    continue
                key = (step.name, rf.step, tuple(rf.fields))
                if key in seen_declarations:
                    errors.append(
                        f"Step '{step.name}': duplicate reads_from declaration "
                        f"for step '{rf.step}' fields={list(rf.fields)}"
                    )
                else:
                    seen_declarations.add(key)
            seen_steps.add(step.name)

        return errors

    def _is_inside_loop_body(
        self,
        step: StepConfig,
        pipeline: PipelineSpec,
    ) -> bool:
        """Check whether a step executes inside a loop body."""
        return False

    def _is_map_step(self, step: StepConfig) -> bool:
        """Check whether a step uses map execution mode."""
        return hasattr(step, "map_config") and step.map_config is not None

    def validate_bindings(self, pipeline: PipelineSpec) -> list[str]:
        """Validate binding structure and content."""
        errors = []

        for step in pipeline.steps:
            for field_name, binding in step.handler_inputs.items():
                if not binding.field_path:
                    errors.append(
                        f"Step '{step.name}': handler_inputs field "
                        f"'{field_name}' has empty field_path"
                    )

                if ".." in binding.field_path or binding.field_path.startswith("."):
                    errors.append(
                        f"Step '{step.name}': handler_inputs field "
                        f"'{field_name}' has invalid field_path "
                        f"'{binding.field_path}'"
                    )

            for field_name, output_binding in step.handler_outputs.items():
                if not output_binding.binding.field_path:
                    errors.append(
                        f"Step '{step.name}': handler_outputs field "
                        f"'{field_name}' has empty field_path"
                    )

        return errors

    def validate_circular_dependencies(self, pipeline: PipelineSpec) -> list[str]:
        """Detect circular dependencies in step references."""
        errors = []

        dependencies: dict[str, list[str]] = {}
        for step in pipeline.steps:
            dependencies[step.name] = step.depends_on

        for step in pipeline.steps:
            visited: set[str] = set()
            path: list[str] = []
            if self._has_circular_dependency(step.name, dependencies, visited, path):
                cycle = " → ".join(path + [step.name])
                errors.append(f"Circular dependency detected: {cycle}")

        return errors

    def _has_circular_dependency(
        self,
        step_name: str,
        dependencies: dict[str, list[str]],
        visited: set[str],
        path: list[str],
    ) -> bool:
        """DFS to detect circular dependencies."""
        if step_name in path:
            return True

        if step_name in visited:
            return False

        visited.add(step_name)
        path.append(step_name)

        for dep in dependencies.get(step_name, []):
            if self._has_circular_dependency(dep, dependencies, visited, path):
                return True

        path.pop()
        return False

    def validate_map_steps(self, pipeline: PipelineSpec) -> list[str]:
        """Validate map step configuration."""
        errors = []

        for step in pipeline.steps:
            if not step.is_map_step:
                continue

            try:
                map_config = step.get_map_config()
            except (ValueError, TypeError) as e:
                errors.append(f"Step '{step.name}': invalid map_config: {e}")
                continue

            if not map_config:
                errors.append(
                    f"Step '{step.name}': is_map_step=True but get_map_config() "
                    f"returned None"
                )
                continue

            for field, binding in map_config.map_over.items():
                if binding.namespace == "mapNs":
                    errors.append(
                        f"Step '{step.name}': map_over field '{field}' cannot "
                        f"reference mapNs (not available outside map body)"
                    )

            for field, binding in map_config.map_inputs.items():
                has_mapns_reference = (
                    binding.namespace == "mapNs" or "[mapNs." in binding.field_path
                )
                if not has_mapns_reference:
                    errors.append(
                        f"Step '{step.name}': map_inputs field '{field}' should "
                        f"reference mapNs namespace (got: "
                        f"{binding.namespace}.{binding.field_path}). "
                        f"Expected direct reference (mapNs.*) or "
                        f"dynamic key syntax (*.mapping[mapNs.*])"
                    )

            for field, binding in step.handler_inputs.items():
                if "*" in binding.field_path:
                    if binding.step_name:
                        ref_step = next(
                            (s for s in pipeline.steps if s.name == binding.step_name),
                            None,
                        )
                        if ref_step and not ref_step.is_map_step:
                            errors.append(
                                f"Step '{step.name}': field '{field}' uses wildcard "
                                f"but '{binding.step_name}' is not a map step"
                            )

        return errors
