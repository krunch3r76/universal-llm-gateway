"""
Request transformer module - handles message and format transformations.

Uses TransformationEngine from systems.transformations for all transformations.
"""

from typing import Any

from model_id import ModelId
from universal_logging import get_logger

from ....transformations import OutputFormat, TransformationEngine

logger = get_logger(__name__)


class RequestTransformer:
    """Handles message and format transformations."""

    def __init__(self, transformation_engine: TransformationEngine) -> None:
        """
        Initialize RequestTransformer.

        Args:
            transformation_engine: TransformationEngine instance
        """
        self._engine = transformation_engine

    def _preprocess_prompt_field(self, prompt: str) -> str:
        """Remove FIM markers from prompt field."""
        cleaned = prompt.replace("<|fim_prefix|>", "")
        cleaned = cleaned.replace("<|fim_suffix|>", "")
        cleaned = cleaned.replace("<|fim_middle|>", "")
        return cleaned.strip()

    def preprocess_prompt_field(self, prompt: str) -> str:
        """Public method to preprocess prompt field."""
        return self._preprocess_prompt_field(prompt)

    def transform_to_prompt(
        self,
        messages: list[dict[str, Any]],
        model: ModelId,
        metadata: dict[str, Any],
        actions: list[str],
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """
        Transform messages to prompt format.

        Args:
            messages: Filtered message list
            model: Parsed ModelId object
            metadata: Transformation metadata dictionary (modified in place)
            actions: Middleware actions list (modified in place)

        Returns:
            Tuple of (empty_messages, metadata_with_prompt)
        """
        result = self._engine.transform(
            messages=messages,
            model=model,
            target_format=OutputFormat.PROMPT,
        )

        metadata["prompt_content"] = result.content
        metadata["transformation_applied"] = result.transformation_applied

        if result.metadata.get("generation_params"):
            metadata["generation_params"] = result.metadata["generation_params"]

        actions.extend(result.actions)

        logger.info(f"✅ Transformation complete: {len(result.content)} chars")
        return [], metadata

    def apply_content_to_request_data(
        self,
        request_data: dict[str, Any],
        messages: list[dict[str, Any]],
        metadata: dict[str, Any],
    ) -> None:
        """Apply processed content to request data."""
        transformation_applied = metadata.get("transformation_applied", False)

        if transformation_applied:
            prompt_content = metadata.get("prompt_content", "")
            if prompt_content:
                request_data["prompt"] = prompt_content
                if "messages" in request_data:
                    del request_data["messages"]
                logger.info(
                    f"✅ Applied prompt to request: {len(prompt_content)} chars"
                )
        else:
            request_data["messages"] = messages
            if "prompt" in request_data:
                del request_data["prompt"]
            logger.info(f"✅ Applied messages to request: {len(messages)} messages")
