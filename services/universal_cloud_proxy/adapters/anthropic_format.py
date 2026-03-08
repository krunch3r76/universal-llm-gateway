"""Stateless translation between OpenAI and Anthropic API formats.

Handles: vision content blocks, tool definitions, tool choice, tool-bearing
messages, tool_use response blocks, and native hosted tool definitions.

All functions are pure — no I/O, no adapter state, no HTTP.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

_DATA_URI_RE = re.compile(
    r"^data:(?P<media_type>image/[a-zA-Z0-9.+-]+);base64,(?P<data>.+)$",
    re.DOTALL,
)

_NATIVE_TOOL_PREFIXES = ("web_search_", "web_fetch_", "code_execution_")

_NATIVE_TOOL_NAMES: dict[str, str] = {
    "web_search_": "web_search",
    "web_fetch_": "web_fetch",
    "code_execution_": "code_execution",
}


# ---------------------------------------------------------------------------
# Content Block Translation (Vision)
# ---------------------------------------------------------------------------


def convert_content_blocks(
    content: str | list[dict[str, Any]] | None,
) -> str | list[dict[str, Any]]:
    """Translate OpenAI content (string or multimodal parts) to Anthropic format."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content)

    blocks: list[dict[str, Any]] = []
    for part in content:
        if isinstance(part, str):
            blocks.append({"type": "text", "text": part})
            continue
        if not isinstance(part, dict):
            logger.warning("Skipping non-dict content part: %r", type(part))
            continue

        block_type = str(part.get("type", ""))
        match block_type:
            case "text":
                text = part.get("text")
                if isinstance(text, str) and text:
                    blocks.append({"type": "text", "text": text})
            case "image_url":
                image_block = _convert_image_url_part(part)
                if image_block is not None:
                    blocks.append(image_block)
            case _:
                logger.warning(
                    "Passing through unrecognized content block type=%r", block_type
                )
                blocks.append(part)

    return blocks if blocks else ""


def _convert_image_url_part(part: dict[str, Any]) -> dict[str, Any] | None:
    image_url_obj = part.get("image_url")
    if not isinstance(image_url_obj, dict):
        logger.warning("image_url part missing image_url object")
        return None

    url = image_url_obj.get("url")
    if not isinstance(url, str) or not url:
        logger.warning("image_url part has invalid url")
        return None

    match = _DATA_URI_RE.match(url)
    if match:
        return {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": match.group("media_type"),
                "data": match.group("data"),
            },
        }

    return {"type": "image", "source": {"type": "url", "url": url}}


# ---------------------------------------------------------------------------
# System Text Extraction
# ---------------------------------------------------------------------------


def extract_system_text(openai_messages: list[dict[str, Any]]) -> str:
    """Extract and concatenate all system messages into a single string."""
    parts: list[str] = []
    for msg in openai_messages:
        if not isinstance(msg, dict) or msg.get("role") != "system":
            continue
        text = _coerce_any_content_to_text(msg.get("content"))
        if text:
            parts.append(text)
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Tool Calling Translation (Request Side)
# ---------------------------------------------------------------------------


def convert_tools(openai_tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Translate OpenAI function-tool definitions to Anthropic tool format.

    Native Anthropic tools (web_search_*, web_fetch_*, code_execution_*) pass
    through unchanged since they already use Anthropic's schema.
    """
    converted: list[dict[str, Any]] = []
    for tool in openai_tools:
        if not isinstance(tool, dict):
            continue

        tool_type = tool.get("type")
        if isinstance(tool_type, str) and _is_native_tool_type(tool_type):
            converted.append(tool)
            continue

        if tool_type != "function":
            logger.warning("Skipping unsupported tool type=%r", tool_type)
            continue

        func = tool.get("function")
        if not isinstance(func, dict):
            logger.warning("Skipping function tool without function object")
            continue

        name = func.get("name")
        if not isinstance(name, str) or not name:
            logger.warning("Skipping function tool with invalid name")
            continue

        input_schema = func.get("parameters")
        if not isinstance(input_schema, dict):
            input_schema = {"type": "object", "properties": {}}

        anthropic_tool: dict[str, Any] = {"name": name, "input_schema": input_schema}
        description = func.get("description")
        if isinstance(description, str) and description:
            anthropic_tool["description"] = description

        converted.append(anthropic_tool)

    return converted


def convert_tool_choice(
    choice: str | dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Translate OpenAI tool_choice to Anthropic tool_choice.

    Returns None when the choice should be omitted from the request.
    OpenAI "none" has no Anthropic equivalent — caller should omit tools.
    """
    if choice is None:
        return None

    if isinstance(choice, str):
        match choice:
            case "auto":
                return {"type": "auto"}
            case "required":
                return {"type": "any"}
            case "none":
                return None
            case _:
                logger.warning(
                    "Unknown tool_choice string=%r; defaulting to auto", choice
                )
                return {"type": "auto"}

    if isinstance(choice, dict):
        func = choice.get("function")
        if isinstance(func, dict) and isinstance(func.get("name"), str):
            return {"type": "tool", "name": func["name"]}

    return None


# ---------------------------------------------------------------------------
# Message Translation (Tool Roles)
# ---------------------------------------------------------------------------


def convert_messages(openai_messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Translate OpenAI messages to Anthropic format, including tool semantics.

    - role=system is omitted (handled separately via extract_system_text)
    - assistant.tool_calls -> content blocks of text + tool_use
    - role=tool -> grouped into tool_result blocks on a user message
    """
    anthropic_messages: list[dict[str, Any]] = []
    pending_tool_results: list[dict[str, Any]] = []

    for msg in openai_messages:
        if not isinstance(msg, dict):
            continue

        role = str(msg.get("role", "user"))

        if role != "tool" and pending_tool_results:
            anthropic_messages.append({"role": "user", "content": pending_tool_results})
            pending_tool_results = []

        match role:
            case "system":
                continue

            case "tool":
                tool_use_id = msg.get("tool_call_id")
                if not isinstance(tool_use_id, str):
                    tool_use_id = ""

                tool_result: dict[str, Any] = {
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                }
                tool_content = msg.get("content")
                if isinstance(tool_content, str):
                    tool_result["content"] = tool_content
                elif tool_content is None:
                    tool_result["content"] = ""
                else:
                    tool_result["content"] = convert_content_blocks(tool_content)

                pending_tool_results.append(tool_result)

            case "assistant":
                tool_calls = msg.get("tool_calls")
                if isinstance(tool_calls, list) and tool_calls:
                    anthropic_messages.append(
                        _build_assistant_tool_message(msg, tool_calls)
                    )
                else:
                    anthropic_messages.append(
                        {
                            "role": "assistant",
                            "content": convert_content_blocks(msg.get("content")),
                        }
                    )

            case _:
                anthropic_messages.append(
                    {
                        "role": "user",
                        "content": convert_content_blocks(msg.get("content")),
                    }
                )

    if pending_tool_results:
        anthropic_messages.append({"role": "user", "content": pending_tool_results})

    return anthropic_messages


def _build_assistant_tool_message(
    msg: dict[str, Any],
    tool_calls: list[dict[str, Any]],
) -> dict[str, Any]:
    blocks: list[dict[str, Any]] = []

    text_content = msg.get("content")
    assistant_text = _coerce_any_content_to_text(text_content)
    if assistant_text:
        blocks.append({"type": "text", "text": assistant_text})

    for tc in tool_calls:
        tool_use = _openai_tool_call_to_tool_use(tc)
        if tool_use is not None:
            blocks.append(tool_use)

    return {"role": "assistant", "content": blocks}


def _openai_tool_call_to_tool_use(tool_call: Any) -> dict[str, Any] | None:
    if not isinstance(tool_call, dict):
        return None
    if tool_call.get("type") != "function":
        logger.warning("Unsupported tool_call type=%r", tool_call.get("type"))
        return None

    func = tool_call.get("function")
    if not isinstance(func, dict):
        return None
    name = func.get("name")
    if not isinstance(name, str) or not name:
        return None

    arguments = func.get("arguments", "{}")
    if isinstance(arguments, str):
        try:
            arguments_json = json.loads(arguments) if arguments.strip() else {}
        except json.JSONDecodeError:
            arguments_json = {}
    elif isinstance(arguments, dict):
        arguments_json = arguments
    else:
        arguments_json = {}

    tool_use_id = tool_call.get("id")
    if not isinstance(tool_use_id, str) or not tool_use_id:
        tool_use_id = ""

    return {
        "type": "tool_use",
        "id": tool_use_id,
        "name": name,
        "input": arguments_json,
    }


# ---------------------------------------------------------------------------
# Native Hosted Tools
# ---------------------------------------------------------------------------


def build_native_tools(
    native_tool_type_ids: list[str],
) -> list[dict[str, Any]]:
    """Build Anthropic-native tool descriptors from configured type IDs.

    Server-side tools require both ``type`` (versioned identifier) and ``name``
    (canonical tool name).  The name is derived from the type prefix — e.g.
    ``web_search_20260209`` → ``name: "web_search"``.
    """
    tools: list[dict[str, Any]] = []
    for type_id in native_tool_type_ids:
        if not isinstance(type_id, str):
            continue
        type_id = type_id.strip()
        if not type_id:
            continue
        if not _is_native_tool_type(type_id):
            logger.warning("Ignoring non-native tool id in native_tools=%r", type_id)
            continue
        name = _derive_native_tool_name(type_id)
        tools.append({"type": type_id, "name": name})
    return tools


def _derive_native_tool_name(type_id: str) -> str:
    """Derive the canonical tool name from a versioned type identifier."""
    for prefix, name in _NATIVE_TOOL_NAMES.items():
        if type_id.startswith(prefix):
            return name
    return type_id


def dedupe_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove duplicate tools by native type or name."""
    seen_native: set[str] = set()
    seen_names: set[str] = set()
    result: list[dict[str, Any]] = []

    for tool in tools:
        if not isinstance(tool, dict):
            continue
        tool_type = tool.get("type")
        if isinstance(tool_type, str) and _is_native_tool_type(tool_type):
            if tool_type in seen_native:
                continue
            seen_native.add(tool_type)
            result.append(tool)
            continue

        name = tool.get("name")
        if isinstance(name, str) and name:
            if name in seen_names:
                continue
            seen_names.add(name)
        result.append(tool)

    return result


def _is_native_tool_type(tool_type: str) -> bool:
    return any(tool_type.startswith(prefix) for prefix in _NATIVE_TOOL_PREFIXES)


def _coerce_any_content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(p for p in parts if p)
    return ""
