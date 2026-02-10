"""
Map output collection with wildcard access support.

Provides structured access to map step outputs enabling patterns like:
- step.* → all outputs
- step.0, step.-1 → indexed access
- step.*.json.field → collect field from all outputs
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ...schemas import StepOutput


@dataclass(frozen=True, slots=True)
class MapIterationContext:
    """Iteration context accessed via mapNs.iteration.*"""

    index: int
    value: Any
    key: str | None
    total: int


class MapOutputCollection:
    """
    Collection of map step outputs enabling wildcard and key-based access.

    Access patterns (via handler_inputs bindings):
    - step.* → list of all outputs
    - step.0, step.1 → indexed access (works for list and dict map_over)
    - step.-1 → last output
    - step.key_name → access by iteration key (ONLY for dict-based map_over)
    - step.*.json.field → collect field from each

    Iteration in map_over (NEW):
    - step_name.* → iterate over MapOutputCollection with keys
    - Use in map_over to process each output from previous map step
    - Provides mapNs.iteration.key and mapNs.iteration.value per iteration

    Key-based access requirements:
    - map_over must use dict format: `map_over: { model: optionsNs.models }`
    - Options must define dict: `models: { qwen: qwen, phi: phi }`
    - Keys come from dict keys, values from dict values

    Example (dict map_over - key access works):
        options:
          models:
            qwen: qwen
            phi: phi
        steps:
          - name: answer_all
            map_over: { model: optionsNs.models }
          - name: next_step
            handler_inputs:
              answer: answer_all.phi.raw  # ✅ Key access works

    Example (list map_over - key access NOT available):
        options:
          models: [qwen, phi]
        steps:
          - name: answer_all
            map_over: { model: optionsNs.models }
          - name: next_step
            handler_inputs:
              answer: answer_all.0.raw  # ✅ Index access works
              # answer: answer_all.phi.raw  # ❌ No keys with list

    Example (iterating over MapOutputCollection in map_over):
        steps:
          - name: answer_all
            map_over: { model: optionsNs.answer_models }  # Dict-based
          - name: decompose_all
            map_over:
              answer: answer_all.*  # ✅ Iterate over collection
            map_inputs:
              model_ref: mapNs.iteration.key      # "qwen", "phi", etc.
              answer_text: mapNs.iteration.value.raw  # StepOutput.raw
    """

    def __init__(
        self, outputs: list["StepOutput"], keys: list[str | None] | None = None
    ):
        """
        Args:
            outputs: List of step outputs
            keys: Optional list of iteration keys (same length as outputs)
        """
        self._outputs = tuple(outputs)  # Immutable
        self._keys = tuple(keys) if keys else tuple([None] * len(outputs))
        # Build key-to-index mapping for O(1) lookups
        self._key_map = {k: i for i, k in enumerate(self._keys) if k is not None}

    def all_outputs(self) -> list["StepOutput"]:
        """Return all outputs as list."""
        return list(self._outputs)

    def get_output(self, index: int) -> "StepOutput":
        """Get output by index (supports negative)."""
        return self._outputs[index]

    def get_output_by_key(self, key: str) -> "StepOutput | None":
        """Get output by iteration key (for dict-based map_over)."""
        idx = self._key_map.get(key)
        return self._outputs[idx] if idx is not None else None

    def __len__(self) -> int:
        return len(self._outputs)

    def __iter__(self):
        return iter(self._outputs)

    def items(self) -> list[tuple[str, "StepOutput"]]:
        """
        Return (key, output) pairs for dict-based map_over.

        Enables iterating over MapOutputCollection in map_over bindings.
        Only works with dict-based map_over that provides keys.

        Returns:
            List of (key, output) tuples

        Raises:
            ValueError: If collection has no keys (list-based map_over)

        Example:
            map_over:
              answer: answer_all.*  # answer_all from dict-based map_over
            # Iterates over: [("qwen", output1), ("phi", output2), ...]
        """
        if not self._key_map:
            raise ValueError(
                "Cannot iterate over MapOutputCollection with step_name.* in map_over: "
                "collection has no keys (created from list-based map_over). "
                "To iterate over this collection, use dict-based map_over in the "
                "step that created it. Example: map_over: { model: optionsNs.models } "
                "where models is a dict, not a list."
            )
        return [
            (key, self._outputs[i])
            for i, key in enumerate(self._keys)
            if key is not None
        ]

    @property
    def json(self) -> "MapJsonAccessor":
        """Access .json from all outputs."""
        return MapJsonAccessor(self._outputs)


class MapJsonAccessor:
    """
    Helper for step.*.json.* access pattern.

    Note: Returns None for missing fields to maintain list alignment.
    Consider filtering None values if only present fields needed.
    """

    def __init__(self, outputs: tuple["StepOutput", ...]):
        self._outputs = outputs

    def __getattr__(self, name: str) -> list[Any]:
        """
        Collect attribute from all outputs' json.

        IMPORTANT: Returns None for outputs missing the requested field
        to maintain index alignment with the original outputs.

        Example:
            outputs = [{"score": 0.8}, {}, {"score": 0.9}]
            collection.json.score → [0.8, None, 0.9]

        Filter out None values if you only need present fields:
            scores = [s for s in collection.json.score if s is not None]
        """
        results = []
        for output in self._outputs:
            json_data = output.to_checkpoint_json()
            if json_data and name in json_data:
                results.append(json_data[name])
            else:
                results.append(None)
        return results
