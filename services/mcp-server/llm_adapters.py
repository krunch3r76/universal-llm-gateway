"""Provider adapters for MCP LLM proxy and llm_generate — Anthropic Messages vs Responses API."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Protocol

_ANTHROPIC_VERSION = "2023-06-01"
_ANTHROPIC_BETA_MCP = "mcp-client-2025-11-20"
_ANTHROPIC_BETA_INTERLEAVED = "interleaved-thinking-2025-05-14"
_ANTHROPIC_BETA_CONTEXT = "context-management-2025-06-27"
_ANTHROPIC_BETA_COMPACT = "compact-2026-01-12"
_ANTHROPIC_BETA_FAST = "fast-mode-2026-02-01"
_ANTHROPIC_SERVER_TOOL_VERSION_MAP = {
    "web_search": "web_search_20260209",
    "web_fetch": "web_fetch_20250910",
    "code_execution": "code_execution_20260120",
}
logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class LLMRequest:
    """Normalized request for adapter build_request (tool + HTTP proxy paths)."""

    messages: list[dict[str, Any]]
    model: str
    max_tokens: int = 4096
    system: str = ""
    inject_mcp: bool = True
    # Generation parameters — None means "omit from request" (use provider default)
    temperature: float | None = None
    top_p: float | None = None
    stop_sequences: list[str] | None = None
    seed: int | None = None  # supported by xAI / OpenAI; silently ignored for Anthropic


@dataclass(frozen=True, slots=True)
class MCPConfig:
    """MCP server injection — URL, logical name, raw bearer token (no \"Bearer \" prefix)."""

    server_url: str
    server_name: str
    auth_token: str


@dataclass(frozen=True, slots=True)
class FrontierRequest:
    """Full-fidelity request preserving vendor-native features (thinking, tools, cache affinity)."""

    messages: list[dict[str, Any]]
    model: str
    max_tokens: int | None = None
    system: str = ""
    inject_mcp: bool = True
    # Generation
    temperature: float | None = None
    top_p: float | None = None
    stop_sequences: list[str] | None = None
    seed: int | None = None
    stream: bool = False
    # Thinking / Reasoning
    thinking: dict[str, Any] | None = None
    effort: str | None = None
    # Tools (function calling + server-side)
    tools: list[dict[str, Any]] | None = None
    tool_choice: str | dict[str, Any] | None = None
    # Structured output
    response_format: dict[str, Any] | None = None
    boot: str = "none"
    boot_ref: str | None = None
    # Multi-turn state
    conversation_id: str | None = None
    reasoning_trace: list[dict[str, Any]] | None = None
    # Escape hatch
    provider_options: dict[str, Any] | None = None


class FrontierAdapter(Protocol):
    """Build vendor-native requests and parse structured responses preserving full fidelity."""

    def build_frontier_request(
        self, req: FrontierRequest, mcp: MCPConfig | None
    ) -> tuple[str, dict[str, str], dict[str, Any]]:
        """Return (full_url, headers, json_body) for a frontier request."""
        ...

    def parse_frontier_response(self, response_data: dict[str, Any]) -> dict[str, Any]:
        """Return structured response: content, thinking, tool_calls, usage, response_id."""
        ...


class LLMAdapter(Protocol):
    """Build upstream HTTP request and parse provider-native JSON responses."""

    @property
    def provider_label(self) -> str:
        """Short name for telemetry (e.g. anthropic, xai)."""
        ...

    def build_request(
        self, req: LLMRequest, mcp: MCPConfig | None
    ) -> tuple[str, dict[str, str], dict[str, Any]]:
        """Return (full_url, headers, json_body)."""

    def extract_text(self, response_data: dict[str, Any]) -> str:
        """Best-effort assistant text for normalized tool responses."""

    def extract_usage(self, response_data: dict[str, Any]) -> dict[str, int]:
        """input_tokens / output_tokens for telemetry."""


def flatten_anthropic_system(system: Any) -> str:
    """Turn Anthropic system (str or content blocks) into a plain string."""
    if system is None or system == "":
        return ""
    if isinstance(system, str):
        return system
    if isinstance(system, list):
        parts: list[str] = []
        for block in system:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(parts)
    return str(system)


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            continue
        clean = value.strip()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        deduped.append(clean)
    return deduped


def _is_claude4_model(model: str) -> bool:
    return any(
        marker in model for marker in ("claude-sonnet-4", "claude-opus-4", "claude-4")
    )


def _build_anthropic_output_format(response_format: dict[str, Any]) -> dict[str, Any]:
    if response_format.get("type") != "json_schema":
        return dict(response_format)

    json_schema = response_format.get("json_schema")
    if not isinstance(json_schema, dict):
        return dict(response_format)

    output_format: dict[str, Any] = {"type": "json_schema"}
    schema = json_schema.get("schema")
    if schema is not None:
        output_format["schema"] = schema
    if "schema" not in output_format and response_format.get("schema") is not None:
        output_format["schema"] = response_format["schema"]
    return output_format


def _build_anthropic_thinking(thinking: dict[str, Any] | None) -> dict[str, Any] | None:
    if not thinking:
        return None

    thinking_type = thinking.get("type")
    thinking_mode = thinking.get("mode")
    display = thinking.get("display")

    if thinking_type == "adaptive" or thinking_mode == "adaptive":
        config: dict[str, Any] = {"type": "adaptive"}
        if display is not None:
            config["display"] = display
        return config

    budget_tokens = thinking.get("budget_tokens")
    if isinstance(budget_tokens, int) and budget_tokens > 0:
        config = {"type": "enabled", "budget_tokens": budget_tokens}
        if display is not None:
            config["display"] = display
        return config

    if (
        thinking_type == "disabled"
        or thinking_mode == "disabled"
        or thinking.get("enabled") is False
    ):
        return {"type": "disabled"}

    return None


def _resolve_anthropic_max_tokens(
    requested_max_tokens: int | None,
    thinking_config: dict[str, Any] | None,
    *,
    model: str,
) -> int | None:
    """Ensure Anthropic's max_tokens > thinking.budget_tokens invariant."""
    if not isinstance(thinking_config, dict) or thinking_config.get("type") != "enabled":
        return requested_max_tokens

    budget_tokens = thinking_config.get("budget_tokens")
    if not isinstance(budget_tokens, int) or budget_tokens < 1:
        return requested_max_tokens

    if isinstance(requested_max_tokens, int) and requested_max_tokens > budget_tokens:
        return requested_max_tokens

    bumped_max_tokens = budget_tokens * 2
    logger.warning(
        "Auto-bumped Anthropic max_tokens from %s to %d for model=%s to satisfy "
        "max_tokens > thinking.budget_tokens",
        requested_max_tokens,
        bumped_max_tokens,
        model,
    )
    return bumped_max_tokens


def _build_anthropic_tool(tool: dict[str, Any]) -> dict[str, Any] | None:
    tool_type = tool.get("type")
    if tool_type == "function":
        return {
            "name": tool["name"],
            "description": tool.get("description", ""),
            "input_schema": tool.get("parameters", {}),
        }

    if isinstance(tool_type, str) and tool_type in _ANTHROPIC_SERVER_TOOL_VERSION_MAP:
        return {
            "type": _ANTHROPIC_SERVER_TOOL_VERSION_MAP[tool_type],
            "name": tool_type,
        }

    return None


class AnthropicAdapter:
    """Anthropic Messages API — x-api-key, mcp_servers + anthropic-beta."""

    def __init__(self, *, api_key: str, base_url: str | None = None) -> None:
        self._api_key = api_key
        self._base = (
            base_url or os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
        ).rstrip("/")

    @property
    def provider_label(self) -> str:
        return "anthropic"

    def build_request(
        self, req: LLMRequest, mcp: MCPConfig | None
    ) -> tuple[str, dict[str, str], dict[str, Any]]:
        headers: dict[str, str] = {
            "x-api-key": self._api_key,
            "anthropic-version": _ANTHROPIC_VERSION,
            "content-type": "application/json",
        }
        body: dict[str, Any] = {
            "model": req.model,
            "max_tokens": req.max_tokens,
            "messages": req.messages,
        }
        if req.system:
            body["system"] = req.system
        if req.temperature is not None:
            body["temperature"] = req.temperature
        if req.top_p is not None:
            body["top_p"] = req.top_p
        if req.stop_sequences is not None:
            body["stop_sequences"] = req.stop_sequences
        # seed is not supported by Anthropic Messages API — silently omitted
        if mcp is not None and req.inject_mcp:
            mcp_def: dict[str, Any] = {
                "type": "url",
                "url": mcp.server_url,
                "name": mcp.server_name,
            }
            if mcp.auth_token:
                mcp_def["authorization_token"] = mcp.auth_token
            body["mcp_servers"] = [mcp_def]
            body["tools"] = [
                {"type": "mcp_toolset", "mcp_server_name": mcp.server_name}
            ]
            headers["anthropic-beta"] = _ANTHROPIC_BETA_MCP
        url = f"{self._base}/v1/messages"
        return url, headers, body

    def build_frontier_request(
        self, req: FrontierRequest, mcp: MCPConfig | None
    ) -> tuple[str, dict[str, str], dict[str, Any]]:
        headers: dict[str, str] = {
            "x-api-key": self._api_key,
            "anthropic-version": _ANTHROPIC_VERSION,
            "content-type": "application/json",
        }
        body: dict[str, Any] = {
            "model": req.model,
            "messages": list(req.messages),
        }
        if req.system:
            body["system"] = req.system
        if req.temperature is not None:
            body["temperature"] = req.temperature
        if req.top_p is not None:
            body["top_p"] = req.top_p
        if req.stop_sequences is not None:
            body["stop_sequences"] = req.stop_sequences

        # Thinking / extended thinking
        thinking_config = _build_anthropic_thinking(req.thinking)
        if thinking_config is not None:
            body["thinking"] = thinking_config
        resolved_max_tokens = _resolve_anthropic_max_tokens(
            req.max_tokens, thinking_config, model=req.model
        )
        if resolved_max_tokens is not None:
            body["max_tokens"] = resolved_max_tokens

        # Tools — function calling and Anthropic-hosted tools
        tools_list: list[dict[str, Any]] = []
        if req.tools:
            for t in req.tools:
                mapped_tool = _build_anthropic_tool(t)
                if mapped_tool is not None:
                    tools_list.append(mapped_tool)
        if mcp is not None and req.inject_mcp:
            mcp_def: dict[str, Any] = {
                "type": "url",
                "url": mcp.server_url,
                "name": mcp.server_name,
            }
            if mcp.auth_token:
                mcp_def["authorization_token"] = mcp.auth_token
            tools_list.append(
                {"type": "mcp_toolset", "mcp_server_name": mcp.server_name}
            )
            body["mcp_servers"] = [mcp_def]
        if tools_list:
            body["tools"] = tools_list
        if req.tool_choice is not None:
            body["tool_choice"] = req.tool_choice

        # Structured output + effort live under output_config for Anthropic.
        output_config: dict[str, Any] = {}
        if req.response_format:
            output_config["format"] = _build_anthropic_output_format(
                req.response_format
            )
        if req.effort is not None:
            output_config["effort"] = req.effort
        if output_config:
            body["output_config"] = output_config

        # Provider-specific overrides
        opts = (req.provider_options or {}).get("anthropic", {})
        for key, value in opts.items():
            if key == "betas":
                continue
            if key == "output_config" and isinstance(value, dict):
                merged = dict(value)
                merged.update(body.get("output_config", {}))
                body["output_config"] = merged
                continue
            if key not in body:
                body[key] = value

        auto_betas: list[str] = []
        if thinking_config is not None and _is_claude4_model(req.model):
            auto_betas.append(_ANTHROPIC_BETA_INTERLEAVED)

        context_management = body.get("context_management")
        if isinstance(context_management, dict):
            auto_betas.append(_ANTHROPIC_BETA_CONTEXT)
            edits = context_management.get("edits")
            if isinstance(edits, list) and any(
                isinstance(edit, dict)
                and str(edit.get("type", "")).startswith("compact")
                for edit in edits
            ):
                auto_betas.append(_ANTHROPIC_BETA_COMPACT)

        if opts.get("speed") == "fast":
            auto_betas.append(_ANTHROPIC_BETA_FAST)

        if (mcp is not None and req.inject_mcp) or body.get("mcp_servers"):
            auto_betas.append(_ANTHROPIC_BETA_MCP)

        explicit_betas = opts.get("betas", [])
        if isinstance(explicit_betas, list):
            beta_values = [*explicit_betas, *auto_betas]
        else:
            beta_values = auto_betas
        if beta_values:
            headers["anthropic-beta"] = ",".join(_dedupe_preserve_order(beta_values))

        url = f"{self._base}/v1/messages"
        return url, headers, body

    def parse_frontier_response(self, response_data: dict[str, Any]) -> dict[str, Any]:
        content_parts: list[str] = []
        thinking_parts: list[str] = []
        thinking_blocks: list[dict[str, Any]] = []
        thinking_tokens = 0
        tool_calls: list[dict[str, Any]] = []
        server_tool_calls: list[dict[str, Any]] = []

        for block in response_data.get("content") or []:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text":
                content_parts.append(str(block.get("text", "")))
            elif btype == "thinking":
                thinking_blocks.append(block)
                thinking_parts.append(str(block.get("thinking", "")))
            elif btype == "redacted_thinking":
                thinking_blocks.append(block)
            elif btype == "tool_use":
                tool_calls.append(
                    {
                        "id": block.get("id"),
                        "name": block.get("name"),
                        "input": block.get("input"),
                    }
                )
            elif btype in {
                "server_tool_use",
                "web_search_tool_result",
                "web_fetch_tool_result",
                "code_execution_tool_result",
            }:
                server_tool_calls.append(block)

        u = response_data.get("usage") or {}
        usage: dict[str, Any] = {
            "input_tokens": int(u.get("input_tokens") or 0),
            "output_tokens": int(u.get("output_tokens") or 0),
            "reasoning_tokens": None,
            "cached_tokens": int(u.get("cache_read_input_tokens") or 0) or None,
        }

        thinking: dict[str, Any] | None = None
        if thinking_blocks:
            thinking_tokens = int(u.get("thinking_tokens") or 0)
            thinking = {
                "text": "".join(thinking_parts) or None,
                "encrypted_content": None,
                "tokens": thinking_tokens,
                "blocks": thinking_blocks,
            }
            usage["reasoning_tokens"] = thinking_tokens

        return {
            "content": "".join(content_parts),
            "model": str(response_data.get("model", "")),
            "provider": "anthropic",
            "usage": usage,
            "thinking": thinking,
            "tool_calls": tool_calls or None,
            "server_tool_calls": server_tool_calls or None,
            "response_id": response_data.get("id"),
            "raw": None,
        }

    def extract_text(self, response_data: dict[str, Any]) -> str:
        parts: list[str] = []
        for block in response_data.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
        return "".join(parts)

    def extract_usage(self, response_data: dict[str, Any]) -> dict[str, int]:
        u = response_data.get("usage") or {}
        return {
            "input_tokens": int(u.get("input_tokens") or 0),
            "output_tokens": int(u.get("output_tokens") or 0),
        }


class ResponsesAPIAdapter:
    """OpenAI-compatible Responses API (xAI Grok, OpenAI) — Bearer auth, type mcp tools."""

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

    def _mcp_tool_def(self, mcp: MCPConfig) -> dict[str, Any]:
        auth_header = f"Bearer {mcp.auth_token}" if mcp.auth_token else ""
        tool: dict[str, Any] = {
            "type": "mcp",
            "server_url": mcp.server_url,
            "server_label": mcp.server_name,
        }
        if auth_header:
            tool["authorization"] = auth_header
        if self._vendor in {"openai", "chatgpt"}:
            tool["require_approval"] = "never"
        return tool

    def build_request(
        self, req: LLMRequest, mcp: MCPConfig | None
    ) -> tuple[str, dict[str, str], dict[str, Any]]:
        headers = {
            "authorization": f"Bearer {self._api_key}",
            "content-type": "application/json",
        }
        input_msgs: list[dict[str, Any]] = []
        if req.system.strip():
            input_msgs.append({"role": "system", "content": req.system})
        input_msgs.extend(req.messages)
        body: dict[str, Any] = {
            "model": req.model,
            "input": input_msgs,
            "max_output_tokens": req.max_tokens,
            "store": False,
        }
        if req.temperature is not None:
            body["temperature"] = req.temperature
        if req.top_p is not None:
            body["top_p"] = req.top_p
        if req.stop_sequences is not None:
            body["stop"] = req.stop_sequences  # Responses API uses "stop"
        if req.seed is not None:
            body["seed"] = req.seed
        if mcp is not None and req.inject_mcp:
            body["tools"] = [self._mcp_tool_def(mcp)]
        url = f"{self._base}/responses"
        return url, headers, body

    def build_frontier_request(
        self, req: FrontierRequest, mcp: MCPConfig | None
    ) -> tuple[str, dict[str, str], dict[str, Any]]:
        headers: dict[str, str] = {
            "authorization": f"Bearer {self._api_key}",
            "content-type": "application/json",
        }

        # Cache affinity — xAI routes to same server for prompt cache hits
        if req.conversation_id and self._vendor == "xai":
            headers["x-grok-conv-id"] = req.conversation_id

        input_msgs: list[dict[str, Any]] = []
        if req.system.strip():
            input_msgs.append({"role": "system", "content": req.system})

        # Reasoning trace passthrough — prepend encrypted blocks from prior turns
        if req.reasoning_trace:
            input_msgs.extend(req.reasoning_trace)

        input_msgs.extend(req.messages)

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

        # Thinking / reasoning
        if req.thinking:
            if req.thinking.get("include_encrypted"):
                body.setdefault("include", []).append("reasoning.encrypted_content")
            if req.thinking.get("effort"):
                body["reasoning"] = {"effort": req.thinking["effort"]}

        # Response format — unwrap OpenAI json_schema wrapper to Responses API format
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

        # Tools — passthrough for both function calling and server-side
        tools_list: list[dict[str, Any]] = []
        if req.tools:
            tools_list.extend(req.tools)
        if mcp is not None and req.inject_mcp:
            tools_list.append(self._mcp_tool_def(mcp))
        if tools_list:
            body["tools"] = tools_list
        if req.tool_choice is not None:
            body["tool_choice"] = req.tool_choice

        # Provider-specific options
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

            elif item_type in {"web_search_call", "code_interpreter_call"}:
                server_tool_calls.append(item)

        # Fallback: output_text convenience field
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

        return {
            "content": "".join(content_parts),
            "model": str(response_data.get("model", "")),
            "provider": self._vendor,
            "usage": usage,
            "thinking": thinking,
            "tool_calls": tool_calls or None,
            "server_tool_calls": server_tool_calls or None,
            "response_id": response_data.get("id"),
            "raw": None,
        }

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


def resolve_llm_adapter(provider: str | None) -> LLMAdapter | None:
    """Return a configured adapter for cloud provider, or None if API key missing."""
    p = (provider or "anthropic").strip().lower()
    match p:
        case "anthropic":
            key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
            if not key:
                return None
            base = os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
            return AnthropicAdapter(api_key=key, base_url=base)
        case "xai":
            key = os.environ.get("XAI_API_KEY", "").strip()
            if not key:
                return None
            base = os.getenv("XAI_BASE_URL", "https://api.x.ai/v1").rstrip("/")
            return ResponsesAPIAdapter(api_key=key, base_url=base, vendor="xai")
        case "openai" | "chatgpt":
            key = os.environ.get("OPENAI_API_KEY", "").strip()
            if not key:
                return None
            base = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
            return ResponsesAPIAdapter(api_key=key, base_url=base, vendor=p)
        case _:
            return None


def effective_provider_for_model(parsed_provider: str | None) -> str:
    """Default cloud provider when ModelId has no provider (legacy bare IDs)."""
    return (parsed_provider or "anthropic").strip().lower()


def anthropic_inject_mcp_into_body(
    body: dict[str, Any],
    *,
    mcp_url: str,
    mcp_name: str,
    mcp_auth_token: str,
) -> None:
    """Mutate Anthropic Messages body in place with mcp_servers + tools (llm_proxy)."""
    mcp_server_def: dict[str, Any] = {
        "type": "url",
        "url": mcp_url,
        "name": mcp_name,
    }
    if mcp_auth_token:
        mcp_server_def["authorization_token"] = mcp_auth_token
    if "mcp_servers" not in body:
        body["mcp_servers"] = [mcp_server_def]
    if "tools" not in body:
        body["tools"] = [{"type": "mcp_toolset", "mcp_server_name": mcp_name}]


def body_to_llm_request(
    body: dict[str, Any],
    api_model_id: str,
    *,
    wants_remote_mcp: bool = False,
) -> LLMRequest:
    """Map Anthropic-shaped proxy body to LLMRequest for Responses adapters."""
    messages = body.get("messages")
    if not isinstance(messages, list):
        messages = []
    system_raw = body.get("system", "")
    system = flatten_anthropic_system(system_raw)
    max_tok = body.get("max_tokens", 4096)
    if not isinstance(max_tok, int) or max_tok < 1:
        max_tok = 4096
    temperature = body.get("temperature")
    if isinstance(temperature, bool) or not isinstance(temperature, int | float):
        temperature = None
    else:
        temperature = float(temperature)
    top_p = body.get("top_p")
    if isinstance(top_p, bool) or not isinstance(top_p, int | float):
        top_p = None
    else:
        top_p = float(top_p)
    stop_sequences = body.get("stop_sequences")
    if not (
        isinstance(stop_sequences, list)
        and all(isinstance(item, str) for item in stop_sequences)
    ):
        stop_sequences = None
    seed = body.get("seed")
    if isinstance(seed, bool) or not isinstance(seed, int):
        seed = None
    inject_mcp = wants_remote_mcp and body.get("tool_choice") != "none"
    return LLMRequest(
        messages=messages,
        model=api_model_id,
        max_tokens=max_tok,
        system=system,
        inject_mcp=inject_mcp,
        temperature=temperature,
        top_p=top_p,
        stop_sequences=stop_sequences,
        seed=seed,
    )


def mcp_config_from_env() -> MCPConfig | None:
    """Build MCPConfig from MCP_SERVER_URL + MCP_AUTH_TOKEN if URL set."""
    url = os.environ.get("MCP_SERVER_URL", "").strip()
    if not url:
        return None
    token = os.environ.get("MCP_AUTH_TOKEN", "").strip()
    return MCPConfig(server_url=url, server_name="vortex", auth_token=token)
