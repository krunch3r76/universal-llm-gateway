"""
Main TokenManager class - orchestrates token counting and allocation.
Refactored to use dedicated modules for allocation and counting.
"""

# ruff: noqa: E501

import time as time_module
from typing import TYPE_CHECKING, Any, Optional

import httpx
from universal_logging import format_json_for_log, get_logger

# Remove import - truncation now automatic

if TYPE_CHECKING:
    from gateways import GatewayInstance

from src.schemas.tokens import Message, TokenCountRequest, TokenMetrics

from ..stargate_config import StargateConfig
from . import allocation
from .token_counting import count_tokens

logger = get_logger(__name__)


class TokenManager:
    """
    Token management for preventing context length overruns.

    The primary purpose of this class is to count input tokens and return a feasible
    max_tokens value so that the total (input + generation) does not exceed the model's
    context_length. This prevents context overruns that would cause the model's response to be cut short.

    Architecture (Event-Driven Model Load Waiting):
    - Uses ModelLoadWaiter for event-driven waiting when model not yet loaded
    - WebSocket MODEL_LOADED events trigger immediate continuation
    - No polling - instant notification when model becomes available
    - Falls back gracefully if ModelLoadWaiter not configured

    Strategy:
        Real-time token counting via gateway API. If token counting fails, returns None, None
        to indicate failure to the caller.

    The system handles various failure scenarios:
        - HTTP client not initialized/closed
        - Model not loaded for token counting (waits via events)
        - Token counting API failures/timeouts
        - Network connectivity issues

    All decisions are logged and tracked via middleware_actions for debugging.
    """

    def __init__(
        self, gateway_url: str, config: StargateConfig | None = None, event_bus=None
    ):
        self.config = config or StargateConfig()
        token_config = self.config.get_token_management_config()

        self.gateway_url = gateway_url
        self.completion_safety_buffer = token_config.get("safety_buffer", 128)
        self.conservative_allocation_ratio = token_config.get(
            "conservative_allocation_ratio", 0.95
        )
        self.token_endpoint_url = token_config.get(
            "gateway_endpoint", f"{gateway_url}/api/v1/tokens/count"
        )
        self.event_bus = event_bus

        # Single request timeout for model loading
        self.wait_for_model_loading = token_config.get("wait_for_model_loading", True)
        self.token_counting_timeout = token_config.get("token_counting_timeout", 300)

        # Initialize HTTP client reference
        self.http_client = None

        # Event-driven load waiter (set via set_load_waiter after initialization)
        self._load_waiter = None

    async def set_http_client(self, http_client: httpx.AsyncClient):
        """Set the HTTP client for token counting requests"""
        logger.debug(
            f"🔄 TokenManager: Updating HTTP client reference (old: {id(self.http_client) if self.http_client else None}, new: {id(http_client)})"
        )
        self.http_client = http_client

    def set_load_waiter(self, load_waiter) -> None:
        """Set the ModelLoadWaiter for event-driven model load waiting."""
        self._load_waiter = load_waiter
        logger.debug("TokenManager: ModelLoadWaiter reference set")

    async def count_tokens_and_compute_generation_space(
        self,
        content: list[dict[str, Any]] | str,
        model_name: str,
        user_requested_max_tokens: int | None,
        middleware_actions: list[str],
        user_explicitly_specified_max_tokens: bool,
        content_type: str = "messages",
        gateway_instance: Optional["GatewayInstance"] = None,
        *,
        sticky: bool = True,
        tools: list[dict[str, Any]] | None = None,
    ) -> tuple[TokenMetrics | None, int | None]:
        """
        Calculate input tokens and subsequent generation space with conservative allocation.

        This function's primary purpose is to estimate input token count and calculate the
        maximum safe generation tokens available, then apply conservative allocation strategies
        to prevent context overruns. It attempts real-time token counting first, then falls
        back to indicating failure to the caller.

        The conservative allocation approach includes:
        - Safety buffers to prevent context overruns
        - Reduced allocation ratios (75% of available space by default)
        - User request validation and capping
        - Clear indication of token counting success/failure

        Args:
            content: Input content to count tokens for. Can be a list of message dicts
                    (OpenAI format) or a raw string prompt.
            model_name: Name of the target model for token counting.
            user_requested_max_tokens: Maximum tokens requested by the user (if any).
            middleware_actions: List to track middleware actions and decisions for logging.
            user_explicitly_specified_max_tokens: Whether user explicitly set max_tokens.
            content_type: Type of content - "messages" for chat format, "prompt" for raw text.
            tools: Tool definitions from the client request. Forwarded to the
                tokenizer so chat-template expansion accounts for their token cost.

        Returns:
            Tuple of (TokenMetrics, final_max_tokens):
            - TokenMetrics: Detailed token analysis including input count, limits, and safety info
            - final_max_tokens: Calculated max_tokens to use for generation (None if no allocation)

        Raises:
            No exceptions raised - all errors are handled gracefully.
        """

        # (a) Log initial request details for debugging
        if content_type == "messages":
            content_count = len(content) if isinstance(content, list) else 0
            logger.debug(
                f"🔢 TOKEN COUNTING START: model={model_name}, user_max_tokens={user_requested_max_tokens}, messages_count={content_count}"
            )
        else:
            content_length = len(content) if isinstance(content, str) else 0
            logger.debug(
                f"🔢 TOKEN COUNTING START: model={model_name}, user_max_tokens={user_requested_max_tokens}, prompt_length={content_length}"
            )

        # (b) Verify prerequisites and select gateway
        if gateway_instance:
            http_client = gateway_instance.client.get_http_client()
            gateway_url = gateway_instance.config.base_url
            token_endpoint_url = f"{gateway_url}/api/v1/tokens/count"
            logger.debug(
                f"🔀 Using selected gateway: {gateway_instance.config.name} ({gateway_url})"
            )
        else:
            http_client = self.http_client
            gateway_url = self.gateway_url
            token_endpoint_url = self.token_endpoint_url

        if http_client is None:
            logger.error("❌ TOKEN COUNTING ERROR: HTTP client not initialized")
            middleware_actions.append("token_count_error: http_client_not_initialized")
            return None, None

        # No model verification needed - select_gateway_and_load_model already waited
        # for model to load. Token counting API call will fail gracefully if model
        # not ready. This prevents redundant verification that can timeout and block.
        logger.debug(
            f"🔍 Token counting for {model_name} (model verification already done "
            f"in routing phase)"
        )

        # Prepare token count request
        if content_type == "messages":
            # Convert message dicts to typed Message objects for proper validation
            messages = (
                [Message.model_validate(msg) for msg in content]
                if isinstance(content, list)
                else None
            )
            token_count_request = TokenCountRequest(
                messages=messages,
                prompt=None,
                model_name=model_name,
                tools=tools,
            )
        else:
            token_count_request = TokenCountRequest(
                messages=None,
                prompt=str(content),
                model_name=model_name,
                tools=tools,
            )

        # (c) Try primary token count
        logger.debug(
            f"🌐 TOKEN COUNTING REQUEST: POST {token_endpoint_url} (timeout: {self.token_counting_timeout}s)"
        )
        # CRITICAL: Use exclude_unset=True to show only what client actually sent
        payload = token_count_request.model_dump(exclude_unset=True)
        logger.debug(
            f"📤 Token count request payload: {format_json_for_log(payload)}"  # Unicode + automatic truncation
        )

        result = await count_tokens(
            http_client,
            token_endpoint_url,
            token_count_request,
            self.token_counting_timeout,
        )

        logger.debug(
            f"📥 TOKEN COUNTING RESPONSE: success={result.success}, elapsed={result.elapsed_seconds:.1f}s"
        )

        # (d) Compute final max_tokens
        if result.success:
            logger.debug(
                f"✅ TOKEN COUNTING SUCCESS: model loaded and responded after {result.elapsed_seconds:.1f}s"
            )
            middleware_actions.append(
                f"token_counting_success: {result.elapsed_seconds:.1f}s"
            )

            logger.debug(
                f"📊 TOKEN ANALYSIS: input_tokens={result.input_tokens}, context_limit={result.context_limit}, generation_space={result.raw_generation_space}"
            )

            # CRITICAL: Check if input exceeds context limit
            # This happens when raw_generation_space is negative or zero AND input_tokens > context_limit
            if (
                result.input_tokens is not None
                and result.context_limit is not None
                and result.input_tokens > result.context_limit
            ):
                logger.error(
                    f"❌ INPUT EXCEEDS CONTEXT: input_tokens={result.input_tokens} > context_limit={result.context_limit}"
                )
                middleware_actions.append(
                    f"input_exceeds_context: {result.input_tokens}/{result.context_limit}"
                )

                # Return metrics to allow caller to provide detailed error
                token_metrics = TokenMetrics(
                    input_tokens=result.input_tokens,
                    max_tokens_requested=(
                        user_requested_max_tokens
                        if user_requested_max_tokens is not None
                        else 0
                    ),
                    max_tokens_adjusted=0,
                    context_limit=result.context_limit,
                    max_tokens_absolute=0,
                    safety_buffer=self.completion_safety_buffer,
                )
                return token_metrics, None

            # CRITICAL: Check if input exactly fits (no generation space remaining)
            # This happens when raw_generation_space <= safety_buffer
            if (
                result.raw_generation_space is not None
                and result.raw_generation_space <= self.completion_safety_buffer
            ):
                logger.error(
                    f"❌ NO GENERATION SPACE: input_tokens={result.input_tokens}, context_limit={result.context_limit}, raw_space={result.raw_generation_space}"
                )
                middleware_actions.append(
                    f"no_generation_space: {result.input_tokens}/{result.context_limit}"
                )

                # Return metrics to allow caller to provide detailed error (safe to assert non-None here)
                token_metrics = TokenMetrics(
                    input_tokens=result.input_tokens or 0,
                    max_tokens_requested=(
                        user_requested_max_tokens
                        if user_requested_max_tokens is not None
                        else 0
                    ),
                    max_tokens_adjusted=0,
                    context_limit=result.context_limit or 0,
                    max_tokens_absolute=0,
                    safety_buffer=self.completion_safety_buffer,
                )
                return token_metrics, None

            # Apply safety buffer (only reached if we have generation space)
            # Ensure we have valid raw_generation_space
            if result.raw_generation_space is None:
                logger.error(
                    "❌ INVALID TOKEN COUNT RESULT: raw_generation_space is None despite success=True"
                )
                middleware_actions.append("invalid_token_count_result")
                return None, None

            available_generation_space = allocation.apply_safety_buffer(
                result.raw_generation_space, self.completion_safety_buffer
            )
            logger.debug(
                f"🛡️ SAFETY BUFFER APPLIED: buffer={self.completion_safety_buffer}, available_space={available_generation_space}"
            )

            # Compute final max_tokens
            final_max_tokens = allocation.compute_final_max_tokens(
                available_generation_space,
                user_requested_max_tokens,
                user_explicitly_specified_max_tokens,
                self.conservative_allocation_ratio,
            )

            # Log allocation decision
            if (
                user_explicitly_specified_max_tokens
                and user_requested_max_tokens is not None
            ):
                logger.debug(
                    f"👤 USER MAX_TOKENS MODE: requested={user_requested_max_tokens}, final={final_max_tokens}"
                )
                if (
                    final_max_tokens is not None
                    and final_max_tokens != user_requested_max_tokens
                ):
                    reduction = user_requested_max_tokens - final_max_tokens
                    logger.warning(
                        f"⚠️ CONTRACTED USER MAX_TOKENS: {user_requested_max_tokens} → {final_max_tokens} (reduced by {reduction})"
                    )
                elif final_max_tokens is not None:
                    logger.debug(
                        "✅ USER MAX_TOKENS UNCHANGED: sufficient space available"
                    )
                else:
                    logger.warning(
                        "⚠️ USER MAX_TOKENS FAILED: no generation space available"
                    )
            else:
                if final_max_tokens is not None:
                    logger.debug(
                        f"🤖 AUTO-ALLOCATION MODE: calculated={final_max_tokens} ({int(self.conservative_allocation_ratio * 100)}% of {available_generation_space})"
                    )
                else:
                    logger.warning(
                        "⚠️ AUTO-ALLOCATION SKIPPED: no available generation space"
                    )

            logger.debug(
                f"🎯 TOKEN MANAGEMENT COMPLETE: final_max_tokens={final_max_tokens}"
            )

            # (e) Return metrics
            # Ensure we have valid token count data
            if result.input_tokens is None or result.context_limit is None:
                logger.error(
                    f"❌ INVALID TOKEN COUNT RESULT: input_tokens={result.input_tokens}, context_limit={result.context_limit}"
                )
                middleware_actions.append("invalid_token_count_result_missing_data")
                return None, None

            token_metrics = TokenMetrics(
                input_tokens=result.input_tokens,
                max_tokens_requested=(
                    user_requested_max_tokens
                    if user_requested_max_tokens is not None
                    else 0
                ),
                max_tokens_adjusted=(
                    final_max_tokens if final_max_tokens is not None else 0
                ),
                context_limit=result.context_limit,
                max_tokens_absolute=available_generation_space,
                safety_buffer=self.completion_safety_buffer,
            )

            # Emit TOKEN_COUNT_COMPLETED event (fire-and-forget metric)
            if self.event_bus and gateway_instance:
                try:
                    from src.scheduling.events import TokenCountCompleted

                    await self.event_bus.publish_nowait(
                        TokenCountCompleted(
                            request_id="unknown",  # Request ID not available in this context
                            model_id=model_name,
                            gateway_url=gateway_instance.config.base_url,
                            timestamp=time_module.time(),
                            success=True,
                            count_time_ms=result.elapsed_seconds * 1000,
                            input_tokens=result.input_tokens,
                            context_limit=result.context_limit,
                            allocated_max_tokens=final_max_tokens,
                            error=None,
                        )
                    )
                except Exception as e:
                    logger.debug(f"Failed to emit TOKEN_COUNT_COMPLETED event: {e}")

            return token_metrics, final_max_tokens

        # Handle failures
        if result.error_message == "http_client_closed":
            logger.error("❌ TOKEN COUNTING ERROR: HTTP client is closed")
            middleware_actions.append("token_count_error: http_client_closed")
            return None, None
        elif result.error_message == "timeout":
            logger.warning(
                f"⏰ TOKEN COUNTING TIMEOUT: model took longer than {self.token_counting_timeout}s to load (elapsed: {result.elapsed_seconds:.1f}s)"
            )
            middleware_actions.append(
                f"token_counting_timeout: {result.elapsed_seconds:.1f}s"
            )
            return None, None
        elif result.status_code is not None:
            logger.error(
                f"❌ TOKEN COUNTING FAILED: status={result.status_code}, response={result.error_message}"
            )
            middleware_actions.append(f"token_count_failed: {result.status_code}")
            return None, None
        else:
            logger.error(
                f"💥 TOKEN COUNTING EXCEPTION: {result.error_message} (elapsed: {result.elapsed_seconds:.1f}s)"
            )
            middleware_actions.append(f"token_count_error: {result.error_message}")
            return None, None
