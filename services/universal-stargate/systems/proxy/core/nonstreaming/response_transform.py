"""
Response transformation helpers for non-streaming requests.

Handles transformation of chat completion responses to text completion format.
"""

import json
from typing import Any

from fastapi.responses import Response
from universal_logging import get_logger

from .context import RequestContext

logger = get_logger(__name__)


def transform_response_to_prompt_format(
    response: Response, context: RequestContext
) -> Response:
    """
    Transform response from chat completion to prompt format.

    Args:
        response: Original response object
        context: Request context for tracking middleware actions

    Returns:
        Transformed response or original if transformation fails
    """
    logger.info(
        "🔄 RESPONSE TRANSFORMATION: Triggering transformation for prompt request"
    )

    if not hasattr(response, "body") or not response.body:
        logger.warning("Response has no body, skipping transformation")
        return response

    try:
        response_body = response.body.decode("utf-8")
        response_json = json.loads(response_body)

        # Transform the response
        transformed_response = transform_dict_to_prompt_format(response_json)

        if transformed_response:
            # Create new response with transformed content
            new_response_body = json.dumps(transformed_response).encode("utf-8")
            response = Response(
                content=new_response_body,
                status_code=response.status_code,
                headers=dict(response.headers),
            )

            context.middleware_actions.append("response_transformed_to_prompt_format")
            logger.info(
                "🔄 Transformed response from chat completion to text completion format"
            )

    except Exception as e:
        logger.warning(f"Failed to transform response: {e}")
        # Continue with original response if transformation fails

    return response


def transform_dict_to_prompt_format(
    response_json: dict[str, Any],
) -> dict[str, Any] | None:
    """
    Transform response JSON from chat to prompt format.

    Args:
        response_json: Chat completion response dict

    Returns:
        Text completion response dict or None if transformation fails
    """
    try:
        # Check if this is a chat completion response
        if "choices" not in response_json:
            return None

        # Transform choices
        transformed_choices = []
        for choice in response_json["choices"]:
            if "message" in choice:
                # Extract content from message
                content = choice["message"].get("content", "")
                transformed_choice = {
                    "text": content,
                    "index": choice.get("index", 0),
                    "finish_reason": choice.get("finish_reason", "stop"),
                }
                if "logprobs" in choice:
                    transformed_choice["logprobs"] = choice["logprobs"]
                transformed_choices.append(transformed_choice)

        # Build transformed response
        transformed_response = {
            "id": response_json.get("id", ""),
            "object": "text_completion",
            "created": response_json.get("created", 0),
            "model": response_json.get("model", ""),
            "choices": transformed_choices,
        }

        # Copy usage if present
        if "usage" in response_json:
            transformed_response["usage"] = response_json["usage"]

        return transformed_response

    except Exception as e:
        logger.warning(f"Failed to transform response dict: {e}")
        return None


def get_response_data(response: Response) -> dict[str, Any] | None:
    """
    Extract response data for monitoring.

    Args:
        response: Response object

    Returns:
        Parsed response data or metadata dict
    """
    from fastapi.responses import StreamingResponse

    if isinstance(response, StreamingResponse):
        return {
            "type": "streaming_response",
            "media_type": "text/event-stream",
            "stream": True,
            "status": "streaming_in_progress",
        }
    elif hasattr(response, "body") and response.body:
        body_bytes = bytes(response.body)
        try:
            response_body = body_bytes.decode("utf-8")  # ← INSIDE try block (FIXED)
            return json.loads(response_body)
        except UnicodeDecodeError as e:  # ← Catches decode errors first
            return {"raw_content": str(response.body)[:1000], "decode_error": str(e)}
        except json.JSONDecodeError as e:
            return {"raw_content": str(response.body)[:1000], "parse_error": str(e)}
        except Exception as e:
            return {"raw_content": str(response.body)[:1000], "error": str(e)}
    else:
        return {"error": "No response content available"}
