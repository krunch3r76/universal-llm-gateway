"""Anthropic response content -> OpenAI format translation.

Handles text, tool_use, mcp_tool_use, mcp_tool_result, server_tool_use,
native tool results (web_search, web_fetch, code_execution), tool_search,
and citation extraction.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


def convert_response_content(
    content: Any,
) -> tuple[dict[str, Any], str | None, list[dict[str, Any]], dict[str, Any]]:
    """Convert Anthropic response content blocks to an OpenAI assistant message.

    Returns (message_dict, finish_reason_override, citations, mcp_meta).
    mcp_meta keys: mcp_tool_names (list[str]), tool_search_ref_count (int).
    """
    if not isinstance(content, list):
        return {"role": "assistant", "content": ""}, None, [], {}

    text_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    citations: list[dict[str, Any]] = []
    mcp_tool_names: list[str] = []
    tool_search_ref_count: int = 0

    for block in content:
        if not isinstance(block, dict):
            continue
        block_type = str(block.get("type", ""))

        match block_type:
            case "text":
                text = block.get("text")
                if isinstance(text, str) and text:
                    text_parts.append(text)

            case "tool_use":
                tool_calls.append(_tool_use_to_openai(block, index=len(tool_calls)))

            case "server_tool_use" | "mcp_tool_use":
                mcp_tool_names.append(str(block.get("name", "")))

            case "mcp_tool_result":
                _extract_mcp_result_text(block, text_parts)

            case "web_search_tool_result" | "web_fetch_tool_result":
                rendered, extracted = _render_native_result(block)
                if rendered:
                    text_parts.append(rendered)
                citations.extend(extracted)

            case "code_execution_tool_result":
                rendered = _render_code_result(block)
                if rendered:
                    text_parts.append(rendered)

            case "tool_search_tool_result":
                tool_search_ref_count = len(block.get("tool_references", []))

            case "tool_search_tool_result_error":
                logger.warning("Tool search error: %s", block.get("error", "unknown"))

            case _:
                logger.warning(
                    "Unrecognized Anthropic response content block type=%r",
                    block_type,
                )

    text = "".join(text_parts).strip()
    message: dict[str, Any] = {
        "role": "assistant",
        "content": text if text else None,
    }
    if tool_calls:
        message["tool_calls"] = tool_calls

    mcp_meta: dict[str, Any] = {}
    if mcp_tool_names:
        mcp_meta["mcp_tool_names"] = mcp_tool_names
    if tool_search_ref_count:
        mcp_meta["tool_search_ref_count"] = tool_search_ref_count

    finish_override = "tool_calls" if tool_calls else None
    return message, finish_override, citations, mcp_meta


def _tool_use_to_openai(block: dict[str, Any], index: int) -> dict[str, Any]:
    tool_use_id = block.get("id")
    if not isinstance(tool_use_id, str) or not tool_use_id:
        tool_use_id = f"call_{index}"

    name = block.get("name")
    if not isinstance(name, str):
        name = ""

    arguments_obj = block.get("input", {})
    arguments_str = (
        arguments_obj if isinstance(arguments_obj, str) else json.dumps(arguments_obj)
    )

    return {
        "id": tool_use_id,
        "type": "function",
        "function": {"name": name, "arguments": arguments_str},
    }


def _render_native_result(
    block: dict[str, Any],
) -> tuple[str, list[dict[str, Any]]]:
    citations: list[dict[str, Any]] = []
    content = block.get("content")

    if isinstance(content, str):
        return content, citations
    if not isinstance(content, list):
        return "", citations

    text_parts: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type", ""))
        match item_type:
            case "text":
                text = item.get("text")
                if isinstance(text, str) and text:
                    text_parts.append(text)
            case "web_search_result" | "web_fetch_result":
                title = item.get("title")
                url = item.get("url")
                snippet = item.get("text") or item.get("snippet") or ""
                line = ""
                if isinstance(title, str) and isinstance(url, str) and title and url:
                    line = f"{title}\n{url}\n{snippet}".strip()
                    citations.append({"title": title, "url": url})
                else:
                    line = str(snippet).strip()
                if line:
                    text_parts.append(line)
            case _:
                continue

    return "\n\n".join(text_parts).strip(), citations


def _render_code_result(block: dict[str, Any]) -> str:
    content = block.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""

    parts: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type", ""))
        match item_type:
            case "text":
                text = item.get("text")
                if isinstance(text, str) and text:
                    parts.append(text)
            case "code_output":
                text = item.get("text")
                if isinstance(text, str) and text:
                    parts.append(f"```\n{text}\n```")
            case _:
                continue

    return "\n".join(parts).strip()


def _extract_mcp_result_text(block: dict[str, Any], text_parts: list[str]) -> None:
    """Extract text from mcp_tool_result (Anthropic-executed, server-side).

    Surfaces tool output inline so the client sees it as response text.
    """
    content = block.get("content")
    if isinstance(content, str) and content:
        text_parts.append(content)
        return
    if not isinstance(content, list):
        return
    for item in content:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "text":
            text = item.get("text")
            if isinstance(text, str) and text:
                text_parts.append(text)
