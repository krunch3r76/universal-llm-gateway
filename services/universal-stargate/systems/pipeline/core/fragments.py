"""
Pipeline fragment loading and composition.

Fragments are reusable step sequences that can be included in pipelines.
Enables DRY pipeline definitions.

TYPE-PRESERVING SUBSTITUTION:
When entire value is a placeholder like "{temperature}", the original
type is preserved (float, int, dict). String interpolation is used
for mixed patterns like "prefix_{name}".

V6 BINDING SUPPORT:
When as_prefix is used, fragment expansion automatically updates
handler_inputs and handler_outputs bindings that reference steps
within the fragment.

Example:
    # Fragment definition
    fragment:
      id: two_step_process
      steps:
        - name: step1
          type: generate
          model_ref: "{model}"
          handler_outputs:
            result: step1.json.result

        - name: step2
          type: transform
          handler_inputs:
            data: step1.json.result  # References step1
          handler_outputs:
            final: step2.json.final

    # Usage in pipeline
    steps:
      - use: two_step_process
        with:
          model: phi-3.5-mini
        as_prefix: proc

    # Expands to:
    # - name: proc_step1
    #   handler_outputs:
    #     result: proc_step1.json.result  # Prefixed!
    # - name: proc_step2
    #   handler_inputs:
    #     data: proc_step1.json.result    # Prefixed!
    #   handler_outputs:
    #     final: proc_step2.json.final    # Prefixed!
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml
from universal_logging import get_logger

from .schemas import FragmentRef, InputBinding, OutputBinding, StepConfig

logger = get_logger(__name__)

# Pattern for full placeholder (entire value is a placeholder)
_FULL_PLACEHOLDER = re.compile(r"^\{([\w\.]+)\}$")
# Pattern for any placeholder in string
_PLACEHOLDER_PATTERN = re.compile(r"\{([\w\.]+)\}")


class FragmentLoader:
    """
    Load and manage pipeline fragments.

    Fragments can be:
    - Separate files in config/fragments.d/
    - Inline in pipeline files (fragments: section)

    Supports type-preserving substitution: if entire value is
    a placeholder, the original type is preserved.
    """

    def __init__(self, config_dir: str = "config"):
        self.config_dir = Path(config_dir)
        self._fragments: dict[str, list[StepConfig]] = {}
        self._loaded = False

    def load(self) -> None:
        """Load all fragment definitions from fragments.d/."""
        fragments_dir = self.config_dir / "fragments.d"

        if fragments_dir.exists():
            for path in sorted(fragments_dir.glob("*.yaml")):
                self._load_fragment_file(path)

        self._loaded = True
        logger.info(f"Loaded {len(self._fragments)} fragments")

    def _load_fragment_file(self, path: Path) -> None:
        """Load fragments from a YAML file."""
        try:
            with path.open() as f:
                data = yaml.safe_load(f) or {}

            fragment_data = data.get("fragment", data)
            fragment_id = fragment_data.get("id")

            if not fragment_id:
                logger.warning(f"Fragment file {path} missing 'id' field")
                return

            steps_data = fragment_data.get("steps", [])
            steps = [StepConfig(**step) for step in steps_data]

            self._fragments[fragment_id] = steps
            logger.debug(f"Loaded fragment '{fragment_id}' ({len(steps)} steps)")

        except Exception as e:
            logger.error(f"Failed to load fragment from {path}: {e}")

    def register_inline_fragments(
        self,
        fragments: dict[str, list[dict[str, Any]]] | None,
    ) -> None:
        """Register fragments defined inline in a pipeline."""
        if not fragments:
            return

        for fragment_id, steps_data in fragments.items():
            steps = [StepConfig(**step) for step in steps_data]
            self._fragments[fragment_id] = steps
            logger.debug(f"Registered inline fragment '{fragment_id}'")

    def get_fragment(self, fragment_id: str) -> list[StepConfig]:
        """Get a fragment by ID."""
        if fragment_id not in self._fragments:
            raise KeyError(f"Fragment '{fragment_id}' not found")
        return self._fragments[fragment_id]

    def expand_fragment_ref(
        self,
        ref: FragmentRef,
    ) -> list[StepConfig]:
        """
        Expand a fragment reference into steps.

        Args:
            ref: Fragment reference with substitutions

        Returns:
            List of steps with variables substituted and IDs prefixed
        """
        fragment_steps = self.get_fragment(ref.use)
        prefix = ref.as_prefix or ""

        # Get step IDs from original fragment for reference updates
        original_ids = {step.name for step in fragment_steps}

        expanded = []
        for step in fragment_steps:
            if prefix:
                # Create a copy with substitutions and prefixing
                step_dict = step.model_dump(by_alias=True)

                # Prefix step ID
                step_dict["id"] = f"{prefix}_{step_dict['id']}"

                # Update handler_inputs bindings (v6)
                if step_dict.get("handler_inputs"):
                    step_dict["handler_inputs"] = self._prefix_input_bindings(
                        step_dict["handler_inputs"], original_ids, prefix
                    )

                # Update handler_outputs bindings (v6)
                if step_dict.get("handler_outputs"):
                    step_dict["handler_outputs"] = self._prefix_output_bindings(
                        step_dict["handler_outputs"], original_ids, prefix
                    )

                # Apply variable substitutions from 'with' (type-preserving)
                step_dict = self._substitute_variables(step_dict, ref.with_)

                expanded.append(StepConfig(**step_dict))
            else:
                # No prefix - just apply variable substitutions to a copy
                if ref.with_:
                    step_dict = step.model_dump(by_alias=True)
                    step_dict = self._substitute_variables(step_dict, ref.with_)
                    expanded.append(StepConfig(**step_dict))
                else:
                    # No changes needed - use original step
                    expanded.append(step)

        return expanded

    def _substitute_variables(
        self,
        data: dict[str, Any],
        variables: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Substitute {variable} placeholders in step config.

        TYPE-PRESERVING: If entire value is a placeholder like "{var}",
        the original type from variables is preserved.

        Supports:
        - Full replacement: "{var}" → variables["var"] (preserves type)
        - Partial: "prefix_{var}" → "prefix_value" (string)
        - Nested: "{a.b}" → variables["a"]["b"]
        """

        def resolve_path(var_path: str, vars_dict: dict[str, Any]) -> Any | None:
            """Resolve a dotted path in variables dict."""
            parts = var_path.split(".")
            value: Any = vars_dict

            for part in parts:
                if isinstance(value, dict) and part in value:
                    value = value[part]
                else:
                    return None

            return value

        def process(obj: Any) -> Any:
            if isinstance(obj, str):
                # Check if entire value is a single placeholder (type-preserving)
                full_match = _FULL_PLACEHOLDER.match(obj)
                if full_match:
                    var_path = full_match.group(1)
                    resolved = resolve_path(var_path, variables)
                    if resolved is not None:
                        return resolved  # Preserves original type!
                    return obj  # Keep original if not found

                # Partial substitution (always returns string)
                def replace(match: re.Match[str]) -> str:
                    var_path = match.group(1)
                    resolved = resolve_path(var_path, variables)
                    if resolved is not None:
                        return str(resolved)
                    return match.group(0)  # Keep original if not found

                return _PLACEHOLDER_PATTERN.sub(replace, obj)

            elif isinstance(obj, dict):
                return {k: process(v) for k, v in obj.items()}

            elif isinstance(obj, list):
                return [process(item) for item in obj]

            return obj

        return process(data)

    def _reconstruct_input_binding(self, binding_dict: dict[str, Any]) -> InputBinding:
        """Reconstruct InputBinding from serialized dict."""
        return InputBinding(**binding_dict)

    def _prefix_input_bindings(
        self,
        bindings: dict[str, dict[str, Any]],
        original_ids: set[str],
        prefix: str,
    ) -> dict[str, InputBinding]:
        """
        Prefix step references in handler_inputs bindings.

        Inputs:
            bindings: Serialized InputBinding dicts from model_dump()
            original_ids: Set of step names from original fragment
            prefix: Prefix to apply to internal step references

        Outputs:
            dict[str, InputBinding]: Updated bindings with prefixed step references

        InputBinding structure: {namespace, step_name, field_path}
        Only update if namespace == "step" and step_name in original_ids.
        """
        result = {}
        for field_name, binding in bindings.items():
            if isinstance(binding, dict):
                if (
                    binding.get("namespace") == "step"
                    and binding.get("step_name") in original_ids
                ):
                    # Create a new InputBinding with prefixed step_name
                    result[field_name] = InputBinding(
                        namespace=binding["namespace"],
                        step_name=f"{prefix}_{binding['step_name']}",
                        field_path=binding["field_path"],
                    )
                else:
                    # Reconstruct the InputBinding from dict
                    result[field_name] = self._reconstruct_input_binding(binding)
            else:
                # Already an InputBinding object (shouldn't happen after model_dump)
                result[field_name] = binding
        return result

    def _prefix_output_bindings(
        self,
        bindings: dict[str, dict[str, Any]],
        original_ids: set[str],
        prefix: str,
    ) -> dict[str, OutputBinding]:
        """
        Prefix step references in handler_outputs bindings.

        Inputs:
            bindings: Serialized OutputBinding dicts from model_dump()
            original_ids: Set of step names from original fragment
            prefix: Prefix to apply to internal step references

        Outputs:
            dict[str, OutputBinding]: Updated bindings with prefixed step references

        OutputBinding structure:
        {binding: {namespace, step_name, field_path}, optional: bool}
        Only update if binding.namespace == "step" and step_name in original_ids.
        """
        result = {}
        for field_name, output_binding in bindings.items():
            if isinstance(output_binding, dict) and "binding" in output_binding:
                inner = output_binding["binding"]
                if isinstance(inner, dict):
                    if (
                        inner.get("namespace") == "step"
                        and inner.get("step_name") in original_ids
                    ):
                        # Create new InputBinding with prefixed step_name
                        prefixed_input_binding = InputBinding(
                            namespace=inner["namespace"],
                            step_name=f"{prefix}_{inner['step_name']}",
                            field_path=inner["field_path"],
                        )
                        # Create new OutputBinding with prefixed InputBinding
                        result[field_name] = OutputBinding(
                            binding=prefixed_input_binding,
                            optional=output_binding.get("optional", False),
                        )
                    else:
                        # Reconstruct the bindings from dicts
                        input_binding = self._reconstruct_input_binding(inner)
                        result[field_name] = OutputBinding(
                            binding=input_binding,
                            optional=output_binding.get("optional", False),
                        )
                else:
                    result[field_name] = output_binding
            else:
                result[field_name] = output_binding
        return result

    def list_fragments(self) -> list[str]:
        """List all available fragment IDs."""
        return sorted(self._fragments.keys())


# Singleton
_fragment_loader: FragmentLoader | None = None


def get_fragment_loader(config_dir: str = "config") -> FragmentLoader:
    """Get or create fragment loader singleton."""
    global _fragment_loader
    if _fragment_loader is None:
        _fragment_loader = FragmentLoader(config_dir)
        _fragment_loader.load()
    return _fragment_loader
