"""
CursorCore assistant-conversation format transformation.

Handles conversion of OpenAI Chat Completions format messages
into the CursorCore-specific prompt format with special tokens.

Special Tokens:
- <|im_start|> / <|im_end|> - Message boundaries
- <|next_start|> / <|next_end|> - Assistant response boundaries
"""

import re
from typing import Any

from universal_logging import get_logger

logger = get_logger(__name__)

_CODE_BLOCK_PATTERN = re.compile(r"```(\w+)?\n(.*?)```", re.DOTALL)


def extract_code_blocks(content: str) -> list[tuple[str, str]]:
    """Extract code blocks from markdown content."""
    matches = _CODE_BLOCK_PATTERN.findall(content)
    return [(lang or "text", code.strip()) for lang, code in matches]


def remove_code_blocks(content: str) -> str:
    """Remove code blocks from content, leaving only surrounding text."""
    cleaned = _CODE_BLOCK_PATTERN.sub("", content)
    return cleaned.strip()


def format_history_message(role: str, content: str) -> str:
    """Format a conversation history message in CursorCore format."""
    if role == "user":
        return f"<|im_start|>user\n{content}<|im_end|>"
    elif role == "assistant":
        return f"<|im_start|>assistant\n<|next_start|>{content}<|next_end|><|im_end|>"
    else:
        logger.warning(f"Unknown role in history: {role}, skipping")
        return ""


def format_cursorcore_prompt(
    messages: list[dict[str, str]], settings: dict[str, Any]
) -> str:
    """
    Format messages into CursorCore assistant-conversation prompt.

    Structure:
    1. System message
    2. Conversation history (if any)
    3. Current code block (if present)
    4. User instruction
    5. Assistant response start marker

    Args:
        messages: List of message dictionaries with 'role' and 'content' keys
        settings: Configuration settings (default_system_message, etc.)

    Returns:
        Formatted prompt string in CursorCore format

    Raises:
        ValueError: If messages list is empty
    """
    if not messages:
        raise ValueError("Cannot format CursorCore prompt from empty message list")

    logger.info(f"🔄 Formatting CursorCore prompt from {len(messages)} messages")

    prompt_parts: list[str] = []
    current_code: tuple[str, str] | None = None
    user_instruction: str | None = None
    system_message: str | None = None
    conversation_history: list[dict[str, str]] = []

    # Process messages to extract components
    for i, msg in enumerate(messages):
        role = msg.get("role")
        content = msg.get("content", "").strip()

        if not content:
            continue

        if role == "system":
            system_message = content

        elif role == "user":
            is_last_user = (i == len(messages) - 1) or all(
                m.get("role") != "user" for m in messages[i + 1 :]
            )

            if is_last_user:
                code_blocks = extract_code_blocks(content)
                remaining_text = remove_code_blocks(content)

                if code_blocks:
                    current_code = code_blocks[-1]

                if remaining_text:
                    user_instruction = remaining_text
                elif not code_blocks:
                    user_instruction = content
            else:
                conversation_history.append({"role": "user", "content": content})

        elif role == "assistant":
            conversation_history.append({"role": "assistant", "content": content})

    # Build the prompt in CursorCore format

    # 1. System message
    if system_message:
        prompt_parts.append(f"<|im_start|>system\n{system_message}<|im_end|>")
    else:
        default_system = settings.get(
            "default_system_message", "You are a helpful programming assistant."
        )
        prompt_parts.append(f"<|im_start|>system\n{default_system}<|im_end|>")

    # 2. Conversation history
    for hist_msg in conversation_history:
        formatted = format_history_message(hist_msg["role"], hist_msg["content"])
        if formatted:
            prompt_parts.append(formatted)

    # 3. Current code block
    if current_code:
        lang, code = current_code
        prompt_parts.append(f"<|im_start|>current\n```{lang}\n{code}\n```<|im_end|>")

    # 4. User instruction
    if user_instruction:
        prompt_parts.append(f"<|im_start|>user\n{user_instruction}<|im_end|>")
    elif not current_code:
        prompt_parts.append("<|im_start|>user\nPlease help me.<|im_end|>")

    # 5. Start assistant response
    prompt_parts.append("<|im_start|>assistant\n<|next_start|>\n")

    prompt = "\n".join(prompt_parts)
    logger.info(f"✅ CursorCore prompt formatted: {len(prompt)} chars")
    return prompt
