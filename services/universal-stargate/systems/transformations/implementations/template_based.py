"""
Template-based transformation system for chat prompts.

Main functions:
- apply_template_transformation(): Applies YAML-configured templates
- apply_transformation_filters(): Filters messages (remove system, truncate)
"""

from typing import Any

from universal_logging import get_logger

logger = get_logger(__name__)


def apply_transformation_filters(
    messages: list[dict[str, str]], settings: dict[str, Any]
) -> list[dict[str, str]]:
    """
    Apply transformation filters to messages WITHOUT converting format.

    Args:
        messages: List of message dictionaries (OpenAI Chat Completions format)
        settings: Configuration settings from model_transformations.yaml

    Returns:
        Filtered messages (still in OpenAI Chat Completions format)
    """
    filtered_messages = messages

    # Extract system messages before filtering (for prepend operation)
    system_content: str | None = None
    if settings.get("prepend_system_to_user", False):
        system_messages = [msg for msg in messages if msg.get("role") == "system"]
        if system_messages:
            # Concatenate multiple system messages with "\n\n" separator
            system_parts = [msg.get("content", "").strip() for msg in system_messages]
            system_parts = [part for part in system_parts if part]  # Remove empty
            if system_parts:
                system_content = "\n\n".join(system_parts)
                logger.info(
                    f"  → Extracted system content for prepend "
                    f"({len(system_content)} chars)"
                )

    # Prepend system content to first user message (if enabled and system exists)
    if system_content:
        # Find first user message
        first_user_idx = -1
        for i, msg in enumerate(filtered_messages):
            if msg.get("role") == "user":
                first_user_idx = i
                break

        if first_user_idx >= 0:
            # Get template (default: "System: {system}\n\n")
            template = settings.get("system_prepend_template", "System: {system}\n\n")

            # Prepend to user message content
            user_msg = filtered_messages[first_user_idx]
            original_content = user_msg.get("content", "")

            # Format template with both system and prompt (user content)
            # Some templates use only {system}, others use {system} and {prompt}
            formatted_system = template.format(
                system=system_content, prompt=original_content
            )

            user_msg["content"] = formatted_system

            logger.info(
                f"  → Prepended system to first user message "
                f"({len(system_content)} chars)"
            )
        else:
            logger.warning(
                "  → Cannot prepend system: no user message found "
                "(system will be removed)"
            )

    # Remove system messages if configured
    if settings.get("remove_system_messages", True):
        filtered_messages = [
            msg for msg in filtered_messages if msg.get("role") != "system"
        ]
        removed_count = len(messages) - len(filtered_messages)
        if removed_count > 0:
            logger.info(f"  → Removed {removed_count} system message(s)")

    # Truncate to last user message if configured
    if settings.get("truncate_to_last_user", False):
        last_user_idx = -1
        for i in range(len(filtered_messages) - 1, -1, -1):
            if filtered_messages[i].get("role") == "user":
                last_user_idx = i
                break
        if last_user_idx >= 0:
            original_count = len(filtered_messages)
            filtered_messages = filtered_messages[last_user_idx:]
            logger.info(
                f"  → Truncated to last user message "
                f"(removed {original_count - len(filtered_messages)} message(s))"
            )

    return filtered_messages


def _derive_assistant_template(prompt_template: str) -> str:
    """
    Derive appropriate assistant template from user-focused prompt_template.

    Args:
        prompt_template: The user-focused template

    Returns:
        Appropriate assistant template for the same format
    """
    # Handle Wizard-Vicuna format
    if "USER:" in prompt_template and "ASSISTANT:" in prompt_template:
        return "{prompt}\n"

    # Handle instruction-based formats
    if "### Instruction:" in prompt_template and "### Response:" in prompt_template:
        return "{prompt}\n\n"

    # Handle other instruction formats
    if (
        "Instruction:" in prompt_template or "### " in prompt_template
    ) and "Response:" in prompt_template:
        return "{prompt}\n\n"

    # Generic fallback
    return "{prompt}\n"


def apply_template_transformation(
    messages: list[dict[str, str]], settings: dict[str, Any]
) -> str:
    """
    Template-based transformation for prompt formatting.

    Uses YAML-configured templates to format messages:
    1. Extract system message if present (before filtering)
    2. Format system message with system_template or conversation_header
    3. Apply user_template/assistant_template to each message
    4. Or use single prompt_template for simple formats

    Args:
        messages: List of message dictionaries (OpenAI Chat Completions format)
        settings: Configuration settings from model_transformations.yaml

    Returns:
        Formatted prompt string based on templates.
    """
    logger.info(f"🔄 Applying template transformation to {len(messages)} messages")

    # Extract system message BEFORE filtering (if not removing system messages)
    system_message: str | None = None
    if not settings.get("remove_system_messages", True):
        for msg in messages:
            if msg.get("role") == "system":
                system_message = msg.get("content", "").strip()
                break  # Use first system message

    # Start with system message (formatted with system_template)
    prompt = ""

    if system_message:
        # Client provided system message - format it
        system_template = settings.get("system_template", "{system}\n\n")
        prompt = system_template.format(system=system_message)
        logger.info(f"  → Using client system message ({len(system_message)} chars)")
    elif "conversation_header" in settings:
        # Fallback to conversation_header if no client system message
        conversation_header = settings.get("conversation_header", "")
        if conversation_header:
            prompt = conversation_header
            if not prompt.endswith("\n"):
                prompt += "\n"
            logger.info(
                f"  → Using conversation_header ({len(conversation_header)} chars)"
            )

    # Check template mode
    if "user_template" in settings or "assistant_template" in settings:
        # Multi-template format with role-specific templates
        user_template = settings.get("user_template", "USER: {prompt}\n")
        assistant_template = settings.get("assistant_template", "ASSISTANT: {prompt}\n")

        for msg in messages:
            role = msg.get("role")
            content = msg.get("content", "").strip()

            if role == "user":
                prompt += user_template.format(prompt=content)
            elif role == "assistant":
                prompt += assistant_template.format(prompt=content)
            # Skip system messages (already handled above)

    elif "prompt_template" in settings:
        # Single prompt_template approach with derived assistant template
        prompt_template = settings.get("prompt_template", "{prompt}")
        assistant_template = _derive_assistant_template(prompt_template)

        for msg in messages:
            role = msg.get("role")
            content = msg.get("content", "").strip()

            if role == "user":
                prompt += prompt_template.format(prompt=content)
            elif role == "assistant":
                prompt += assistant_template.format(prompt=content)
            # Skip system messages (already handled above)

    else:
        # Fallback defaults
        user_template = "USER: {prompt}\n"
        assistant_template = "ASSISTANT: {prompt}\n"

        for msg in messages:
            role = msg.get("role")
            content = msg.get("content", "").strip()

            if role == "user":
                prompt += user_template.format(prompt=content)
            elif role == "assistant":
                prompt += assistant_template.format(prompt=content)
            # Skip system messages (already handled above)

    logger.info(f"  → Template applied ({len(prompt)} chars)")
    return prompt
