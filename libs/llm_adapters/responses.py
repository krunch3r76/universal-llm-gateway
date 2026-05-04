"""Responses API adapter for xAI Grok and OpenAI — Bearer auth, function calling.

MCP tool calling uses client-side tool resolution (function_call items executed
locally by the caller's tool loop).  No remote MCP tool injection
(``type: "mcp"``) is applied automatically.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from llm_adapters._mcp_entry import (
    openai_xai_mcp_tool_entry,
    resolve_mcp_env,
)
from llm_adapters._tool_schema import sanitize_tool_parameters

if TYPE_CHECKING:
    from llm_adapters import FrontierRequest, LLMRequest

logger = logging.getLogger(__name__)


def _normalize_tool_for_responses_api(tool: dict[str, Any]) -> dict[str, Any]:
    """Flatten Chat Completions function format to Responses API format.

    Chat Completions: {"type": "function", "function": {"name": ..., "description": ..., "parameters": ...}}
    Responses API:    {"type": "function", "name": ..., "description": ..., "parameters": ...}

    Server tools (web_search, x_search, etc.) and already-flat tools pass through unchanged.
    """
    fn = tool.get("function")
    if not isinstance(fn, dict):
        return tool
    flat: dict[str, Any] = {"type": "function"}
    if "name" in fn:
        flat["name"] = fn["name"]
    if "description" in fn:
        flat["description"] = fn["description"]
    if "parameters" in fn:
        params = fn["parameters"]
        if isinstance(params, dict):
            flat["parameters"] = sanitize_tool_parameters(params)
        else:
            flat["parameters"] = params
    for k, v in fn.items():
        if k not in flat:
            flat[k] = v
    return flat


def _normalize_input_content(content: Any) -> Any:
    """Normalize message content for Responses API input messages.

    The Responses API uses ``"type": "input_text"`` for input content blocks,
    not ``"type": "text"`` (which is the output block type). Callers that build
    messages with Chat-Completions-style ``{"type": "text", "text": "..."}``
    blocks must be translated before sending to the Responses API.

    String content passes through unchanged.
    Single-text-block arrays are flattened to plain strings (simplest form).
    Multi-block arrays have text blocks translated to ``input_text`` type.
    """
    if not isinstance(content, list):
        return content
    text_only = all(
        isinstance(b, dict) and b.get("type") in {"text", "input_text"} for b in content
    )
    if text_only and len(content) == 1:
        return str(content[0].get("text", ""))
    normalized: list[dict[str, Any]] = []
    for block in content:
        if not isinstance(block, dict):
            normalized.append(block)
            continue
        if block.get("type") == "text":
            normalized.append({"type": "input_text", "text": block.get("text", "")})
        else:
            normalized.append(block)
    return normalized


def _xai_supports_reasoning_effort(model: str) -> bool:
    """Only grok-3 family accepts reasoning.effort control.

    All grok-4 family models (including -reasoning variants) reject
    reasoningEffort despite xAI docs suggesting otherwise (tested 2026-03-31).
    """
    return any(prefix in model for prefix in ("grok-3-mini", "grok-3"))


def _openai_supports_reasoning_effort(model: str) -> bool:
    """Only OpenAI reasoning model families accept reasoning.effort.

    The o-series (o1, o3, o4-mini) and gpt-5 family (gpt-5.x, gpt-5.x-*)
    support the Responses API reasoning.effort parameter. Standard chat
    models (gpt-4o, gpt-4.1, gpt-4o-mini) reject it with
    'unsupported_parameter' — as does any model that routes through the
    Chat Completions API surface instead of Responses (those are caught
    earlier at frontier_dispatch admission via _is_chat_completions_only).
    ∀ new reasoning-capable OpenAI model: confirm it starts with one of
    these prefixes or extend this set.
    """
    return any(model.startswith(prefix) for prefix in ("o1", "o3", "o4", "gpt-5"))


class ResponsesAPIAdapter:
    """OpenAI-compatible Responses API (xAI Grok, OpenAI) — Bearer auth."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        vendor: str,
    ) -> None:
        self._api_key = api_key
        self._base = base_url.rstrip("/")
        self._vendor = vendor

    @property
    def provider_label(self) -> str:
        return self._vendor

    def build_request(
        self,
        req: LLMRequest,
    ) -> tuple[str, dict[str, str], dict[str, Any]]:
        headers = {
            "authorization": f"Bearer {self._api_key}",
            "content-type": "application/json",
        }
        input_msgs: list[dict[str, Any]] = []
        if req.system.strip():
            input_msgs.append({"role": "system", "content": req.system})
        for msg in req.messages:
            content = _normalize_input_content(msg.get("content"))
            input_msgs.append({**msg, "content": content})
        body: dict[str, Any] = {
            "model": req.model,
            "input": input_msgs,
            "store": False,
        }
        if req.max_tokens is not None:
            body["max_output_tokens"] = req.max_tokens
        if req.temperature is not None:
            body["temperature"] = req.temperature
        if req.top_p is not None:
            body["top_p"] = req.top_p
        if req.stop_sequences is not None:
            body["stop"] = req.stop_sequences
        if req.seed is not None:
            body["seed"] = req.seed
        url = f"{self._base}/responses"
        return url, headers, body

    def build_frontier_request(
        self,
        req: FrontierRequest,
    ) -> tuple[str, dict[str, str], dict[str, Any]]:
        headers: dict[str, str] = {
            "authorization": f"Bearer {self._api_key}",
            "content-type": "application/json",
        }

        if req.conversation_id and self._vendor == "xai":
            headers["x-grok-conv-id"] = req.conversation_id

        input_msgs: list[dict[str, Any]] = []
        if req.system.strip():
            input_msgs.append({"role": "system", "content": req.system})

        if req.reasoning_trace:
            input_msgs.extend(req.reasoning_trace)

        for msg in req.messages:
            content = _normalize_input_content(msg.get("content"))
            input_msgs.append({**msg, "content": content})

        body: dict[str, Any] = {
            "model": req.model,
            "input": input_msgs,
            "store": False,
        }
        responses_min_output_tokens = 16384
        if req.max_tokens is not None:
            effective = max(req.max_tokens, responses_min_output_tokens)
            if effective != req.max_tokens:
                logger.info(
                    "Bumped max_tokens from %d to %d for model=%s (frontier floor)",
                    req.max_tokens,
                    effective,
                    req.model,
                )
            body["max_output_tokens"] = effective
        if req.temperature is not None:
            body["temperature"] = req.temperature
        if req.top_p is not None:
            body["top_p"] = req.top_p
        if req.stop_sequences is not None:
            body["stop"] = req.stop_sequences
        if req.seed is not None:
            body["seed"] = req.seed

        if req.thinking:
            if req.thinking.get("include_encrypted"):
                body.setdefault("include", []).append("reasoning.encrypted_content")
            effort = req.thinking.get("effort")
            if effort:
                if self._vendor == "xai" and _xai_supports_reasoning_effort(req.model):
                    body["reasoning"] = {"effort": effort}
                elif self._vendor == "xai":
                    logger.warning(
                        "Dropping unsupported reasoning.effort=%s for model=%s "
                        "(grok-4 family has built-in reasoning, "
                        "does not accept effort control)",
                        effort,
                        req.model,
                    )
                elif self._vendor == "openai" and not _openai_supports_reasoning_effort(
                    req.model
                ):
                    logger.warning(
                        "Dropping unsupported reasoning.effort=%s for model=%s "
                        "(only o-series and gpt-5 family accept reasoning.effort; "
                        "pass a reasoning model or omit reasoning_effort)",
                        effort,
                        req.model,
                    )
                else:
                    body["reasoning"] = {"effort": effort}

        if req.response_format:
            fmt = req.response_format
            json_schema = fmt.get("json_schema") if isinstance(fmt, dict) else None
            if isinstance(json_schema, dict):
                body["text"] = {
                    "format": {
                        "type": "json_schema",
                        "name": json_schema.get("name", "response"),
                        "schema": json_schema["schema"],
                        "strict": json_schema.get("strict", True),
                    }
                }
            else:
                body["text"] = {"format": fmt}

        if req.remote_mcp:
            if req.mcp_tool_loop:
                raise ValueError(
                    "remote_mcp=True is mutually exclusive with mcp_tool_loop=True"
                )
            if req.tools:
                for t in req.tools:
                    if not isinstance(t, dict) or t.get("type") != "mcp":
                        raise ValueError(
                            "remote_mcp=True only accepts provider-native "
                            'MCP tool entries (type=="mcp"); got '
                            f"type={t.get('type') if isinstance(t, dict) else type(t).__name__}"
                        )
            url, token = resolve_mcp_env()
            existing_tools = list(req.tools or [])
            body["tools"] = [
                *existing_tools,
                openai_xai_mcp_tool_entry(url, token),
            ]
        else:
            tools_list: list[dict[str, Any]] = []
            if req.tools:
                for tool in req.tools:
                    tools_list.append(_normalize_tool_for_responses_api(tool))
            if tools_list:
                body["tools"] = tools_list
        if req.tool_choice is not None:
            body["tool_choice"] = req.tool_choice

        vendor_opts = (req.provider_options or {}).get(self._vendor, {})
        for k, v in vendor_opts.items():
            if k not in body:
                body[k] = v

        url = f"{self._base}/responses"
        return url, headers, body

    def parse_frontier_response(self, response_data: dict[str, Any]) -> dict[str, Any]:
        content_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        server_tool_calls: list[dict[str, Any]] = []
        encrypted_content: list[dict[str, Any]] = []

        for item in response_data.get("output") or []:
            if not isinstance(item, dict):
                continue
            item_type = item.get("type")

            if item_type == "message":
                for block in item.get("content") or []:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "output_text" and block.get("text"):
                        content_parts.append(str(block["text"]))
                    elif block.get("type") == "refusal":
                        content_parts.append(f"[REFUSAL] {block.get('refusal', '')}")

            elif item_type == "reasoning":
                enc = item.get("encrypted_content")
                if enc:
                    encrypted_content.append(item)

            elif item_type == "function_call":
                tool_calls.append(
                    {
                        "id": item.get("call_id"),
                        "name": item.get("name"),
                        "arguments": item.get("arguments"),
                    }
                )

            elif item_type in {
                "web_search_call",
                "x_search_call",
                "code_interpreter_call",
                "file_search_call",
                "mcp_call",
            }:
                server_tool_calls.append(item)

        if not content_parts:
            ot = response_data.get("output_text")
            if isinstance(ot, str) and ot.strip():
                content_parts.append(ot)

        u = response_data.get("usage") or {}
        inp = u.get("input_tokens") or u.get("prompt_tokens") or 0
        out = u.get("output_tokens") or u.get("completion_tokens") or 0
        reasoning_tokens = u.get("reasoning_tokens") or None
        cached = u.get("input_tokens_details", {}).get("cached_tokens") or None

        usage: dict[str, Any] = {
            "input_tokens": int(inp),
            "output_tokens": int(out),
            "reasoning_tokens": int(reasoning_tokens) if reasoning_tokens else None,
            "cached_tokens": int(cached) if cached else None,
        }

        thinking: dict[str, Any] | None = None
        if encrypted_content:
            thinking = {
                "text": None,
                "encrypted_content": encrypted_content,
                "tokens": int(reasoning_tokens) if reasoning_tokens else 0,
            }

        # For xAI grok-4 family (built-in reasoning, no separate "reasoning" block in
        # Responses API output): populate reasoning field so NativeLoopResult and
        # frontier_dispatch callers see the chain.
        reasoning = thinking
        if reasoning is None and self._vendor == "xai":
            # grok-4 family uses built-in reasoning; surface main content as reasoning
            # when no dedicated block is present (matches test_responses_reasoning_effort.py)
            if reasoning_tokens and int(reasoning_tokens) > 0:
                reasoning = {"text": "".join(content_parts), "tokens": int(reasoning_tokens)}
            elif "grok-4" in str(response_data.get("model", "")).lower():
                reasoning = {"text": "".join(content_parts[:500]), "tokens": 0}  # summary for visibility

        return {
            "content": "".join(content_parts),
            "model": str(response_data.get("model", "")),
            "provider": self._vendor,
            "usage": usage,
            "thinking": thinking,
            "reasoning": reasoning,
            "tool_calls": tool_calls or None,
            "server_tool_calls": server_tool_calls or None,
            "response_id": response_data.get("id"),
            "raw": None,
        }

    def append_tool_round(
        self,
        body: dict[str, Any],
        raw_response: dict[str, Any],
        tool_results: list[dict[str, Any]],
    ) -> None:
        """Append function_call + function_call_output items for the next Responses API turn."""
        for item in raw_response.get("output", []):
            if isinstance(item, dict) and item.get("type") == "function_call":
                body["input"].append(item)
        for tr in tool_results:
            body["input"].append(
                {
                    "type": "function_call_output",
                    "call_id": tr["id"],
                    "output": tr["content"],
                }
            )

    def extract_text(self, response_data: dict[str, Any]) -> str:
        ot = response_data.get("output_text")
        if isinstance(ot, str) and ot.strip():
            return ot
        parts: list[str] = []
        for item in response_data.get("output") or []:
            if not isinstance(item, dict):
                continue
            for block in item.get("content") or []:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "output_text" and block.get("text"):
                    parts.append(str(block["text"]))
        return "".join(parts)

    def extract_usage(self, response_data: dict[str, Any]) -> dict[str, int]:
        u = response_data.get("usage") or {}
        inp = u.get("input_tokens")
        if inp is None:
            inp = u.get("prompt_tokens")
        out = u.get("output_tokens")
        if out is None:
            out = u.get("completion_tokens")
        return {
            "input_tokens": int(inp or 0),
            "output_tokens": int(out or 0),
        }
