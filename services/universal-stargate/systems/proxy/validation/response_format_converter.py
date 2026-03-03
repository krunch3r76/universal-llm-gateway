"""Response format converter for engine compatibility.

GGUF/llama.cpp models use:
    {"type": "json_object", "schema": {...}}

vLLM/OpenAI-compatible models use:
    {"type": "json_schema", "json_schema": {"name": "...", "strict": true,
     "schema": {...}}}

Heuristic: model IDs containing quantization markers (q4, q8, iq3, etc.)
indicate GGUF; absence indicates vLLM.
"""

import re
from typing import Any

from universal_logging import get_logger

logger = get_logger(__name__)

# Matches GGUF quantization markers: -q4, -q8_0, -iq3, _q5-k-m, etc.
# Preceded by separator to avoid matching "qwen", "q2-5" (version fragments).
_GGUF_QUANT_RE = re.compile(r"[-_]i?q[2-8](?:[-_]|$)", re.IGNORECASE)


def is_gguf_quantized_model(model_id: str) -> bool:
    """Detect GGUF quantization markers in a model ID.

    Args:
        model_id: Model identifier (e.g. "phi-4-q4-k-m-16384" or "microsoft/phi-4")

    Returns:
        True if model_id contains GGUF quant patterns like q4, q8_0, iq3, etc.
    """
    return bool(_GGUF_QUANT_RE.search(model_id))


def convert_response_format_for_engine(
    model_id: str, response_format: dict[str, Any] | None
) -> dict[str, Any] | None:
    """Convert response_format to match the target engine's expected schema.

    Conversion rules:
        - None → None (no-op)
        - type=text → passthrough
        - type=json_schema → passthrough (already vLLM-style)
        - type=json_object + schema + GGUF model → passthrough (llama.cpp native)
        - type=json_object + schema + non-GGUF model → convert to json_schema wrapper
        - type=json_object without schema → passthrough

    Args:
        model_id: Target model identifier for engine detection
        response_format: The response_format dict (or None)

    Returns:
        Converted response_format dict, or None if input was None
    """
    if response_format is None:
        return None

    fmt_type = response_format.get("type")

    if fmt_type in ("json_schema", "text"):
        return response_format

    if fmt_type == "json_object" and "schema" in response_format:
        if not is_gguf_quantized_model(model_id):
            schema = response_format["schema"]
            logger.debug(
                "Converting response_format from json_object to json_schema "
                "for non-GGUF model %s",
                model_id,
            )
            return {
                "type": "json_schema",
                "json_schema": {
                    "name": "pipeline_response",
                    "strict": True,
                    "schema": schema,
                },
            }

    return response_format
