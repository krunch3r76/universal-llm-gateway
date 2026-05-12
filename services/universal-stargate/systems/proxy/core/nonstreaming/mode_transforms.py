"""Mode-specific request transformation functions.

Extracted from RequestPreparer so each preparation mode is a standalone
function rather than a method on a god-class.  Two modes exist:

- **Normal mode** (prepare_normal_mode): full transformation pipeline —
  fetches model config, resolves profiles, applies prompt templates and
  input schema conversion, builds the final request payload.
- **Master mode** (prepare_master_mode): client-facing policy only (profiles,
  system prompts, generation params).  Model-specific transformations are
  deferred to the execution target (Edge/Gateway).

Helper functions (extract_messages, inject_profile_system_prompt, etc.)
are shared between modes and are usable independently for testing.
"""
# ruff: noqa: E501

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import HTTPException
from universal_logging import get_logger

from ...utils.model_metadata_helpers import (
    extract_input_schema,
    metadata_to_profile_dict,
)
from .context import RequestContext
from .sticky_routing import resolve_model_sticky

if TYPE_CHECKING:
    from .preparer import RequestPreparer

logger = get_logger(__name__)


def extract_messages(context: RequestContext, transformer: Any) -> list[dict[str, Any]]:
    """Extract the message sequence from a RequestContext into a list of plain dicts.

    If the request uses the legacy ``prompt`` field, it is preprocessed
    (FIM markers removed) and wrapped as a single user message.  Otherwise,
    ``chat_request.messages`` are converted to ``{role, content, ...}`` dicts,
    preserving tool_calls, tool_call_id, and name extras when present.
    """
    if context.chat_request and context.chat_request.prompt:
        cleaned_prompt = transformer.preprocess_prompt_field(
            str(context.chat_request.prompt)
        )
        context.middleware_actions.append("prompt_field_processed_to_messages")
        return [{"role": "user", "content": cleaned_prompt}]

    messages_list = (
        context.chat_request.messages
        if context.chat_request and context.chat_request.messages
        else []
    )
    result: list[dict[str, Any]] = []
    for msg in messages_list:
        d: dict[str, Any] = {"role": msg.role, "content": msg.content}
        extras = msg.model_extra or {}
        for key in ("tool_calls", "tool_call_id", "name"):
            if key in extras:
                d[key] = extras[key]
        result.append(d)
    return result


def inject_profile_system_prompt(
    messages: list[dict[str, Any]],
    system_prompt: str,
    profile_name: str,
    context: RequestContext,
) -> list[dict[str, Any]]:
    """Prepend the profile's system prompt to the message list, unless the
    client already supplied a system message (in which case it is preserved).

    Records the decision in ``context.middleware_actions`` for auditing:
    either ``system_message_preserved_no_profile_override`` or
    ``system_prompt_injected_from_profile:{name}``.
    """
    for msg in messages:
        if msg.get("role") == "system":
            logger.info(
                "Preserving existing system message (user-provided): '%s...'",
                msg.get("content", "")[:50],
            )
            context.middleware_actions.append(
                "system_message_preserved_no_profile_override"
            )
            return messages

    messages_with_system = [{"role": "system", "content": system_prompt}] + messages

    logger.info(
        "✅ Injected profile system prompt from '%s': %d characters",
        profile_name,
        len(system_prompt),
    )
    context.middleware_actions.append(
        f"system_prompt_injected_from_profile:{profile_name}"
    )

    return messages_with_system


def extract_and_filter_messages(
    preparer: RequestPreparer, context: RequestContext
) -> list[dict[str, Any]]:
    """Extract messages from context and apply content filters for Master mode.

    Combines extract_messages (prompt→messages conversion) with the
    TransformationEngine's filter-only pass (blocklist, length limits)
    without applying model-specific prompt templates — those are deferred
    to the execution target in federation forwarding.
    """
    original_messages = extract_messages(context, preparer.transformer)
    return preparer._transformation_engine.apply_filters_only(
        original_messages, context.selected_model
    )


def build_federation_forward_payload(
    preparer: RequestPreparer,
    context: RequestContext,
    filtered_messages: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build request payload for federation forwarding (Master mode).

    Applies client-facing policy (generation_params, profile params) but
    defers model-specific transformations (prompt templates, input schemas)
    to the execution target.
    """
    request_data = context.original_request.copy()
    request_data["messages"] = filtered_messages

    if "prompt" in request_data:
        del request_data["prompt"]

    transform_config = preparer._transformation_engine.get_config_for_model(
        context.selected_model
    )
    if transform_config:
        settings = transform_config.get("settings", {})
        if "generation_params" in settings:
            gen_params = settings["generation_params"]
            applied_params = []
            for param_name, param_value in gen_params.items():
                if param_name not in request_data:
                    request_data[param_name] = param_value
                    applied_params.append(param_name)
            if applied_params:
                logger.info(
                    "Applied generation params in master mode: %s", applied_params
                )

    if hasattr(context, "profile_data") and context.profile_data:
        for key, value in context.profile_data.params.items():
            if key not in context.user_params and key not in request_data:
                request_data[key] = value

    return request_data


async def prepare_master_mode(
    preparer: RequestPreparer, context: RequestContext
) -> None:
    """Prepare a request on the Master (no local gateway).

    Client-facing policy (profiles, system prompts) is applied here.
    Model-specific transformations (prompt templates, input schemas)
    are deferred to the execution target (Edge/Gateway).
    """
    context.middleware_actions.append("master_mode")
    logger.debug(f"Master mode: preparing request for {context.selected_model}")

    profile_data = None
    if preparer._profile_manager and not context.disable_profile:
        profile_data = preparer._profile_manager.get_complete_profile(
            model_id=str(context.selected_model),
            user_params=context.user_params,
            request_profile=context.request_profile,
            model_info={},
            disable_profile=context.disable_profile,
        )
        for warning in profile_data.warnings:
            logger.warning(warning)
            context.middleware_actions.append(f"profile_warning:{warning}")
        context.profile_data = profile_data

    if profile_data and profile_data.name:
        context.request_profile = profile_data.name
        context.middleware_actions.append(f"profile_resolved:{profile_data.name}")

    original_messages = extract_messages(context, preparer.transformer)
    if (
        profile_data
        and profile_data.has_system_prompt()
        and profile_data.system_prompt
        and profile_data.name
    ):
        original_messages = inject_profile_system_prompt(
            original_messages,
            profile_data.system_prompt,
            profile_data.name,
            context,
        )
    filtered_messages = preparer._transformation_engine.apply_filters_only(
        original_messages, context.selected_model
    )

    context.processed_messages = filtered_messages
    context.transformation_metadata = {}
    context.model_metadata = None

    preparer._set_sticky_policy(context)

    context.modified_request = build_federation_forward_payload(
        preparer, context, filtered_messages
    )
    context.client_wants_streaming = context.original_request.get("stream", False)

    logger.debug(
        f"Master mode preparation complete: {len(filtered_messages)} messages, "
        f"sticky={context.model_sticky}"
    )


async def prepare_normal_mode(
    preparer: RequestPreparer, context: RequestContext
) -> None:
    """Prepare request with the full transformation pipeline (normal mode).

    Fetches model config, validates existence (local/federation), resolves
    profiles, extracts and transforms messages, and builds the final request.
    """
    logger.debug(f"🔍 PREPARING NORMAL MODE for model: {context.selected_model}")

    if preparer.is_router_only:
        await prepare_master_mode(preparer, context)
        return

    try:
        model_config = await preparer.gateway_manager.fetch_model_configuration(
            context.selected_model
        )
        if not model_config:
            if preparer.gateway_manager.model_exists_in_federation(
                str(context.selected_model)
            ):
                logger.debug(
                    f"Model {context.selected_model} not in local gateway, "
                    "but exists in federation - applying local transformations"
                )
                context.model_metadata = None
                context.middleware_actions.append(
                    "federated_model_local_transformation"
                )
            else:
                logger.error(
                    f"❌ Model {context.selected_model} not found "
                    f"in local or federated catalogs"
                )
                from ..errors.model_errors import ModelErrorBuilder as ModelErrors

                raise ModelErrors.model_not_found(str(context.selected_model))
        else:
            context.model_metadata = model_config

        context.model_sticky = resolve_model_sticky(
            context.selected_model, preparer._config
        )
        context.middleware_actions.append(
            f"model_sticky:{'true' if context.model_sticky else 'false'}"
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Failed to get model information for {context.selected_model}: {e}"
        )
        raise HTTPException(
            status_code=500,
            detail=f"Error retrieving model {context.selected_model}: {str(e)}",
        )

    if context.model_metadata is not None:
        input_schema = extract_input_schema(context.model_metadata)
    else:
        transform_config = preparer._transformation_engine.get_config_for_model(
            context.selected_model
        )
        input_schema = (
            transform_config.get("settings", {}).get("input_schema", "prompt")
            if transform_config
            else "prompt"
        )

    profile_data = None
    if preparer._profile_manager and not context.disable_profile:
        profile_data = preparer._profile_manager.get_complete_profile(
            model_id=str(context.selected_model),
            user_params=context.user_params,
            request_profile=context.request_profile,
            model_info=metadata_to_profile_dict(context.model_metadata),
            disable_profile=context.disable_profile,
        )

        for warning in profile_data.warnings:
            logger.warning(warning)
            context.middleware_actions.append(f"profile_warning:{warning}")

        context.profile_data = profile_data
        if profile_data.name:
            context.request_profile = profile_data.name
            context.middleware_actions.append(f"profile_resolved:{profile_data.name}")

    original_messages = extract_messages(context, preparer.transformer)
    if (
        profile_data
        and profile_data.has_system_prompt()
        and profile_data.system_prompt
        and profile_data.name
    ):
        original_messages = inject_profile_system_prompt(
            original_messages,
            profile_data.system_prompt,
            profile_data.name,
            context,
        )

    transformation_metadata = {"prompt_content": ""}
    filtered_messages = preparer._transformation_engine.apply_filters_only(
        original_messages, context.selected_model
    )

    if input_schema != "messages":
        processed_messages, transformation_metadata = (
            preparer.transformer.transform_to_prompt(
                filtered_messages,
                context.selected_model,
                transformation_metadata,
                context.middleware_actions,
            )
        )
    else:
        processed_messages = filtered_messages.copy()
        context.middleware_actions.append("pass_through_messages_format_with_filters")

        transform_config = preparer._transformation_engine.get_config_for_model(
            context.selected_model
        )
        if transform_config:
            settings = transform_config.get("settings", {})
            if "generation_params" in settings:
                transformation_metadata["generation_params"] = settings[
                    "generation_params"
                ]
                logger.info(
                    f"✅ Extracted generation params for messages format: {list(settings['generation_params'].keys())}"
                )

    context.processed_messages = processed_messages
    context.transformation_metadata = transformation_metadata

    transformation_metadata["model_format"] = (
        context.model_metadata.format if context.model_metadata else None
    )
    transformation_metadata["input_schema"] = input_schema
    transformation_metadata["transformation_applied"] = str(input_schema != "messages")

    preparer.builder.build_request_data(
        context, processed_messages, transformation_metadata
    )
    context.client_wants_streaming = context.raw_client_fields.get("stream", False)
