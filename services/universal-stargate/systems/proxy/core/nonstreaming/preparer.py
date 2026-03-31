"""Request preparation — context construction and dispatch to mode-specific transforms.

RequestPreparer is the single entry point for building a RequestContext from
a raw HTTP request.  It handles model ID validation, parameter extraction,
response_format schema validation, profile/pipeline header propagation, and
bypass-mode preparation.  The actual transformation logic for normal mode
and master mode lives in mode_transforms.py — this module delegates to it.

Note: Import ordering is intentionally non-standard to ensure logging
configuration is loaded before universal_logging import.
"""
# ruff: noqa: E501

import time
import uuid

from fastapi import HTTPException, Request

# CRITICAL: Load logging config before importing universal_logging
from config.logging_config import load_logging_config

load_logging_config()

from universal_logging import get_logger, format_json_for_log  # noqa: E402,I001

from ....profiles import ProfileManager  # noqa: E402,I001
from systems.persona_aliases.manager import PersonaAliasManager  # noqa: E402,I001
from ...validation.json_schema_validator import (  # noqa: E402
    SchemaValidationError,
    validate_response_format,
)
from src.schemas.chat_completion import ChatCompletionRequest  # noqa: E402,I001

from model_id import ModelId, validate_model_id  # noqa: E402,I001
from ....transformations import TransformationEngine  # noqa: E402,I001

# Remove import - truncation now automatic
from ..errors import RequestErrorBuilder  # noqa: E402,I001
from .builder import RequestBuilder  # noqa: E402,I001
from .context import RequestContext  # noqa: E402,I001
from .request_extraction import extract_original_request  # noqa: E402,I001
from .sticky_routing import resolve_model_sticky  # noqa: E402,I001
from .transformer import RequestTransformer  # noqa: E402,I001
from .mode_transforms import prepare_normal_mode  # noqa: E402,I001

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
    """Builds a fully-populated RequestContext from a raw HTTP request.

    Owns the preparation lifecycle: model ID validation, parameter extraction,
    response_format schema checking, profile/pipeline header propagation, and
    mode dispatch.  Transformation logic for normal mode and master mode is
    delegated to mode_transforms.py — this class coordinates but does not
    contain transformation implementations.

    Typical usage::

        preparer = RequestPreparer(...)
        context = await preparer.prepare_request(request, chat_request, ...)
        # context is ready for execution by gateway/federation code.

    Invariants:
      - raw_client_fields is never mutated after extraction.
      - All decision points are tracked in context.middleware_actions.
      - Supports normal, bypass, master, and federated preparation modes.
    """

    def __init__(
        self,
        gateway_manager,
        transformation_engine: TransformationEngine,
        profile_manager: ProfileManager,
        persona_alias_manager: PersonaAliasManager,
        token_manager,
        token_management_enabled: bool,
        config=None,
    ):
        self.gateway_manager = gateway_manager
        self._transformation_engine = transformation_engine
        self._profile_manager = profile_manager
        self._persona_alias_manager = persona_alias_manager
        self.token_manager = token_manager
        self.token_management_enabled = token_management_enabled
        self._config = config

        self.transformer = RequestTransformer(transformation_engine)
        self.builder = RequestBuilder(self._profile_manager)

    @property
    def is_router_only(self) -> bool:
        """No local gateway -- Master preparation mode.

        When True, the preparer applies client-facing policy (profiles,
        system prompts) but defers model-specific transformations to
        the execution target (Edge/Gateway).
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
        requested_model: str | None = None,
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

        requested_model_str = requested_model or model_override or chat_request.model
        if not requested_model_str:
            raise RequestErrorBuilder.model_not_specified()

        persona_alias = self._persona_alias_manager.get(requested_model_str)
        if persona_alias is not None:
            # Fail fast on ambiguous persona layering.
            # If an alias is used, the request must not also specify a profile/filter.
            qp_profile = (
                profile_override
                or request.query_params.get("profile")
                or request.query_params.get("filter")
            )
            if qp_profile:
                raise HTTPException(
                    status_code=400,
                    detail={
                        "type": "invalid_request_error",
                        "message": (
                            "Persona alias request may not combine with profile/filter. "
                            f"alias={persona_alias.alias_id} profile={qp_profile}"
                        ),
                        "param": "filter",
                        "code": "invalid_persona_alias_conflict",
                    },
                )
            selected_model_str = persona_alias.backing_model
        else:
            selected_model_str = requested_model_str
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

            # Adapt response_format to target engine (GGUF keeps json_object; vLLM gets json_schema)
            from ...validation.response_format_converter import (
                convert_response_format_for_engine,
            )

            original_request["response_format"] = convert_response_format_for_engine(
                selected_model_str, original_request["response_format"]
            )

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

        context.requested_model = requested_model_str
        if persona_alias is not None:
            context.persona_alias_id = persona_alias.alias_id
            context.persona_backing_model = persona_alias.backing_model
            context.persona_system_prompt = persona_alias.system_prompt
            context.persona_params = dict(persona_alias.params)
            context.middleware_actions.append(
                f"persona_alias_resolved:{persona_alias.alias_id}"
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

            cancel_group = request.headers.get("X-Pipeline-Cancel-Group")
            if cancel_group:
                context.cancel_group = cancel_group

            if execution_id or step_id:
                logger.debug(
                    f"Pipeline request: execution={execution_id}, step={step_id}"
                )

        if is_pipeline or bypass_transformations:
            await self._prepare_bypass_mode(context)
        else:
            await prepare_normal_mode(self, context)

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
