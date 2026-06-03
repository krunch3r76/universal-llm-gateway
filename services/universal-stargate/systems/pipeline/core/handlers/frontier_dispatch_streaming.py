"""SSE streaming closures for ``frontier_dispatch_v1``.

Extracted from ``frontier_dispatch.py`` to keep that module under the SLOC
ceiling.  Three public factory functions build the callables that
``FrontierDispatchHandler.execute`` passes to ``run_native_tool_loop``:

- ``build_in_process_sender`` — the ``send_native`` callable; streams provider-
  native SSE via the in-process cloud forwarder and reduces to a terminal dict.
- ``build_cancel_check`` — polls the dispatch tracker for external cancellation.
- ``build_on_tool_event`` — translates ``agent_seat.native_loop`` tool-event
  signals into Stargate event-bus events.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .protocol import PipelineContext

DISPATCH_STALL_TIMEOUT_S = 180.0

# Wall-clock ceiling for a single remote-MCP provider call. The provider runs
# the MCP tool loop server-side and keeps the SSE stream alive with periodic
# ``ping`` events, which reset ``stall_timeout`` on every frame (see
# sse/accumulator.py). With no overall ceiling a server-side loop that makes no
# token progress (e.g. the provider's MCP connector fails to reach/auth the
# vortex endpoint) hangs indefinitely with 0 tokens and no terminal event —
# observed on execution 0bca04f6 (claude-web thread 1191). The wall-clock cap
# converts that silent hang into a loud SSETimeoutError. Callers may override
# via the step's ``timeout_seconds`` option for legitimately long consults.
REMOTE_MCP_OVERALL_TIMEOUT_S = 300.0


def build_cancel_check(context: PipelineContext) -> Callable[[], bool]:
    """Poll the dispatch tracker for cancellation at tool-loop boundaries."""
    execution_id = context.execution_id
    proxy = getattr(context, "_proxy", None)

    def _check() -> bool:
        if proxy is None:
            return False
        tracker = getattr(proxy, "pipeline_dispatch_tracker", None)
        if tracker is None:
            return False
        record = tracker.get(execution_id)
        if record is None:
            return False
        return record.status == "cancelled"

    return _check


def build_on_tool_event(
    context: PipelineContext,
    agent: str | None,
    publish: Callable[[object], None],
) -> Callable[[str, dict[str, Any]], None]:
    """Translate lib-emitted tool events to Stargate event-bus factories."""
    from ..events.dispatch import (
        PipelineFrontierDispatchToolCalled,
        PipelineFrontierDispatchToolFailed,
    )

    execution_id = context.execution_id

    def _on(signal: str, payload: dict[str, Any]) -> None:
        provider = str(payload.get("provider", ""))
        if signal == "pipeline.frontier.dispatch.tool.called":
            event: Any = PipelineFrontierDispatchToolCalled(
                agent=agent,
                execution_id=execution_id,
                tool_name=str(payload.get("tool_name", "")),
                turn=int(payload.get("turn", 0)),
                elapsed_ms=float(payload.get("elapsed_ms", 0.0)),
                provider=provider,
            )
        else:
            # Richer failure event for better observability. Do not retry the
            # exact same (tool_name, arguments) combination on deterministic errors.
            event = PipelineFrontierDispatchToolFailed(
                agent=agent,
                execution_id=execution_id,
                tool_name=str(payload.get("tool_name", "")),
                turn=int(payload.get("turn", 0)),
                elapsed_ms=float(payload.get("elapsed_ms", 0.0)),
                error=str(payload.get("error", "tool returned error envelope")),
                provider=provider,
                arguments=payload.get("arguments"),
                full_error=payload.get("full_error"),
                retry_count=int(payload.get("retry_count", 0)),
            )
        publish(event)

    return _on


def build_in_process_sender(
    context: PipelineContext,
    step_id: str,
    agent: str | None,
    publish: Callable[[object], None],
    cancel_check: Callable[[], bool],
    default_overall_timeout: float | None = None,
) -> Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]:
    """Construct the ``send_native`` closure for ``run_native_tool_loop``.

    Uses Stargate's in-process cloud forwarder to stream provider-native SSE
    and reduce it to the terminal response dict.  Reducer is selected by
    provider extracted from the request path:
      anthropic → AnthropicReducer
      openai / xai → OpenAIResponsesReducer
      google → GoogleStreamReducer
    """
    import httpx
    from llm_adapters.streaming.anthropic import AnthropicReducer
    from llm_adapters.streaming.google import GoogleStreamReducer
    from llm_adapters.streaming.openai import OpenAIResponsesReducer
    from sse.accumulator import accumulate_sse_stream
    from sse.protocols import SSEMessage as _SSEMsg
    from sse.protocols import SSEProviderError, SSEStallError, SSETimeoutError

    from systems.proxy.routers.cloud_passthrough import _get_cloud_forwarder

    from ..events.dispatch import PipelineFrontierDispatchToolRequested

    execution_id = context.execution_id
    # An explicit per-step ``timeout_seconds`` wins; otherwise fall back to the
    # caller-supplied default (set for remote-MCP dispatches so a token-stalled
    # server-side loop fails loudly rather than hanging behind ping keepalives).
    pipeline_timeout: float | None = default_overall_timeout
    step_opts = getattr(context, "options", None)
    if step_opts and isinstance(step_opts, dict):
        raw_timeout = step_opts.get("timeout_seconds")
        if isinstance(raw_timeout, int | float) and raw_timeout > 0:
            pipeline_timeout = float(raw_timeout)
    headers = {
        "X-Pipeline-Internal": "true",
        "X-Pipeline-Execution-Id": execution_id,
        "X-Pipeline-Step-Id": step_id,
    }

    def _provider_from_path(path: str) -> str:
        for segment in ("anthropic", "openai", "xai", "google"):
            if f"/{segment}/" in path:
                return segment
        return "unknown"

    def _make_reducer(
        provider: str,
    ) -> AnthropicReducer | OpenAIResponsesReducer | GoogleStreamReducer:
        if provider in ("openai", "xai"):
            return OpenAIResponsesReducer()
        if provider == "google":
            return GoogleStreamReducer()
        return AnthropicReducer()

    def _make_on_event(provider: str) -> Callable[[_SSEMsg, object], None]:
        if provider in ("openai", "xai"):

            def _on_event_openai(event: _SSEMsg, state: object) -> None:
                if event.event != "response.output_item.added":
                    return
                try:
                    payload = json.loads(event.data or "{}")
                except json.JSONDecodeError:
                    return
                item = payload.get("item") or {}
                if item.get("type") != "function_call":
                    return
                tool_name = str(item.get("name") or "")
                raw_id = item.get("id") or item.get("call_id")
                tool_call_id = str(raw_id) if raw_id else None
                publish(
                    PipelineFrontierDispatchToolRequested(
                        agent=agent,
                        execution_id=execution_id,
                        tool_name=tool_name,
                        provider=provider,
                        tool_call_id=tool_call_id,
                    )
                )

            return _on_event_openai

        if provider == "google":

            def _on_event_google(event: _SSEMsg, state: object) -> None:
                del state
                try:
                    payload = json.loads(event.data or "{}")
                except json.JSONDecodeError:
                    return
                candidates = payload.get("candidates") or []
                if not candidates:
                    return
                candidate = candidates[0]
                if not isinstance(candidate, dict):
                    return
                parts = (candidate.get("content") or {}).get("parts") or []
                for part in parts:
                    if not isinstance(part, dict):
                        continue
                    fc = part.get("functionCall")
                    if not isinstance(fc, dict):
                        continue
                    tool_name = str(fc.get("name") or "")
                    publish(
                        PipelineFrontierDispatchToolRequested(
                            agent=agent,
                            execution_id=execution_id,
                            tool_name=tool_name,
                            provider="google",
                            tool_call_id=None,
                        )
                    )

            return _on_event_google

        def _on_event_anthropic(event: _SSEMsg, state: object) -> None:
            del state
            if event.event != "content_block_start":
                return
            try:
                payload = json.loads(event.data or "{}")
            except json.JSONDecodeError:
                return
            block = payload.get("content_block") or {}
            if block.get("type") != "tool_use":
                return
            tool_name = str(block.get("name") or "")
            raw_id = block.get("id")
            tool_call_id = str(raw_id) if raw_id else None
            publish(
                PipelineFrontierDispatchToolRequested(
                    agent=agent,
                    execution_id=execution_id,
                    tool_name=tool_name,
                    provider="anthropic",
                    tool_call_id=tool_call_id,
                )
            )

        return _on_event_anthropic

    async def _send(path: str, json_body: dict[str, Any]) -> dict[str, Any]:
        client = _get_cloud_forwarder()
        if client is None:
            raise RuntimeError(
                "cloud_forwarder unavailable — Stargate proxy not initialized"
            )
        provider = _provider_from_path(path)
        reducer = _make_reducer(provider)
        on_event = _make_on_event(provider)

        streaming_body = {**json_body, "stream": True}
        byte_iter = client.stream_provider_native(path, streaming_body, headers=headers)

        try:
            try:
                state = await accumulate_sse_stream(
                    byte_iter,
                    reducer,
                    on_event=on_event,
                    stall_timeout=DISPATCH_STALL_TIMEOUT_S,
                    overall_timeout=pipeline_timeout,
                    cancel_check=cancel_check,
                )
            except (SSEStallError, SSETimeoutError) as e:
                raise RuntimeError(f"SSE stream liveness failure: {e}") from e
            except SSEProviderError as e:
                raise RuntimeError(f"{provider} stream provider error: {e}") from e
            except httpx.HTTPStatusError as e:
                preview = (e.response.text or "")[:500] if e.response else ""
                raise RuntimeError(
                    f"provider-native {e.response.status_code}: {preview}"
                ) from e
            except httpx.RemoteProtocolError as e:
                raise RuntimeError(f"stream closed prematurely: {e}") from e
        finally:
            await byte_iter.aclose()

        return type(reducer).to_terminal_dict(state)

    return _send
