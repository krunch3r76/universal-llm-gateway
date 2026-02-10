"""Main transformation engine orchestrator."""

from typing import Any

from model_id import ModelId
from universal_logging import get_logger

from ..implementations.cursorcore import format_cursorcore_prompt
from ..implementations.template_based import (
    apply_template_transformation,
    apply_transformation_filters,
)
from ..registry.loaders import TransformationConfigLoader
from ..registry.registry import TransformationRegistry, create_default_registry
from .types import OutputFormat, TransformationResult

logger = get_logger(__name__)


class TransformationEngine:
    """
    Main orchestrator for message transformations.

    Responsibilities:
    - Resolve transformation config for model
    - Apply filters (remove system messages, truncate)
    - Transform format (messages → prompt if needed)
    - Return structured result with metadata

    Invariants:
    - All config lookups use in-memory data (no I/O)
    - Fail-fast on unknown transformation types
    - Uses ModelId objects, not strings
    """

    def __init__(
        self,
        config_loader: TransformationConfigLoader,
        registry: TransformationRegistry | None = None,
    ) -> None:
        """
        Initialize transformation engine.

        Args:
            config_loader: Pre-loaded configuration (startup I/O already done)
            registry: TransformationRegistry instance (creates default if None)
        """
        self._config_loader = config_loader
        self._registry = registry or create_default_registry()

    def transform(
        self,
        messages: list[dict[str, Any]],
        model: ModelId,
        target_format: OutputFormat,
    ) -> TransformationResult:
        """
        Transform messages based on model requirements.

        Args:
            messages: OpenAI Chat Completions format messages
            model: Parsed ModelId object
            target_format: Expected output format (MESSAGES or PROMPT)

        Returns:
            TransformationResult with transformed content and metadata

        Raises:
            ValueError: If transformation type is unknown (fail-fast)
        """
        actions: list[str] = []
        metadata: dict[str, Any] = {
            "model_id": model.original,
            "target_format": target_format.value,
            "transformation_applied": False,
        }

        # Get transformation config for model
        transform_config = self._config_loader.get_for_model(model)

        if not transform_config:
            # No transformation configured
            if target_format == OutputFormat.PROMPT:
                # Use generic fallback: extract last user message
                from ..implementations.generic import transform_generic_prompt

                logger.info(
                    f"No transformation config for {model}, using generic fallback"
                )
                prompt = transform_generic_prompt(messages, {})
                metadata.update(
                    {
                        "transformation_applied": True,
                        "transformation_name": "generic",
                        "transformation_type": "generic",
                        "prompt_length": len(prompt),
                    }
                )
                return TransformationResult(
                    format=OutputFormat.PROMPT,
                    content=prompt,
                    metadata=metadata,
                    actions=["generic_fallback"],
                )
            else:
                # Pass through in messages format
                logger.debug(f"No transformation config for {model}, passing through")
                return TransformationResult(
                    format=OutputFormat.MESSAGES,
                    content=messages,
                    metadata=metadata,
                    actions=["pass_through"],
                )

        settings = transform_config.get("settings", {})
        transform_name = transform_config.get("name")
        transformation_type = settings.get("transformation_type", "template")

        # Apply filters (messages → filtered_messages)
        filtered_messages = apply_transformation_filters(messages, settings)
        if len(filtered_messages) < len(messages):
            actions.append("filtered_messages")

        # Check if we need prompt format
        if target_format == OutputFormat.PROMPT:
            prompt = self._transform_to_prompt(
                filtered_messages, transformation_type, settings, actions
            )

            metadata.update(
                {
                    "transformation_applied": True,
                    "transformation_name": transform_name,
                    "transformation_type": transformation_type,
                    "prompt_length": len(prompt),
                }
            )

            # Extract generation params if present
            if "generation_params" in settings:
                metadata["generation_params"] = settings["generation_params"]

            return TransformationResult(
                format=OutputFormat.PROMPT,
                content=prompt,
                metadata=metadata,
                actions=actions,
            )
        else:
            # Keep messages format (just filtered)
            return TransformationResult(
                format=OutputFormat.MESSAGES,
                content=filtered_messages,
                metadata=metadata,
                actions=actions,
            )

    def _transform_to_prompt(
        self,
        messages: list[dict[str, Any]],
        transformation_type: str,
        settings: dict[str, Any],
        actions: list[str],
    ) -> str:
        """
        Transform messages to prompt format.

        Args:
            messages: Filtered messages
            transformation_type: Type of transformation
            settings: Transformation settings
            actions: Actions list (modified in place)

        Returns:
            Formatted prompt string

        Raises:
            ValueError: If transformation type is unknown
        """
        actions.append(f"transform_to_{transformation_type}_prompt")

        if transformation_type == "cursorcore":
            return format_cursorcore_prompt(messages, settings)
        elif transformation_type == "template":
            return apply_template_transformation(messages, settings)
        else:
            # Check registry for custom handler
            handler = self._registry.get(transformation_type)
            if handler:
                return handler(messages, settings)

            # Fail-fast: unknown transformation type is a configuration error
            raise ValueError(
                f"Unknown transformation type: {transformation_type}. "
                f"Available types: template, cursorcore, {self._registry.list_types()}"
            )

    def apply_filters_only(
        self,
        messages: list[dict[str, Any]],
        model: ModelId,
    ) -> list[dict[str, Any]]:
        """
        Apply transformation filters without format conversion.

        Use this for models with input_schema="messages" that need filtering.

        Args:
            messages: Original messages
            model: Parsed ModelId object

        Returns:
            Filtered messages (still in messages format)
        """
        transform_config = self._config_loader.get_for_model(model)
        if not transform_config:
            return messages

        settings = transform_config.get("settings", {})
        return apply_transformation_filters(messages, settings)

    def get_config_for_model(self, model: ModelId) -> dict[str, Any] | None:
        """
        Get the raw transformation config for a model.

        Args:
            model: Parsed ModelId object

        Returns:
            Transformation config dict or None
        """
        return self._config_loader.get_for_model(model)
