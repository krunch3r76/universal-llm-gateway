"""
Token management for request execution.

Handles token counting, context limit validation, and max_tokens computation.
"""

# TODO: Move to a shared location (e.g. core/ or core/request/); used by both
# streaming and non-streaming paths, so core/nonstreaming/ is misleading.

from dataclasses import dataclass
from typing import Any

from universal_logging import get_logger

from ..errors import TokenErrorBuilder
from .context import RequestContext

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class TokenAllocationPolicy:
    """
    Immutable policy for token allocation decisions.

    Decoupled from TokenManager to allow federated token counting
    without requiring a local gateway URL.

    INVARIANT: ∀ federated_request: allocation_policy ≠ None ∧ policy.safety_buffer ≥ 0

    NOTE: Only safety_buffer needed for federated path. Conservative allocation
    ratio unused (federated logic clamps with available_tokens - safety_buffer).
    Buffer=0 means no safety margin (acceptable for non-production or when clients
    manage their own token budgets).
    """

    safety_buffer: int

    @classmethod
    def from_config(cls, config) -> "TokenAllocationPolicy":
        """
        Create policy from StargateConfig.

        Args:
            config: StargateConfig instance

        Returns:
            TokenAllocationPolicy with values from config (buffer=0 if not set)

        Note:
            If safety_buffer is not configured, defaults to 0. This is intentional:
            - Router-only Master uses authoritative token counts from execution target
            - Buffer is advisory; 0 means "no extra margin"
            - Explicit config encouraged for production use
        """
        token_config = config.get_token_management_config()
        # Default to 0 if not set (predictable, no hidden defaults)
        safety_buffer = token_config.get("safety_buffer", 0)
        return cls(safety_buffer=safety_buffer)

    @classmethod
    def from_token_manager(cls, token_manager) -> "TokenAllocationPolicy":
        """
        Extract policy from existing TokenManager (fail-fast if missing).

        Args:
            token_manager: TokenManager instance

        Returns:
            TokenAllocationPolicy with values from token_manager

        Raises:
            AttributeError: If completion_safety_buffer missing (fail-fast)
        """
        # Fail-fast: no getattr defaults, require explicit attribute
        return cls(safety_buffer=token_manager.completion_safety_buffer)


async def apply_token_management(
    context: RequestContext,
    token_manager,
    http_client,
) -> None:
    """
    Apply token counting and management before inference.

    Args:
        context: Request context with processed messages
        token_manager: Token manager instance
        http_client: HTTP client for token counting requests
    """
    if token_manager is None:
        logger.info("⚠️ TOKEN MANAGEMENT DISABLED - no token manager provided")
        return

    # Skip token counting if requested
    if context.skip_token_counting:
        logger.info(
            f"⏩ TOKEN COUNTING BYPASSED: Client requested skip "
            f"for {context.selected_model}"
        )
        context.middleware_actions.append("token_counting_bypassed")
        return

    # Ensure token manager has HTTP client
    if http_client and token_manager.http_client != http_client:
        await token_manager.set_http_client(http_client)

    # Determine content for token counting
    token_counting_content, token_counting_content_type = (
        _get_content_for_token_counting(
            context.processed_messages, context.transformation_metadata
        )
    )

    tools = context.original_request.get("tools")
    tools_count = len(tools) if tools else 0

    logger.info(
        f"🔍 TOKEN COUNTING CONTENT: type={token_counting_content_type}, "
        f"tools={tools_count}, content={token_counting_content}"
    )
    if token_counting_content is None:
        logger.info("⚠️ NO CONTENT FOR TOKEN COUNTING - skipping")
        return

    user_explicitly_specified_max_tokens = "max_tokens" in context.user_params

    (
        token_metrics,
        final_max_tokens,
    ) = await token_manager.count_tokens_and_compute_generation_space(
        token_counting_content,
        str(context.selected_model),
        context.user_params.get("max_tokens"),
        context.middleware_actions,
        user_explicitly_specified_max_tokens,
        token_counting_content_type,
        gateway_instance=context.selected_gateway_instance,
        sticky=context.model_sticky,
        tools=tools,
    )

    # THREE DISTINCT ERROR SCENARIOS:
    # 1. token_metrics = None → Token counting service failed (503)
    # 2. input_tokens > context_limit → Input too long (400)
    # 3. final_max_tokens = None AND has metrics → No generation space (400)

    if token_metrics is None:
        logger.error(
            f"❌ TOKEN COUNTING SERVICE FAILED: {context.selected_model} "
            f"- service unavailable"
        )
        context.middleware_actions.append(
            f"token_counting_service_failed: {context.selected_model}"
        )
        raise TokenErrorBuilder.service_unavailable(str(context.selected_model))

    # Check if input exceeds context limit
    if token_metrics.input_tokens > token_metrics.context_limit:
        logger.error(
            f"❌ INPUT EXCEEDS CONTEXT LIMIT: {token_metrics.input_tokens} > "
            f"{token_metrics.context_limit} for {context.selected_model}"
        )
        context.middleware_actions.append(
            f"input_exceeds_context_request_failed: "
            f"{token_metrics.input_tokens}/{token_metrics.context_limit}"
        )
        error = TokenErrorBuilder.context_length_exceeded(
            token_metrics, str(context.selected_model), token_counting_content_type
        )
        logger.error(
            f"🚀 RAISING CONTEXT_LENGTH_EXCEEDED: {error.status_code} - {error.detail}"
        )
        raise error

    # Check if no generation space available (but input fits)
    if final_max_tokens is None:
        logger.error(
            f"❌ NO GENERATION SPACE: {context.selected_model} has no tokens available"
        )
        context.middleware_actions.append("no_generation_space_request_failed")
        error = TokenErrorBuilder.insufficient_generation_space(
            token_metrics,
            str(context.selected_model),
            token_manager.completion_safety_buffer if token_manager else 0,
        )
        logger.error(
            f"🚀 RAISING INSUFFICIENT_GENERATION_SPACE: "
            f"{error.status_code} - {error.detail}"
        )
        raise error

    # Normal case: We have generation space
    context.user_params["max_tokens"] = final_max_tokens
    if context.modified_request is None:
        context.modified_request = {}
    context.modified_request["max_tokens"] = final_max_tokens
    if user_explicitly_specified_max_tokens:
        context.middleware_actions.append(
            f"max_tokens_contracted_for_context: "
            f"{context.user_params.get('max_tokens')} → {final_max_tokens}"
        )
    else:
        context.middleware_actions.append(
            f"max_tokens_auto_allocated: {final_max_tokens}"
        )

    context.token_metrics = token_metrics
    logger.info(f"✅ TOKEN MANAGEMENT COMPLETE: final_max_tokens={final_max_tokens}")


def _get_content_for_token_counting(
    messages: list[dict[str, Any]] | str | None,
    metadata: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]] | str | None, str]:
    """Determine content for token counting."""
    if metadata and metadata.get("transformation_applied"):
        prompt_content = metadata.get("prompt_content", "")
        if prompt_content:
            return prompt_content, "prompt"
    return messages, "messages"


def _build_token_payload(
    model_id: str,
    content: list[dict[str, Any]] | str,
    content_type: str,
    tools: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Build token count request payload.

    Args:
        model_id: Model to use for tokenization
        content: Either messages (list[dict]) or prompt (str)
        content_type: "messages" or "prompt" - determines payload key
        tools: Tool definitions — included so chat-template expansion
            accounts for their token cost.

    Returns:
        Payload dict with model, content, and optionally tools
    """
    payload: dict[str, Any] = {"model": model_id}
    if content_type == "prompt":
        payload["prompt"] = content
    else:
        payload["messages"] = content
    if tools:
        payload["tools"] = tools
    return payload


def _compute_federated_max_tokens(
    input_tokens: int,
    context_limit: int,
    user_max_tokens: int | None,
    safety_buffer: int,
) -> int | None:
    """
    Compute final max_tokens for federated request.

    Pure computation: no I/O, no state mutation.

    Args:
        input_tokens: Token count from remote
        context_limit: Model's context window
        user_max_tokens: Client-specified max_tokens (or None)
        safety_buffer: Reserved tokens for safety margin

    Returns:
        Final max_tokens value, or None if no space available

    Raises:
        ValueError: If final_max_tokens <= 0 (insufficient generation space)
    """
    available_tokens = context_limit - input_tokens if context_limit > 0 else None

    if available_tokens is None or available_tokens <= 0:
        return None

    final_max_tokens = min(
        user_max_tokens or available_tokens, available_tokens - safety_buffer
    )

    if final_max_tokens <= 0:
        raise ValueError(
            f"Insufficient generation space: "
            f"available={available_tokens}, buffer={safety_buffer}"
        )

    return final_max_tokens


def _apply_max_tokens_to_context(
    context: "RequestContext",
    final_max_tokens: int,
    user_max_tokens: int | None,
) -> None:
    """
    Mutate context with computed max_tokens.

    Single responsibility: context mutation only.

    Args:
        context: Request context to update
        final_max_tokens: Computed max_tokens value
        user_max_tokens: Original user-specified value (for logging)
    """
    context.user_params["max_tokens"] = final_max_tokens
    if context.modified_request is None:
        context.modified_request = {}
    context.modified_request["max_tokens"] = final_max_tokens

    context.middleware_actions.append(
        f"max_tokens_adjusted_federated: {user_max_tokens or 'auto'} "
        f"→ {final_max_tokens}"
    )


async def apply_federated_token_management(
    context: "RequestContext",
    allocation_policy: TokenAllocationPolicy,
    federated_gateway,
    federation_forwarder,
    event_bus=None,
) -> None:
    """
    Apply token counting for federated requests via Remote Stargate API.

    INVARIANT: ∀ federated_request: token_counting_before_forward
    INVARIANT: uses federation auth headers via forwarder
    INVARIANT (Master): ∀ master_mode_request:
        token_counting_target = execution_target
        execution_target = federated_gateway
        ¬∃ local_gateway ⟹ token_counting via federation_forwarder ONLY
    INVARIANT (Policy): allocation_policy ≠ None (fail-fast if missing)

    For federated requests, token counting happens via Remote Stargate's
    /api/v1/federation/tokens/count endpoint. max_tokens adjustment happens on Master.

    Token counting MUST occur on the same gateway that will execute inference,
    using the exact tokenizer/model instance.

    Args:
        context: Request context with processed messages
        allocation_policy: Token allocation policy (safety_buffer) - REQUIRED
        federated_gateway: FederatedGateway target with remote_stargate_url
        federation_forwarder: Forwarder with auth credentials for remote calls

    Raises:
        HTTPException: If federation_forwarder is None or token counting fails
        TypeError: If allocation_policy is None (coding error, should never happen)
    """
    # Fail-fast: allocation_policy is non-optional
    if allocation_policy is None:
        raise TypeError(
            "BUG: allocation_policy is None - master mode wiring must provide policy"
        )

    if federation_forwarder is None:
        logger.error("❌ FEDERATION FORWARDER NOT AVAILABLE - cannot count tokens")
        context.middleware_actions.append("federated_token_counting_no_forwarder")
        raise TokenErrorBuilder.service_unavailable(
            str(context.selected_model),
            details="Federation forwarder not configured for token counting",
        )

    if context.skip_token_counting:
        logger.info(
            f"⏩ TOKEN COUNTING BYPASSED: Client requested skip for "
            f"federated request to {federated_gateway.gateway_id}"
        )
        context.middleware_actions.append("token_counting_bypassed_federated")
        return

    # Cloud gateways: no remote token API; skip token counting (same effect as bypass).
    if getattr(federated_gateway, "is_cloud", False):
        logger.info(
            f"⏩ TOKEN COUNTING SKIPPED: Cloud gateway {federated_gateway.gateway_id} "
            "(no remote token API)"
        )
        context.middleware_actions.append("token_counting_skipped_cloud")
        return

    # Reuse existing content extraction logic
    token_counting_content, token_counting_content_type = (
        _get_content_for_token_counting(
            context.processed_messages, context.transformation_metadata
        )
    )

    if token_counting_content is None:
        logger.info("⚠️ NO CONTENT FOR TOKEN COUNTING - skipping federated")
        return

    tools = context.original_request.get("tools")
    tools_count = len(tools) if tools else 0

    logger.info(
        f"🔍 FEDERATED TOKEN COUNTING: gateway={federated_gateway.gateway_id} "
        f"type={token_counting_content_type}, tools={tools_count}"
    )

    token_payload = _build_token_payload(
        str(context.selected_model),
        token_counting_content,
        token_counting_content_type,
        tools=tools,
    )

    if event_bus:
        from src.scheduling.events import TokenCountPrecondition

        selected_gateway = context.target_gateway_id
        loaded_on_gateway = context.selected_model in federated_gateway.loaded_models
        known_to_gateway = context.selected_model in federated_gateway.available_models
        legal_reason = (
            "selected_gateway_loaded"
            if loaded_on_gateway
            else (
                "selected_gateway_known_load_or_wait_completed"
                if known_to_gateway
                else "selected_gateway_unknown"
            )
        )
        await event_bus.publish_nowait(
            TokenCountPrecondition(
                request_id=context.request_id,
                model_id=str(context.selected_model),
                target_gateway=federated_gateway.gateway_id,
                selected_gateway=selected_gateway,
                gateway_url=federated_gateway.remote_stargate_url,
                remote_id=federated_gateway.remote_stargate_id,
                sticky=context.model_sticky,
                loaded_on_gateway=loaded_on_gateway,
                known_to_gateway=known_to_gateway,
                skip_requested=context.skip_token_counting,
                legal_reason=legal_reason,
                content_type=token_counting_content_type,
                tools_count=tools_count,
            )
        )

    # Count tokens via Remote Stargate API with federation auth
    try:
        token_response = await federation_forwarder.forward_token_request(
            federated_gateway,
            token_payload,
            request_id=context.request_id,
        )

        # Response uses token_count, context_limit, max_generation_tokens keys
        input_tokens = token_response.get("token_count", 0)
        context_limit = token_response.get("context_limit", 0)

    except Exception as e:
        logger.error(f"❌ Federated token counting failed: {e}")
        context.middleware_actions.append(f"federated_token_counting_failed: {e}")
        raise TokenErrorBuilder.service_unavailable(
            str(context.selected_model), details=f"Federated token counting failed: {e}"
        )

    # Validate context limit
    if context_limit > 0 and input_tokens > context_limit:
        logger.error(
            f"❌ INPUT EXCEEDS CONTEXT LIMIT (federated): {input_tokens} > "
            f"{context_limit} for {context.selected_model}"
        )
        context.middleware_actions.append(
            f"input_exceeds_context_request_failed_federated: "
            f"{input_tokens}/{context_limit}"
        )
        raise TokenErrorBuilder.context_length_exceeded_simple(
            input_tokens, context_limit, str(context.selected_model)
        )

    # Compute and apply max_tokens
    user_max_tokens = context.user_params.get("max_tokens")

    try:
        final_max_tokens = _compute_federated_max_tokens(
            input_tokens,
            context_limit,
            user_max_tokens,
            allocation_policy.safety_buffer,
        )
    except ValueError:
        logger.error(f"❌ NO GENERATION SPACE (federated): {context.selected_model}")
        context.middleware_actions.append("no_generation_space_federated")
        raise TokenErrorBuilder.insufficient_generation_space_simple(
            input_tokens,
            context_limit,
            allocation_policy.safety_buffer,
            str(context.selected_model),
        )

    if final_max_tokens is not None:
        _apply_max_tokens_to_context(context, final_max_tokens, user_max_tokens)
        logger.info(
            f"✅ FEDERATED TOKEN MANAGEMENT: max_tokens={final_max_tokens} "
            f"(input={input_tokens}, limit={context_limit})"
        )
    else:
        logger.warning(
            "⚠️ Could not determine available tokens for federated request - "
            "proceeding without max_tokens adjustment"
        )
        context.middleware_actions.append("federated_token_limit_unknown")
