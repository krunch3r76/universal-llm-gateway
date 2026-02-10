"""
Request preparation module - handles all request transformation logic.

This module is responsible for:
- Parsing and validating incoming requests
- Extracting user parameters
- Applying chat template transformations
- Preparing request data for forwarding

Note: Import ordering is intentionally non-standard to ensure logging
configuration is loaded before universal_logging import.
"""
# ruff: noqa: E501

import time
import uuid
from typing import Any

from fastapi import HTTPException, Request

# CRITICAL: Load logging config before importing universal_logging
from config.logging_config import load_logging_config

load_logging_config()

from universal_logging import get_logger, format_json_for_log  # noqa: E402,I001

from ....profiles import ProfileManager  # noqa: E402,I001
from ...validation.json_schema_validator import (  # noqa: E402
    SchemaValidationError,
    validate_response_format,
)
from src.schemas.chat_completion import ChatCompletionRequest  # noqa: E402,I001

from model_id import ModelId, validate_model_id  # noqa: E402,I001
from ...utils.model_metadata_helpers import (  # noqa: E402,I001
    extract_input_schema,
    should_transform_to_prompt,
)
from ....transformations import TransformationEngine  # noqa: E402,I001

# Remove import - truncation now automatic
from ..errors import RequestErrorBuilder  # noqa: E402,I001
from .builder import RequestBuilder  # noqa: E402,I001
from .context import RequestContext  # noqa: E402,I001
from .request_extraction import extract_original_request  # noqa: E402,I001
from .sticky_routing import resolve_model_sticky  # noqa: E402,I001
from .transformer import RequestTransformer  # noqa: E402,I001

logger = get_logger(__name__)


def validate_and_prepare_model_id(model_id: str) -> tuple[str, str | None]:
    """
    Validate model ID suffixes and prepare for routing.

    This is the SINGLE ENTRYPOINT for suffix validation in Stargate.
    Called early in request processing before any routing decisions.

    Args:
        model_id: Raw model ID from request

    Returns:
        Tuple of (routing_key, error_message)
        - If error_message is not None, request should be rejected with 400
        - routing_key has context and -hybrid stripped for routing
    """
    error = validate_model_id(model_id)
    if error:
        return model_id, error

    parsed = ModelId.parse(model_id)
    return parsed.routing_key, None


class RequestPreparer:
    """
    Handles all request preparation and transformation logic for incoming requests.

    This class is responsible for:
      - Validating and parsing model IDs to ensure routing and request validity.
      - Managing all steps required to transform a client request into a normalized,
        ready-to-execute form (including applying model templates, system prompts,
        and compatibility adjustments).
      - Coordinating any required user or system profile logic (injecting system prompts,
        warnings, or user-specific behaviors when applicable).
      - Applying gateway and router rules (such as sticky routing policy, router-only short-circuit,
        federation/forwarding mode, bypasses for debugging/testing, and distinguishing between
        internal pipeline requests and normal user requests).
      - Extracting, filtering, and optionally transforming request messages into the correct
        format expected by downstream model gateways or proxy services.
      - Managing context objects that track the full lifecycle of a request,
        including unmodified client input, processing history, middleware actions,
        and downstream metadata.
      - Handling corner cases (such as missing models, federated-only models,
        legacy prompt fields, and robust schema validation for advanced
        features like response_format).
      - Building and emitting detailed logs and trace data throughout preparation,
        critical for observability and debugging.

    This class is the central engine for _all_ non-streaming request pre-processing
    before dispatching to lower-level systems or external execution targets.
    Downstream consumers rely on the context and transformations produced here.

    Typical usage:
        preparer = RequestPreparer(...)
        context = await preparer.prepare_request(request, chat_request, ...)
        # Now context is ready for execution by gateway/federation code.

    Design highlights and invariants:
      - Isolates mutation of client input; raw_client_fields is never modified.
      - All internal state and decision points are tracked in middleware_actions.
      - Supports multiple modes (normal, bypass, router-only, federated).
      - Strict points of input validation / model ID canonicalization.
      - Designed to be resilient to configuration, model upgrades, and profile changes.
    """

    def __init__(
        self,
        gateway_manager,
        transformation_engine: TransformationEngine,
        profile_manager: ProfileManager,
        token_manager,
        token_management_enabled: bool,
        config=None,
    ):
        self.gateway_manager = gateway_manager
        self._transformation_engine = transformation_engine
        self._profile_manager = profile_manager
        self.token_manager = token_manager
        self.token_management_enabled = token_management_enabled
        self._config = config

        self.transformer = RequestTransformer(transformation_engine)
        self.builder = RequestBuilder(self._profile_manager)

    @property
    def is_router_only(self) -> bool:
        """
        Return True if this is router-only mode (there is no local gateway).

        Router-only mode occurs when gateway_manager is None; in this mode,
        the preparer delegates all model-specific processing (configuration fetching,
        transformation, validation, etc) to the execution/federation target.
        Preparation here is minimal (just input normalization and routing metadata).
        """
        return self.gateway_manager is None

    async def prepare_request(
        self,
        request: Request,
        chat_request: ChatCompletionRequest,
        model_override: str | None = None,
        profile_override: str | None = None,
        disable_profile: bool = False,
        is_pipeline: bool = False,
        skip_token_counting: bool | None = None,
    ) -> RequestContext:
        """
        Prepare a complete RequestContext for downstream execution, with all
        applicable transformations, validation, profile logic, context propagation,
        and compatibility adjustments applied.

        This is the main entry point called for every non-streaming request.
        All preparation, mutation, validation, and middleware action logging
        for request handling is coordinated here.

        Processing details:
          - Extract and canonicalize model ID (either from override or chat_request).
          - Validate model string using the single source of validation logic.
          - Extract unmodified client input as original_request/raw_client_fields.
          - If debugging is enabled, a "before" copy is saved for snapshot-based debugging.
          - If there is a response_format, it is schema-checked and errors are surfaced gracefully.
          - User parameters (excluding core fields like model/messages/prompt) are saved.
          - Tracks all preparation decision points/mutations in middleware_actions, for
            later auditing, debugging, and user feedback.
          - Determines special modes: bypass_transformations, router-only, pipeline, etc.
          - Applies token-counting flags if specified in request or header.
          - Applies profile override or disables profiles if instructed.
          - Handles pipeline/internally-routed requests by propagating execution ID, timeout hints, etc.
          - Passes context state into either bypass mode or normal mode preparers.

        Args:
            request: HTTP request object, typically FastAPI Request.
            chat_request: Instance of ChatCompletionRequest parsed from client payload.
            model_override: Raw model string to force/override. If not provided,
                model is pulled from chat_request.model.
            profile_override: String to select a named profile, overriding user or model-level default.
            disable_profile: If true, no profile logic is run or injected.
            is_pipeline: If true, preparation is for an internal pipeline (special behaviors set).
            skip_token_counting: If set, overrides request- or profile-based token counting behaviors.

        Returns:
            context: A fully-prepared RequestContext to pass to execution/federation, with metadata,
                transformations, flags, and audit record of preparation decisions.

        Raises:
            HTTPException: If required request fields are missing or invalid, or schema checks fail.
        """
        start_time = time.time()

        # INV: request_id = X-Internal-Request-ID || uuid4()
        # Computed once at proxy boundary; used everywhere internally.
        # Pipeline cancellation invariant: queue_key = cancel_key = request_id
        request_id = request.headers.get("X-Internal-Request-ID") or str(uuid.uuid4())

        selected_model_str = model_override or chat_request.model
        if not selected_model_str:
            raise RequestErrorBuilder.model_not_specified()

        model_id_error = validate_model_id(selected_model_str)
        if model_id_error:
            raise HTTPException(
                status_code=400,
                detail={
                    "type": "invalid_request_error",
                    "message": model_id_error,
                    "param": "model",
                    "code": "invalid_model_id",
                },
            )

        # Parse model ID at API boundary (architecture pattern)
        selected_model = ModelId.parse(selected_model_str)

        original_request = await extract_original_request(request, chat_request)
        raw_client_fields = original_request.copy()

        logger.info(
            f"📥 REQUEST BODY (from client): {format_json_for_log(original_request, truncate=False)}"
        )

        # Write before-modification snapshot if debugging enabled
        from ...debug.request_snapshots import write_request_snapshot

        await write_request_snapshot(original_request, request_id, stage="before")

        # Validate response_format if present
        if "response_format" in original_request:
            try:
                validate_response_format(original_request["response_format"])
                logger.debug("✅ response_format validation passed")
            except SchemaValidationError as e:
                error_detail = {
                    "type": "invalid_request_error",
                    "message": e.message,
                    "param": e.param,
                    "code": "invalid_json_schema",
                }
                if e.suggested_fix:
                    error_detail["suggested_fix"] = e.suggested_fix

                logger.error(f"❌ JSON Schema validation failed: {e.message}")
                raise HTTPException(status_code=400, detail=error_detail)

        user_params = {
            field_name: field_value
            for field_name, field_value in raw_client_fields.items()
            if field_name not in ["model", "messages", "prompt"]
            and field_value is not None
        }

        middleware_actions = []
        if model_override:
            middleware_actions.append(f"model_override_applied: {model_override}")
        else:
            middleware_actions.append(f"model_from_request: {selected_model}")

        bypass_transformations = (
            request.query_params.get("bypass_transformations", "false").lower()
            == "true"
        )
        disable_profile = (
            request.query_params.get("disable_profile", "false").lower() == "true"
        )

        # Determine skip_token_counting: query param > request body > default False
        effective_skip_token_counting = False
        if skip_token_counting is not None:
            effective_skip_token_counting = skip_token_counting
        elif (
            hasattr(chat_request, "skip_token_counting")
            and chat_request.skip_token_counting is not None
        ):
            effective_skip_token_counting = chat_request.skip_token_counting

        context = RequestContext(
            request_id=request_id,
            start_time=start_time,
            selected_model=selected_model,
            original_request=original_request,
            raw_client_fields=raw_client_fields,
            user_params=user_params,
            middleware_actions=middleware_actions,
            bypass_transformations=bypass_transformations,
            disable_profile=disable_profile,
            skip_token_counting=effective_skip_token_counting,
            http_request=request,
            chat_request=chat_request,
        )

        request_profile = (
            profile_override
            or request.query_params.get("profile")
            or request.query_params.get("filter")
        )
        if request_profile:
            context.request_profile = request_profile
            context.middleware_actions.append(f"request_profile:{request_profile}")

        # Handle internal pipeline requests (via HTTP headers)
        is_pipeline_internal = request.headers.get("X-Pipeline-Internal") == "true"
        if is_pipeline_internal:
            context.middleware_actions.append("pipeline_internal")

            # Skip token counting for pipeline steps (matches original ModelInvoker)
            if request.headers.get("X-Skip-Token-Counting", "").lower() == "true":
                context.skip_token_counting = True

            # Propagate execution context for logging/tracing/cancellation
            execution_id = request.headers.get("X-Pipeline-Execution-Id")
            step_id = request.headers.get("X-Pipeline-Step-Id")
            if execution_id:
                context.pipeline_execution_id = execution_id
            if step_id:
                context.pipeline_step_id = step_id

            # Read timeout hint from pipeline
            timeout_hint = request.headers.get("X-Request-Timeout")
            if timeout_hint:
                try:
                    context.request_timeout_hint = float(timeout_hint)
                except ValueError:
                    logger.warning(f"Invalid X-Request-Timeout header: {timeout_hint}")

            if execution_id or step_id:
                logger.debug(
                    f"Pipeline request: execution={execution_id}, step={step_id}"
                )

        if is_pipeline or bypass_transformations:
            await self._prepare_bypass_mode(context)
        else:
            await self._prepare_normal_mode(context)

        logger.debug(f"Request {request_id} prepared for model {selected_model}")
        return context

    async def _prepare_bypass_mode(self, context: RequestContext):
        """
        Prepare request in bypass mode (minimal transformations).

        Bypass mode is primarily used for debugging or testing; normal model transformation
        logic is skipped and only basic compatibility modifications are applied.
        The goal is to forward the raw user input to the downstream gateway with as
        little mutation as possible (other than legacy field normalization).

        Details:
          - If prompt is set, it is preprocessed and inserted as a user message,
            and the prompt field is removed, mimicking the minimum necessary preparation.
          - No token counting or other model-specific configuration is performed.
        """
        context.middleware_actions.append("transformations_bypassed_for_testing")
        logger.debug(
            "BYPASS MODE: Forwarding request to gateway without transformations"
        )

        request_data = context.original_request.copy()
        if context.chat_request and context.chat_request.prompt:
            cleaned_prompt = self.transformer.preprocess_prompt_field(
                str(context.chat_request.prompt)
            )
            request_data["messages"] = [{"role": "user", "content": cleaned_prompt}]
            if "prompt" in request_data:
                del request_data["prompt"]
            context.middleware_actions.append(
                "prompt_field_processed_to_messages_bypass_mode"
            )
            logger.info(
                "BYPASS MODE: Prompt processed; removed FIM markers, converted to user message"
            )

        context.modified_request = request_data
        context.client_wants_streaming = request_data.get("stream", False)
        context.model_metadata = None

    def _extract_and_filter_messages(
        self, context: RequestContext
    ) -> list[dict[str, Any]]:
        """
        Extract and filter messages for router-only mode and pass them on for forwarding.

        In router-only mode, full transformation to legacy prompt format is NOT performed;
        instead, all user content is extracted as messages and basic filters (such as profanity,
        redaction, etc) are applied.

        Returns:
            List of filtered message dictionaries, ready to be forwarded (in messages format).
        """
        original_messages = self._extract_messages(context)
        return self._transformation_engine.apply_filters_only(
            original_messages, context.selected_model
        )

    def _set_sticky_policy(self, context: RequestContext) -> None:
        """
        Set sticky routing policy on the request context for the selected model.

        Sticky routing is used for routing affinity and request pinning; the policy is determined
        by model configuration and optionally system-level config. The decision is recorded
        in context.model_sticky and logged into middleware actions for auditing.
        """
        context.model_sticky = resolve_model_sticky(
            context.selected_model, self._config
        )
        context.middleware_actions.append(
            f"model_sticky:{'true' if context.model_sticky else 'false'}"
        )

    def _build_router_only_forward_payload(
        self, context: RequestContext, filtered_messages: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """
        Build the minimal request payload for forwarding in router-only/federation mode.

        This payload:
          - Contains user messages after filtering.
          - Applies generation_params (e.g. default stop sequences, max tokens)
            from model transformation config if and only if not overridden by the user.
          - Strips redundant prompt fields if present (after legacy handling).
          - Leaves full transformation (prompt template, input schema, etc)
            to the execution (federation) target gateway.

        Args:
            context: The RequestContext holding all input/context metadata.
            filtered_messages: Pre-filtered, format-normalized user messages.

        Returns:
            request_data: Dict suitable for direct federation/forwarding to a remote gateway.
        """
        request_data = context.original_request.copy()
        request_data["messages"] = filtered_messages

        # Remove prompt if present (converted to messages)
        if "prompt" in request_data:
            del request_data["prompt"]

        # Apply generation_params from transformation config (e.g., stop sequences)
        # Even though we defer full transformations, generation params like stop
        # sequences must be applied before forwarding
        logger.debug(
            f"🔍 Router-only: Looking for generation_params for {context.selected_model}"
        )
        transform_config = self._transformation_engine.get_config_for_model(
            context.selected_model
        )
        logger.debug(
            f"🔍 Router-only: transform_config={'found' if transform_config else 'NOT FOUND'}"
        )
        if transform_config:
            settings = transform_config.get("settings", {})
            logger.debug(
                f"🔍 Router-only: Has generation_params: {'generation_params' in settings}"
            )
            if "generation_params" in settings:
                gen_params = settings["generation_params"]
                logger.info(
                    f"🔍 Router-only: generation_params = {list(gen_params.keys())}"
                )
                applied_params = []
                for param_name, param_value in gen_params.items():
                    if param_name not in request_data:
                        request_data[param_name] = param_value
                        applied_params.append(param_name)
                if applied_params:
                    logger.info(
                        f"✅ Applied generation params in router-only mode: "
                        f"{applied_params}"
                    )

        return request_data

    async def _prepare_router_only_mode(self, context: RequestContext):
        """
        Prepare a request for router-only Master (where there is no local model gateway).

        Behavior:
          - Only messages filtering and minimal transformation is performed locally.
          - No model-specific configuration or prompt templates are applied locally
            (these are the responsibility of the execution/federation target).
          - Only data needed for routing and minimal compliance is set.
          - Updates context with processed messages, sticky routing decision,
            and builds minimal request payload for federation forwarding.

        This method is a critical fast-path for large deployments and advanced routing setups.

        INVARIANT:
            For all router_only_master:
                No local_gateway => minimal_for_routing preparation only.
                All model configuration and transformations are performed downstream.
        """
        context.middleware_actions.append("router_only_mode")
        logger.debug(
            f"Router-only mode: minimal preparation for {context.selected_model}"
        )

        # Extract and filter messages (execution target handles transformations)
        filtered_messages = self._extract_and_filter_messages(context)

        # Update context with minimal metadata
        context.processed_messages = filtered_messages
        context.transformation_metadata = {}  # No local transformations
        context.model_metadata = None  # Fetched by execution target

        # Set sticky routing policy (needed for routing decision)
        self._set_sticky_policy(context)

        # Build minimal request data for forwarding
        context.modified_request = self._build_router_only_forward_payload(
            context, filtered_messages
        )
        context.client_wants_streaming = context.original_request.get("stream", False)

        logger.debug(
            f"Router-only preparation complete: {len(filtered_messages)} messages, "
            f"sticky={context.model_sticky}"
        )

    async def _prepare_normal_mode(self, context: RequestContext):
        """
        Prepare request with the full transformation pipeline (normal mode).

        In normal mode (when running with a local gateway):
          - Fetch and attach model configuration to the context (for template, schema, etc).
          - Validate whether model exists locally, in federation, or not at all.
            - If model is only in federation, build context for minimal forwarding.
            - If model is unknown, raise a not-found error.
          - Extract profile data (if not disabled and available), merging user, model,
            and override information as needed.
          - Apply any profile-level warnings to context.
          - Extract 'messages' from chat_request, and if a profile system prompt is
            specified and not already present in user's messages, inject it as a system message.
          - Perform message filtering with the transformation engine.
          - If the model requires prompt-based input, transform the messages into the expected prompt;
            otherwise, preserve the messages and attach any required generation parameters.
          - Populate context.processed_messages, transformation_metadata,
            and required downstream fields.
          - Mutate context in-place and call builder.build_request_data for assembling the
            final structure sent to downstream systems.

        All model- and user-specific mutation and transformation logic is applied here.
        """
        logger.debug(f"🔍 PREPARING NORMAL MODE for model: {context.selected_model}")

        # Router-only Master: skip local model config fetching
        if self.is_router_only:
            logger.debug(
                f"Router-only mode: skipping local model config for {context.selected_model}"
            )
            await self._prepare_router_only_mode(context)
            return

        try:
            model_config = await self.gateway_manager.fetch_model_configuration(
                context.selected_model
            )
            if not model_config:
                # Check if model exists in federation before failing
                if self.gateway_manager.model_exists_in_federation(
                    str(context.selected_model)
                ):
                    logger.debug(
                        f"Model {context.selected_model} not in local gateway, "
                        "but exists in federation - deferring configuration fetch"
                    )
                    # For federated models: extract messages but skip transformations
                    original_messages = self._extract_messages(context)

                    # Apply filters only (no transformation to prompt format)
                    filtered_messages = self._transformation_engine.apply_filters_only(
                        original_messages, context.selected_model
                    )

                    context.processed_messages = filtered_messages
                    context.transformation_metadata = {}  # No local transformations
                    context.model_metadata = None
                    context.model_sticky = resolve_model_sticky(
                        context.selected_model, self._config
                    )
                    context.middleware_actions.append(
                        f"model_sticky:{'true' if context.model_sticky else 'false'}"
                    )
                    context.middleware_actions.append(
                        "federated_model_deferred_metadata"
                    )
                    return

                # Model doesn't exist anywhere - fail immediately
                logger.error(
                    f"❌ Model {context.selected_model} not found in local or federated catalogs"
                )
                from ..errors.model_errors import ModelErrorBuilder as ModelErrors

                raise ModelErrors.model_not_found(str(context.selected_model))

            context.model_metadata = model_config
            context.model_sticky = resolve_model_sticky(
                context.selected_model, self._config
            )
            context.middleware_actions.append(
                f"model_sticky:{'true' if context.model_sticky else 'false'}"
            )
        except HTTPException:
            # Re-raise HTTPExceptions (like our model_not_found error) as-is
            raise
        except Exception as e:
            logger.error(
                f"Failed to get model information for {context.selected_model}: {e}"
            )
            # For other errors, use generic error
            raise HTTPException(
                status_code=500,
                detail=f"Error retrieving model {context.selected_model}: {str(e)}",
            )

        input_schema = extract_input_schema(context.model_metadata)

        # Get complete profile data (single query)
        profile_data = None
        if self._profile_manager and not context.disable_profile:
            from ...utils.model_metadata_helpers import metadata_to_profile_dict

            profile_data = self._profile_manager.get_complete_profile(
                model_id=str(context.selected_model),
                user_params=context.user_params,
                request_profile=context.request_profile,
                model_info=metadata_to_profile_dict(context.model_metadata),
                disable_profile=context.disable_profile,
            )

            # Apply warnings
            for warning in profile_data.warnings:
                logger.warning(warning)
                context.middleware_actions.append(f"profile_warning:{warning}")

            # Store for later use in builder
            context.profile_data = profile_data

        # Extract and apply system prompt to messages
        original_messages = self._extract_messages(context)
        if profile_data and profile_data.has_system_prompt():
            original_messages = self._inject_profile_system_prompt(
                original_messages,
                profile_data.system_prompt,
                profile_data.name,
                context,
            )

        transformation_metadata = {"prompt_content": ""}
        # context.selected_model is already a ModelId object
        filtered_messages = self._transformation_engine.apply_filters_only(
            original_messages, context.selected_model
        )

        if should_transform_to_prompt(context.model_metadata):
            processed_messages, transformation_metadata = (
                self.transformer.transform_to_prompt(
                    filtered_messages,
                    context.selected_model,
                    transformation_metadata,
                    context.middleware_actions,
                )
            )
        else:
            processed_messages = filtered_messages.copy()
            context.middleware_actions.append(
                "pass_through_messages_format_with_filters"
            )

            # Extract generation_params even when not transforming to prompt
            transform_config = self._transformation_engine.get_config_for_model(
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

        transformation_metadata["model_format"] = context.model_metadata.format
        transformation_metadata["input_schema"] = input_schema
        transformation_metadata["transformation_applied"] = str(
            input_schema != "messages"
        )

        self.builder.build_request_data(
            context, processed_messages, transformation_metadata
        )
        context.client_wants_streaming = context.raw_client_fields.get("stream", False)

    def _extract_messages(self, context: RequestContext) -> list[dict[str, Any]]:
        """
        Extract the message sequence from the provided context into the common format
        (dicts with 'role' and 'content').

        If a `prompt` field is present on the chat request, it is preprocessed and converted
        to a single 'user' message, and the fact is logged for auditing. Otherwise, if the
        chat request is already in messages format, these are normalized to dicts.

        Args:
            context: RequestContext containing chat_request/input payloads.

        Returns:
            List of messages (each a dict with 'role' and 'content' fields).
        """
        if context.chat_request and context.chat_request.prompt:
            cleaned_prompt = self.transformer.preprocess_prompt_field(
                str(context.chat_request.prompt)
            )
            context.middleware_actions.append("prompt_field_processed_to_messages")
            return [{"role": "user", "content": cleaned_prompt}]

        messages_list = (
            context.chat_request.messages
            if context.chat_request and context.chat_request.messages
            else []
        )
        return [{"role": msg.role, "content": msg.content} for msg in messages_list]

    def _inject_profile_system_prompt(
        self,
        messages: list[dict[str, Any]],
        system_prompt: str,
        profile_name: str,
        context: RequestContext,
    ) -> list[dict[str, Any]]:
        """
        Inject a profile system prompt into the beginning of the message list,
        unless a user-provided system message has already been given.

        If the first (or any) message is already a system message, no action is taken,
        and the presence is logged/audited in middleware_actions.
        If the user did NOT provide a system message, a new system message with the
        content from the current profile is prepended and tracked.

        Args:
            messages: List of conversation messages (each a dict with role/content).
            system_prompt: System prompt string to inject, e.g. from a profile.
            profile_name: Name or identifier for the active profile (used for logging/tracing).
            context: RequestContext for updating preparation history/info.

        Returns:
            List of messages, with system prompt injected at the start if needed.
        """
        # Check if user already provided a system message
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

        # Inject profile system prompt
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
