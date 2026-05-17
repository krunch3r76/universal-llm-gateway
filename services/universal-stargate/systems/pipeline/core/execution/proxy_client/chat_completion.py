"""ProxyClient chat_completion operation mixin.

Implements the primary model invocation path: builds Stargate request,
applies profile controls, manages active request count, handles
timeouts with diagnostics, and normalizes HTTP errors.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

import httpx

from .errors import (
    ProxyClientError,
    _error_message,
    _raise_httpx_transport_error,
)

if TYPE_CHECKING:
    from .configuration import ProxyClientConfig


class _ProxyChatCompletion:
    """Mixin providing the chat_completion coroutine for ProxyClient."""

    _config: ProxyClientConfig
    _active_requests: int

    async def chat_completion(
        self,
        model: str,
        messages: list[dict[str, Any]],
        *,
        execution_id: str | None = None,
        step_id: str | None = None,
        skip_token_counting: bool = False,
        disable_profile: bool = True,
        profile: str | None = None,
        timeout: float | None = None,
        map_iteration_request_id: str | None = None,
        request_id: str | None = None,
        **params: Any,
    ) -> tuple[dict[str, Any], str, str]:
        """
        Execute chat completion via Stargate.

        Args:
            model: Model identifier
            messages: Chat messages
            execution_id: Pipeline execution ID (for tracing)
            step_id: Pipeline step ID (for tracing)
            skip_token_counting: Skip pre-request token counting
                (default: False — token counting runs for slot-aware max_tokens)
            disable_profile: Suppress model-assigned profile injection (default: True
                for this method — caller should pass the effective value resolved from
                step/pipeline options, which defaults to False). Set True to skip all
                profile logic; set False (default pipeline behavior) to allow the
                model's assigned profile (e.g. "gemma4-instruct") to apply.
            profile: Explicit profile to apply (overrides model assignment).
                Passed as ?filter= query param. Takes effect only when
                disable_profile=False, or forces the named profile when set.
            timeout: Request timeout (overrides default)
            map_iteration_request_id: Pre-generated per-iteration request ID
                for cancellation tracking. If None, generates new UUID.
            request_id: Pre-generated unique request ID for capacity tracking.
                Becomes X-Internal-Request-ID and context.request_id in Stargate.
                If None, generates new UUID. Used by MapExecutor to correlate
                request.processing events before the HTTP call completes.
            **params: Additional OpenAI-compatible parameters
                (temperature, max_tokens, response_format, etc.)

        Returns:
            Tuple of (response_dict, map_iteration_request_id,
            snapshot_request_id). snapshot_request_id is the per-call
            unique ID used as X-Internal-Request-ID — matches the
            request/response snapshot filenames.

        Raises:
            ProxyClientError: On request failure
        """
        client = await self._ensure_client()

        # Use provided map_iteration_request_id or generate new
        if map_iteration_request_id is None:
            map_iteration_request_id = str(uuid.uuid4())

        # CRITICAL: Each call needs its own capacity slot. Handlers like
        # sub_decompose_individual make N parallel calls per iteration; shared
        # request_id → N capacity releases for 1 acquisition → livelock.
        # Pre-generated request_id is only used for the first call of an
        # iteration (for request.processing event correlation); subsequent
        # calls in the same iteration generate fresh UUIDs.
        unique_request_id = request_id or str(uuid.uuid4())

        # Build request body (stream=False enforced after merge — pipeline invariant)
        request_body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            **params,
        }
        request_body["stream"] = False

        request_headers = self._build_request_headers(
            execution_id,
            step_id,
            skip_token_counting,
            timeout,
            request_id=unique_request_id,
            cancel_group=map_iteration_request_id,
        )

        # Build query params for profile control.
        # disable_profile and profile (alias: filter) are Stargate-only query params
        # — they control profile application without entering the forwarded body.
        # When profile is set, do not pass disable_profile — Stargate skips all
        # profile logic when disable_profile=true, so the explicit profile would
        # be ignored.
        query_params: dict[str, str] = {}
        if disable_profile and not profile:
            query_params["disable_profile"] = "true"
        if profile:
            query_params["filter"] = profile

        # Apply timeout override if specified
        request_timeout = timeout or self._config.request_timeout

        self._active_requests += 1
        try:
            response = await client.post(
                "/v1/chat/completions",
                json=request_body,
                headers=request_headers,
                params=query_params,
                timeout=request_timeout,
            )

            if response.status_code >= 400:
                # Parse error detail if available
                try:
                    error_body = response.json()
                    detail = error_body.get("detail", error_body)
                except Exception:
                    detail = response.text

                raise ProxyClientError(
                    _error_message(response.status_code, detail),
                    status_code=response.status_code,
                    detail=detail,
                )

            return response.json(), map_iteration_request_id, unique_request_id

        except httpx.TimeoutException as e:
            await self._raise_timeout_error(
                endpoint="/v1/chat/completions",
                request_timeout=request_timeout,
                request_body=request_body,
                request_headers=request_headers,
                execution_id=execution_id,
                step_id=step_id,
                exception=e,
                request_kind="chat",
            )
        except httpx.HTTPError as e:
            _raise_httpx_transport_error(e)

        finally:
            self._active_requests -= 1
