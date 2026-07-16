"""Federated pseudostream: upstream SSE, client JSON completion.

Master-only buffering. Edge/federation keep SSE passthrough invariants.
Forces ``stream=true`` toward the gateway, accumulates via
``OpenAIChatCompletionsReducer`` + ``accumulate_sse_stream``, returns one
OpenAI ``chat.completion`` JSON body with ``X-ULG-Pseudostream: accumulated``.

Observability (hard proof of upstream SSE):
  - ``X-ULG-Pseudostream-Upstream-Stream: true`` — body forced before forward
  - ``X-ULG-Pseudostream-Sse-Events`` — JSON SSE payloads reduced
  - ``X-ULG-Pseudostream-Delta-Parts`` — ``delta.content`` appends
  - ``X-ULG-Pseudostream-Full-Message-Events`` — ``message.content`` frames
  - ``X-ULG-Pseudostream-Chunk-Objects`` — e.g. ``chat.completion.chunk``
  - snapshot stage ``pseudostream`` when ``STARGATE_DEBUG_REQUEST_SNAPSHOTS``
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException
from fastapi.responses import Response
from llm_adapters.streaming.openai_chat import OpenAIChatCompletionsReducer
from sse.accumulator import accumulate_sse_stream
from sse.protocols import SSEProviderError, SSEStallError, SSETimeoutError
from universal_logging import get_logger
from universal_protocol import ErrorCode, error_envelope

from ....debug.request_snapshots import write_request_snapshot
from .federated_streaming import _execute_federated_streaming

if TYPE_CHECKING:
    from systems.federation.common.config.schema import EndpointCategory
    from systems.federation.common.types import FederatedGateway

    from ..context import RequestContext

logger = get_logger(__name__)

PSEUDOSTREAM_HEADER = "X-ULG-Pseudostream"
PSEUDOSTREAM_HEADER_VALUE = "accumulated"
PSEUDOSTREAM_UPSTREAM_STREAM_HEADER = "X-ULG-Pseudostream-Upstream-Stream"


async def _execute_federated_pseudostream(
    context: RequestContext,
    fed_gateway: FederatedGateway,
    request_body: dict[str, Any],
    request_id: str,
    hop_count: int,
    endpoint_category: EndpointCategory,
    hints: dict[str, Any] | None,
    federation_integration: Any,
    federation_forwarder: Any,
    release_capacity_token: Any,
) -> Response:
    """Upstream stream + master accumulate → JSON chat.completion."""
    streamed_body = {**request_body, "stream": True}
    if streamed_body.get("stream") is not True:
        raise HTTPException(
            status_code=500,
            detail=error_envelope(
                code=ErrorCode.UNEXPECTED_ERROR,
                message="Pseudostream failed to force stream=true on upstream body",
                source="master",
                retryable=False,
                data={"request_id": request_id},
            ),
        )

    await write_request_snapshot(streamed_body, request_id, stage="pseudostream")
    logger.info(
        "📡 [PSEUDO:%s] Upstream body stream=%s (forced) model=%s gateway=%s",
        request_id[:8],
        streamed_body.get("stream"),
        streamed_body.get("model"),
        fed_gateway.gateway_id,
    )

    streaming_resp = await _execute_federated_streaming(
        context,
        fed_gateway,
        streamed_body,
        request_id,
        hop_count,
        endpoint_category,
        hints,
        federation_integration,
        federation_forwarder,
        release_capacity_token,
    )

    async def byte_iter():
        async for chunk in streaming_resp.body_iterator:
            if isinstance(chunk, bytes):
                yield chunk
            elif isinstance(chunk, str):
                yield chunk.encode("utf-8")
            else:
                yield bytes(chunk)

    reducer = OpenAIChatCompletionsReducer()
    try:
        state = await accumulate_sse_stream(
            byte_iter(),
            reducer,
            stall_timeout=OpenAIChatCompletionsReducer.DEFAULT_STALL_TIMEOUT,
            overall_timeout=context.request_timeout_hint,
        )
    except SSEStallError as exc:
        raise HTTPException(
            status_code=504,
            detail=error_envelope(
                code=ErrorCode.INFERENCE_TIMEOUT,
                message=f"Pseudostream SSE stalled: {exc}",
                source="master",
                retryable=True,
                data={"request_id": request_id, "kind": "pseudostream_stall"},
            ),
        ) from exc
    except SSETimeoutError as exc:
        raise HTTPException(
            status_code=504,
            detail=error_envelope(
                code=ErrorCode.INFERENCE_TIMEOUT,
                message=f"Pseudostream SSE overall timeout: {exc}",
                source="master",
                retryable=True,
                data={"request_id": request_id, "kind": "pseudostream_timeout"},
            ),
        ) from exc
    except SSEProviderError as exc:
        raise HTTPException(
            status_code=502,
            detail=error_envelope(
                code=ErrorCode.UNEXPECTED_ERROR,
                message=f"Pseudostream upstream SSE error: {exc}",
                source="master",
                retryable=True,
                data={"request_id": request_id},
            ),
        ) from exc

    body = OpenAIChatCompletionsReducer.to_chat_completion(
        state, model=str(context.selected_model)
    )
    if state.error is not None and not "".join(state.content_parts):
        err = state.error
        raise HTTPException(
            status_code=502,
            detail=error_envelope(
                code=ErrorCode.UNEXPECTED_ERROR,
                message=str(err.get("message") or err),
                source="master",
                retryable=True,
                data={
                    "request_id": request_id,
                    "upstream_error": err,
                    "kind": "pseudostream_empty_or_error",
                },
            ),
        )

    obs = OpenAIChatCompletionsReducer.observability_headers(state)
    logger.info(
        "✅ [PSEUDO:%s] Accumulated %d chars sse_events=%s delta_parts=%s "
        "full_message_events=%s objects=%s saw_done=%s",
        request_id[:8],
        len(body["choices"][0]["message"]["content"]),
        obs.get("X-ULG-Pseudostream-Sse-Events"),
        obs.get("X-ULG-Pseudostream-Delta-Parts"),
        obs.get("X-ULG-Pseudostream-Full-Message-Events"),
        obs.get("X-ULG-Pseudostream-Chunk-Objects"),
        obs.get("X-ULG-Pseudostream-Saw-Done"),
    )
    return Response(
        content=json.dumps(body).encode("utf-8"),
        media_type="application/json",
        headers={
            PSEUDOSTREAM_HEADER: PSEUDOSTREAM_HEADER_VALUE,
            PSEUDOSTREAM_UPSTREAM_STREAM_HEADER: "true",
            **obs,
        },
    )
