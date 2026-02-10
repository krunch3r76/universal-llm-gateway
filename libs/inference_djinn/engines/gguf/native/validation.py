"""
Structured output validation for NativeGGUFEngine.

Extracted to maintain SLOC limits (<400) while adding embedding support.
Guards against llama.cpp #19051 (invalid schemas fail open).
"""

import json
from typing import Any

from universal_logging import get_logger

logger = get_logger(__name__)


def validate_response_format(response_format: dict[str, Any]) -> None:
    """Pre-validate response_format before sending to server.

    Guards against llama.cpp #19051 (invalid schemas fail open).

    Args:
        response_format: OpenAI-style response_format dict

    Raises:
        ValueError: If response_format is invalid
    """
    fmt_type = response_format.get("type")
    if fmt_type not in ("json_object", "json_schema", "text"):
        raise ValueError(f"Unsupported response_format type: {fmt_type}")

    if fmt_type == "json_schema":
        json_schema = response_format.get("json_schema", {})
        schema = json_schema.get("schema")
        if not schema or not isinstance(schema, dict):
            raise ValueError(
                "response_format.json_schema.schema must be a non-empty dict"
            )


def verify_structured_output(
    result: dict[str, Any],
    response_format: dict[str, Any],
) -> None:
    """Post-validate that output matches requested schema.

    Logs warning if server generated unconstrained output despite schema.
    Does not raise — caller handles malformed output.

    Args:
        result: OpenAI-style completion response
        response_format: Original response_format request
    """
    choices = result.get("choices")
    if not isinstance(choices, list) or not choices:
        logger.warning(
            f"Structured output validation skipped: missing/empty choices (response_format.type={response_format.get('type')})"
        )
        return

    first_choice = choices[0]
    if not isinstance(first_choice, dict):
        logger.warning(
            f"Structured output validation skipped: choices[0] is not a dict (response_format.type={response_format.get('type')})"
        )
        return

    # llama-server returns chat content as choices[0].message.content,
    # and text completion output as choices[0].text. Support both.
    content: str | None = None
    message = first_choice.get("message")
    if isinstance(message, dict):
        msg_content = message.get("content")
        if isinstance(msg_content, str):
            content = msg_content

    if content is None:
        text = first_choice.get("text")
        if isinstance(text, str):
            content = text

    if content is None:
        logger.warning(
            f"Structured output validation skipped: unable to extract output text from completion result (expected choices[0].message.content or choices[0].text). choice_keys={sorted(first_choice.keys())}"
        )
        return

    try:
        json.loads(content)
    except json.JSONDecodeError:
        logger.warning(
            "Structured output validation failed: server returned non-JSON despite json_schema response_format. This may indicate llama-server did not apply grammar constraint."
        )


def verify_structured_output_content(content: str) -> None:
    """Post-validate streaming content is valid JSON.

    Args:
        content: Concatenated streaming content
    """
    if not content:
        return

    try:
        json.loads(content)
    except json.JSONDecodeError:
        logger.warning(
            "Structured output stream validation failed: concatenated chunks are not valid JSON"
        )
