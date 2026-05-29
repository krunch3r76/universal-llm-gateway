"""Pure helpers for non-streaming response shaping and headers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from systems.federation.common.types import FederatedGateway


def extract_remote_headers(response: Any) -> dict[str, str]:
    """Extract and namespace headers from remote httpx.Response."""
    headers_to_preserve = [
        "x-request-id",
        "x-correlation-id",
        "x-response-time-ms",
        "x-model-id",
        "x-gateway-id",
    ]
    response_headers: dict[str, str] = {}

    for header in headers_to_preserve:
        if header in response.headers:
            namespaced = (
                f"x-federated-{header[2:]}" if header.startswith("x-") else header
            )
            response_headers[namespaced] = response.headers[header]

    return response_headers


def prepare_federation_headers(
    fed_gateway: FederatedGateway,
    base_headers: dict[str, str] | None = None,
) -> dict[str, str]:
    """Prepare response headers with federation metadata."""
    headers = (base_headers or {}).copy()
    headers["x-federation-source"] = fed_gateway.remote_stargate_id
    headers["x-federation-gateway"] = fed_gateway.gateway_id
    return headers


def apply_content_filter_to_response(
    response_content: dict[str, Any],
    content_filter: Any,
    model_name: str,
    logger: Any,
) -> dict[str, Any]:
    """Apply optional content filtering to a chat completion response."""
    if not content_filter or not isinstance(response_content, dict):
        return response_content

    if "choices" in response_content:
        choices = response_content.get("choices", [])
        if choices and "message" in choices[0]:
            content_text = choices[0]["message"].get("content", "")

            if content_text:
                filtered_content = content_filter.filter_content(content_text)

                if filtered_content != content_text:
                    response_content["choices"][0]["message"]["content"] = (
                        filtered_content
                    )
                    logger.info(
                        "Applied analysis filter to federated non-streaming response: %s",
                        model_name,
                    )

    return response_content
