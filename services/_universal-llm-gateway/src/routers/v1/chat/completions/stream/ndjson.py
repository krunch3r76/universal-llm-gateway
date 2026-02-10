"""NDJSON serialization for streaming responses.

Wire format contract (MUST preserve):
- NDJSON: {"signal": ..., "payload": ...}\\n
- Signals: "chunk", "error", "complete"
- Chunk payload: OpenAI format {"choices": [{"delta": {"content": "..."}, ...}]}
"""

import json


def create_ndjson_event(signal: str, payload) -> dict:
    """
    Create signal/payload event structure.

    Args:
        signal: Event signal ("chunk", "error", "complete")
        payload: Event payload (any JSON-serializable)

    Returns:
        dict: {"signal": ..., "payload": ...}
    """
    return {"signal": signal, "payload": payload}


def serialize_ndjson_event(event: dict) -> str:
    """
    Serialize event to NDJSON format (JSON + newline).

    Args:
        event: dict with "signal" and "payload" keys

    Returns:
        str: JSON string with trailing newline
    """
    return json.dumps(event, ensure_ascii=False) + "\n"


def convert_worker_chunk_to_openai_format(chunk: dict) -> dict:
    """
    Convert worker chunk format to OpenAI streaming format.

    Transforms:
        Worker: {"choices": [{"text": "...", "finish_reason": null}], ...}
        OpenAI: {"choices": [{"delta": {"content": "..."}, "finish_reason": null}], ...}

    Args:
        chunk: Worker-format chunk

    Returns:
        dict: OpenAI-format chunk
    """
    if not isinstance(chunk, dict) or "choices" not in chunk or not chunk["choices"]:
        return chunk

    openai_chunk: dict = {"choices": []}
    for choice in chunk["choices"]:
        openai_choice: dict = {}

        # Convert 'text' field to 'delta' format
        if "text" in choice:
            openai_choice["delta"] = {"content": choice["text"]}
        elif "delta" in choice:
            # Already in delta format
            openai_choice["delta"] = choice["delta"]

        # Preserve index and finish_reason
        if "index" in choice:
            openai_choice["index"] = choice["index"]
        if "finish_reason" in choice:
            openai_choice["finish_reason"] = choice["finish_reason"]

        openai_chunk["choices"].append(openai_choice)

    # Preserve other fields
    if "usage" in chunk:
        openai_chunk["usage"] = chunk["usage"]

    return openai_chunk


def iter_error_and_complete_events(message: str, error_type: str, error_code: str):
    """
    Yield error event followed by completion marker.

    Args:
        message: Error message
        error_type: OpenAI error type (e.g., "server_error")
        error_code: Error code

    Yields:
        str: NDJSON-formatted error event, then completion event
    """
    error_chunk = {
        "error": {"message": message, "type": error_type, "code": error_code}
    }
    yield serialize_ndjson_event(create_ndjson_event("error", error_chunk))
    yield serialize_ndjson_event(create_ndjson_event("complete", "[DONE]"))
