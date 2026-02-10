"""
Checkpoint adapters for different output types.

Adapts various output types to the CheckpointManager's expected interface.
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ...handlers.protocol import StepOutput


class StepOutputCheckpointAdapter:
    """
    Adapts StepOutput to CheckpointManager interface.

    Extracts checkpoint data from StepOutput fields.
    """

    def __init__(self, step_output: "StepOutput"):
        self._output = step_output

    def to_checkpoint_data(self) -> tuple[str, dict[str, Any] | None, dict[str, Any]]:
        """
        Extract checkpoint data from StepOutput.

        Returns:
            Tuple of (raw_text, json_data, metadata)
        """
        raw = self._output.raw
        json_data = self._output.json

        metadata: dict[str, Any] = {
            "latency_ms": self._output.latency_ms,
            "model_id": self._output.model_id,
            "step_id": self._output.step_id,
            "prompt_tokens": self._output.prompt_tokens,
            "completion_tokens": self._output.completion_tokens,
            "temperature": self._output.temperature,
            "max_tokens": self._output.max_tokens,
        }
        # Remove None values for cleaner storage
        metadata = {k: v for k, v in metadata.items() if v is not None}

        return raw, json_data, metadata
