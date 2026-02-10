"""
Parse-time validation for pipeline configuration.

Validates namespace usage, binding references, step dependencies.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .schemas import PipelineConfig, StepConfig


class PipelineValidator:
    """Validates pipeline configuration at parse time."""

    RESERVED_NAMESPACES = frozenset({"sourceNs", "optionsNs", "loopNs", "mapNs"})

    def validate(self, pipeline: "PipelineConfig") -> list[str]:
        """
        Run all validations.

        Returns list of error messages (empty if valid).
        """
        errors = []
        errors.extend(self.validate_namespace_usage(pipeline))
        errors.extend(self.validate_step_references(pipeline))
        errors.extend(self.validate_bindings(pipeline))
        errors.extend(self.validate_circular_dependencies(pipeline))
        errors.extend(self.validate_map_steps(pipeline))
        return errors

    def validate_namespace_usage(self, pipeline: "PipelineConfig") -> list[str]:
        """Validate reserved namespaces used in appropriate contexts."""
        errors = []

        for step in pipeline.steps:
            for field_name, binding in step.handler_inputs.items():
                # loopNs only valid inside loop body (Phase 4)
                if binding.namespace == "loopNs":
                    if not self._is_inside_loop_body(step, pipeline):
                        errors.append(
                            f"Step '{step.name}': handler_inputs field "
                            f"'{field_name}' uses loopNs namespace but step "
                            f"is not inside a loop body"
                        )

                # mapNs only valid inside map step (Phase 4)
                if binding.namespace == "mapNs":
                    if not self._is_map_step(step):
                        errors.append(
                            f"Step '{step.name}': handler_inputs field "
                            f"'{field_name}' uses mapNs namespace but step "
                            f"is not a map step"
                        )

        return errors

    def validate_step_references(self, pipeline: "PipelineConfig") -> list[str]:
        """Validate step references point to existing steps."""
        errors = []
        step_names = {s.name for s in pipeline.steps}

        for step in pipeline.steps:
            for field_name, binding in step.handler_inputs.items():
                if binding.namespace == "step" and binding.step_name:
                    if binding.step_name not in step_names:
                        errors.append(
                            f"Step '{step.name}': handler_inputs field '{field_name}' "
                            f"references unknown step '{binding.step_name}'"
                        )

        return errors

    def _is_inside_loop_body(
        self, step: "StepConfig", pipeline: "PipelineConfig"
    ) -> bool:
        """Check if step is inside a loop body. (Placeholder for Phase 4)"""
        # Loop support added in Phase 4
        return False

    def _is_map_step(self, step: "StepConfig") -> bool:
        """Check if step is a map step. (Placeholder for Phase 4)"""
        # Map support added in Phase 4
        return hasattr(step, "map_config") and step.map_config is not None

    def validate_bindings(self, pipeline: "PipelineConfig") -> list[str]:
        """Validate binding structure and content."""
        errors = []

        for step in pipeline.steps:
            # Validate handler_inputs
            for field_name, binding in step.handler_inputs.items():
                if not binding.field_path:
                    errors.append(
                        f"Step '{step.name}': handler_inputs field "
                        f"'{field_name}' has empty field_path"
                    )

                # Check for invalid characters in field paths
                if ".." in binding.field_path or binding.field_path.startswith("."):
                    errors.append(
                        f"Step '{step.name}': handler_inputs field "
                        f"'{field_name}' has invalid field_path "
                        f"'{binding.field_path}'"
                    )

            # Validate handler_outputs
            for field_name, output_binding in step.handler_outputs.items():
                if not output_binding.binding.field_path:
                    errors.append(
                        f"Step '{step.name}': handler_outputs field "
                        f"'{field_name}' has empty field_path"
                    )

        return errors

    def validate_circular_dependencies(self, pipeline: "PipelineConfig") -> list[str]:
        """Detect circular dependencies in step references."""
        errors = []

        # Build dependency graph
        dependencies = {}
        for step in pipeline.steps:
            dependencies[step.name] = step.depends_on

        # Check each step for circular dependencies
        for step in pipeline.steps:
            visited = set()
            path = []
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

    def validate_map_steps(self, pipeline: "PipelineConfig") -> list[str]:
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

            # map_over must not reference mapNs
            for field, binding in map_config.map_over.items():
                if binding.namespace == "mapNs":
                    errors.append(
                        f"Step '{step.name}': map_over field '{field}' cannot "
                        f"reference mapNs (not available outside map body)"
                    )

            # map_inputs should reference mapNs (directly or via dynamic key)
            for field, binding in map_config.map_inputs.items():
                # Allow direct mapNs reference OR dynamic key syntax with mapNs
                # Dynamic key syntax: optionsNs.mapping[mapNs.iteration.key]
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

            # Validate wildcard references in handler_inputs
            for field, binding in step.handler_inputs.items():
                if "*" in binding.field_path:
                    # Check referenced step is a map step
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
