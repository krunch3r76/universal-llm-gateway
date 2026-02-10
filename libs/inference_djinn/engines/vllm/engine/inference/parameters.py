"""
VLLM engine parameter extraction and sampling parameter creation.

Handles conversion from OpenAI-style parameters to vLLM SamplingParams.
"""

from universal_logging import get_logger
from typing import Any

from vllm.sampling_params import SamplingParams, StructuredOutputsParams

logger = get_logger(__name__)


class VLLMParameterBuilder:
    """Handles parameter extraction and sampling parameter creation for VLLM engine."""

    def __init__(self, engine_instance: Any):
        """
        Initialize parameter builder with reference to engine instance.

        Args:
            engine_instance: The VLLMEngine instance to operate on
        """
        self.engine = engine_instance

    def extract_vllm_params(self) -> dict[str, Any]:
        """Extract vLLM-specific parameters from kwargs. Pass through all parameters."""
        # Pass through all kwargs - let vLLM handle validation
        return self.engine.kwargs.copy()

    def create_sampling_params(
        self, generation_params: dict[str, Any]
    ) -> SamplingParams:
        """Create vLLM SamplingParams from generation parameters."""
        sampling_kwargs = generation_params.copy()

        # Filter parameters that vLLM's SamplingParams doesn't accept
        # stream: Valid OpenAI API parameter, but vLLM determines streaming mode
        #         by which API method is called (generate vs generate_stream),
        #         not by a SamplingParams argument
        sampling_kwargs.pop("stream", None)

        # Handle special cases that need remapping for vLLM compatibility
        if "stop" in sampling_kwargs and isinstance(sampling_kwargs["stop"], str):
            # vLLM expects stop to be a list, not a string
            sampling_kwargs["stop"] = [sampling_kwargs["stop"]]

        # Convert response_format to vLLM's structured_outputs if present
        if "response_format" in sampling_kwargs:
            response_format = sampling_kwargs.pop("response_format")

            if isinstance(response_format, dict):
                response_type = response_format.get("type")

                if response_type == "json_object":
                    # Simple JSON mode (OpenAI response_format.type = "json_object")
                    sampling_kwargs["structured_outputs"] = StructuredOutputsParams(
                        json_object=True
                    )
                elif response_type == "json_schema":
                    # Structured output with JSON schema (OpenAI response_format.type = "json_schema")
                    # Extract schema from response_format.json_schema.schema
                    json_schema_obj = response_format.get("json_schema", {})
                    schema = json_schema_obj.get("schema")
                    if schema:
                        # Pass schema to vLLM as StructuredOutputsParams(json=schema)
                        # vLLM's guided decoding enforces all schema constraints:
                        # - additionalProperties, required, enum, type constraints, etc.
                        # Note: Cannot pass explicit disable_fallback, structural_tag, etc.
                        # alongside json parameter due to vLLM validation ("multiple constraints")
                        sampling_kwargs["structured_outputs"] = StructuredOutputsParams(
                            json=schema
                        )
                    else:
                        logger.warning(
                            "response_format.type='json_schema' provided but no schema found in response_format.json_schema.schema"
                        )

        # Let vLLM handle parameter validation - pass all parameters through
        try:
            return SamplingParams(**sampling_kwargs)
        except TypeError as e:
            # If vLLM rejects parameters, log the issue but don't fail silently
            logger.warning(
                f"vLLM rejected some parameters: {e}. Attempting with basic parameters only."
            )
            # Fallback to basic parameters that are known to work
            basic_params = {}
            for key in [
                "temperature",
                "top_p",
                "top_k",
                "max_tokens",
                "stop",
                "frequency_penalty",
                "presence_penalty",
                "repetition_penalty",
                "structured_outputs",  # Preserve JSON mode in fallback
            ]:
                if key in sampling_kwargs:
                    basic_params[key] = sampling_kwargs[key]
            return SamplingParams(**basic_params)
