"""Inference engine metadata derived from gateway config and registry model info.

Builds engine name, input format, and chat-template usage flags for API
responses based on model format and input_schema from the model registry.
"""

from typing import Any

from ...core.model_registry import ModelRegistry


def get_inference_engine_info(
    registry: ModelRegistry,
    model_id: str,
    inference_engine_specs: dict[str, Any],
) -> dict[str, Any]:
    """Get inference engine information for a model based on input_schema."""
    model_info = registry.get_model_info(model_id)
    if not model_info:
        return {}

    model_format = model_info.format
    engine_specs = inference_engine_specs.get(model_format, {})
    input_schema = getattr(model_info, "input_schema", "messages")
    uses_chat_template = input_schema == "messages"

    specification = {
        "input_format": input_schema,
        "uses_chat_template": uses_chat_template,
        "expected_field": input_schema,
    }

    return {
        "engine_name": engine_specs.get("engine_name", "unknown"),
        "format": model_format,
        "input_format": input_schema,
        "expected_field": input_schema,
        "uses_chat_template": uses_chat_template,
        "specification": specification,
    }
