"""
Request builder module - handles final request data construction.

This module is responsible for:
- Building the final request dictionary
- Applying profile parameters
- Handling field inclusion/exclusion
"""

from typing import Any

from universal_logging import get_logger

from .local_reasoning import apply_local_reasoning_off

logger = get_logger(__name__)


class RequestBuilder:
    """Handles request data construction and parameter application"""

    def __init__(self, profile_manager=None):
        """
        Initialize RequestBuilder.

        Args:
            profile_manager: Optional ProfileManager instance for profile parameters
        """
        self.profile_manager = profile_manager

    def build_request_data(
        self,
        context,
        processed_messages: list[dict[str, Any]],
        transformation_metadata: dict[str, Any],
    ):
        """
        Build the final request data for forwarding.

        Args:
            context: RequestContext instance
            processed_messages: Processed message list
            transformation_metadata: Transformation metadata dictionary
        """
        request_data = {}

        # Include explicitly set client fields
        # CRITICAL: Preserve all fields including 'stream' parameter
        # EXCEPT Stargate-only routing/control parameters
        stargate_only_params = {
            "skip_token_counting",
            "profile",
            "filter",
            "disable_profile",
        }

        filtered_count = 0
        for field_name, field_value in context.raw_client_fields.items():
            if field_value is not None:
                if field_name in stargate_only_params:
                    filtered_count += 1
                    logger.debug(
                        f"🚫 Filtered Stargate-only parameter: "
                        f"{field_name}={field_value}"
                    )
                else:
                    request_data[field_name] = field_value

        if filtered_count > 0:
            logger.info(
                f"Filtered {filtered_count} Stargate-only parameter(s) from request"
            )

        # Apply processed messages/content based on transformation
        self.apply_content_to_request(
            request_data, processed_messages, transformation_metadata
        )

        # Ensure model is set (convert ModelId to string for serialization)
        request_data["model"] = str(context.selected_model)

        # Apply token management modifications
        if "max_tokens" in context.user_params:
            request_data["max_tokens"] = context.user_params["max_tokens"]

        # Apply profile parameters from pre-resolved profile data
        if hasattr(context, "profile_data") and context.profile_data:
            profile_data = context.profile_data

            # Apply parameters (only those not in user_params)
            for key, value in profile_data.params.items():
                if key not in context.user_params and key not in request_data:
                    request_data[key] = value

            # Record actions
            if profile_data.actions:
                context.middleware_actions.extend(profile_data.actions)
                logger.info(
                    f"Applied profile '{profile_data.name}' parameters: "
                    f"{len(profile_data.params)} params"
                )

        # OpenRouter-shaped reasoning.off → local enable_thinking=false
        # (pipelines already do this via profiles; one-shots need the body knob).
        if apply_local_reasoning_off(
            request_data, model_id=str(context.selected_model)
        ):
            context.middleware_actions.append(
                "local_reasoning_off_mapped_to_enable_thinking_false"
            )
            logger.info(
                "Mapped reasoning.effort=none → chat_template_kwargs."
                "enable_thinking=false for local model %s",
                context.selected_model,
            )

        context.modified_request = request_data
        context.middleware_actions.append(
            "no_parameter_defaults_applied_engine_responsibility"
        )

        logger.debug(f"Built request data with {len(request_data)} fields")

    def apply_content_to_request(
        self,
        request_data: dict[str, Any],
        processed_messages: list[dict[str, Any]],
        transformation_metadata: dict[str, Any],
    ):
        """
        Apply processed content to request data.

        This method handles the logic of whether to use 'prompt' or 'messages' field
        based on the transformation metadata.

        Args:
            request_data: Request data dictionary (modified in place)
            processed_messages: Processed message list
            transformation_metadata: Transformation metadata dictionary
        """
        transformation_applied_str = transformation_metadata.get(
            "transformation_applied", "False"
        )
        transformation_applied = transformation_applied_str.lower() == "true"

        if transformation_applied:
            # Use prompt field
            prompt_content = transformation_metadata.get("prompt_content", "")
            if prompt_content:
                request_data["prompt"] = prompt_content
                if "messages" in request_data:
                    del request_data["messages"]
                logger.info(
                    f"✅ Applied transformed prompt to request: "
                    f"{len(prompt_content)} chars"
                )
            else:
                logger.warning(
                    "⚠️ transformation_applied=True but no prompt_content found"
                )
        else:
            # Use messages field
            request_data["messages"] = processed_messages
            if "prompt" in request_data:
                del request_data["prompt"]
            logger.info(
                f"✅ Applied messages to request: {len(processed_messages)} messages"
            )

        # Apply generation parameters from transformation metadata if present
        # Only apply if client didn't explicitly provide the parameter
        if "generation_params" in transformation_metadata:
            gen_params = transformation_metadata["generation_params"]
            applied_params = []
            for param_name, param_value in gen_params.items():
                if param_name not in request_data:
                    request_data[param_name] = param_value
                    applied_params.append(param_name)
            if applied_params:
                logger.info(
                    f"✅ Applied generation params from transformation: "
                    f"{applied_params}"
                )
