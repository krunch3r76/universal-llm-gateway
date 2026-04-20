"""Frontier generation tool backed by the frontier-dispatch pipeline."""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Literal

import httpx
from universal_logging import get_logger

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = get_logger(__name__)

BootLevel = Literal["none", "mcp", "team", "full"]
FrontierKind = Literal["grok", "claude", "openai", "gemini"]
AgentName = Literal["oppie", "orion", "bard", "api_claude"]
ReasoningEffort = Literal["minimal", "low", "medium", "high", "max"]

# Identity integrity: persona boot levels ("team"/"full") load a durable
# birth-prompt seed keyed by frontier. Running that seed on a non-canonical
# model produces a stand-in that inherits the voice but not the capability
# profile. `agent` is the primary selector for boot="team"/"full"; it pins
# frontier, model whitelist + default, and remote_mcp routing.
_AGENT_CONFIG: dict[AgentName, dict[str, Any]] = {
    "oppie": {
        "frontier": "grok",
        "default_model": "grok-4.20-multi-agent-0309",
        "allowed_models": ["grok-4.20-multi-agent-0309"],
        "require_remote_mcp": True,
    },
    "orion": {
        "frontier": "openai",
        "default_model": "gpt-5.4",
        "allowed_models": ["gpt-5.4", "gpt-5.4-mini", "o4-mini", "o3"],
        "require_remote_mcp": True,
    },
    "bard": {
        "frontier": "gemini",
        "default_model": "gemini-2.5-pro",
        "allowed_models": [
            "gemini-2.5-pro",
            "gemini-2.5-flash",
            "gemini-3-flash-preview",
            "gemini-3.1-pro-preview",
        ],
        "require_remote_mcp": True,
    },
    "api_claude": {
        "frontier": "claude",
        "default_model": "claude-sonnet-4-6",
        "allowed_models": ["claude-sonnet-4-6", "claude-opus-4", "claude-3-5-sonnet"],
        # Claude's tool-use API with client-side tool defs is production-stable;
        # MCP server injection is the reliable route for this agent.
        "require_remote_mcp": False,
    },
}

_PIPELINE_ID = "frontier-dispatch"
_POLL_WAIT_SECONDS = 290.0
_DISPATCH_TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=10.0)
_POLL_TIMEOUT = httpx.Timeout(
    connect=10.0, read=_POLL_WAIT_SECONDS + 10.0, write=30.0, pool=10.0
)

_FRONTIER_DEFAULTS: dict[FrontierKind, str] = {
    "grok": "grok-4.20-0309-reasoning",
    "claude": "claude-sonnet-4-6",
    "openai": "gpt-5.4",
    "gemini": "gemini-2.5-flash",
}
_FRONTIER_AGENT: dict[FrontierKind, str] = {
    "grok": "oppie",
    "claude": "api_claude",
    "openai": "orion",
    "gemini": "bard",
}
_FRONTIER_SEED_REFS: dict[FrontierKind, str] = {
    "grok": "notes/system/prompts/oppie-seed-mcp-v1.5.md",
    "claude": "notes/system/prompts/api-claude-seed-v1.0.md",
    "openai": "notes/system/prompts/api-claude-seed-v1.0.md",
    "gemini": "agent-identity/bard-birth.md",
}
_AGENT_IDENTITY_ROOT = os.environ.get("AGENT_IDENTITY_DIR", "/mnt/torus/mcp-data/files")

XAI_SERVER_TOOL_MAP: dict[str, dict[str, str]] = {
    "web_search": {"type": "web_search"},
    "x_search": {"type": "x_search"},
    "code_execution": {"type": "code_interpreter"},
}
OPENAI_SERVER_TOOL_MAP: dict[str, dict[str, str]] = {
    "web_search": {"type": "web_search_preview"},
    "code_interpreter": {"type": "code_interpreter"},
    "file_search": {"type": "file_search"},
}
GOOGLE_SERVER_TOOL_MAP: dict[str, dict[str, Any]] = {
    "google_search": {"google_search": {}},
    "code_execution": {"code_execution": {}},
}


def _agent_for_boot(frontier: FrontierKind, boot: BootLevel) -> str | None:
    return _FRONTIER_AGENT[frontier] if boot in ("team", "full") else None


def _assemble_seed_system(frontier: FrontierKind, boot_ref: str | None) -> str:
    ref = boot_ref or _FRONTIER_SEED_REFS.get(frontier)
    if not ref:
        return ""
    path = ref if os.path.isabs(ref) else os.path.join(_AGENT_IDENTITY_ROOT, ref)
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


def _set_if(target: dict[str, Any], key: str, value: Any) -> None:
    if value is not None:
        target[key] = value


def _collect_tools(
    server_tools: list[str] | None,
    mapped_tools: dict[str, Any] | None,
    tools: list[dict[str, Any]] | None,
    *,
    passthrough_server_tools: bool = False,
) -> list[dict[str, Any]] | None:
    merged: list[dict[str, Any]] = []
    if server_tools:
        for st in server_tools:
            if passthrough_server_tools:
                merged.append({"type": st})
            elif mapped_tools and st in mapped_tools:
                mapped = mapped_tools[st]
                merged.append(dict(mapped) if isinstance(mapped, dict) else mapped)
    if tools:
        merged.extend(tools)
    return merged or None


def _extract_pipeline_error(resp: httpx.Response) -> dict[str, Any]:
    try:
        payload = resp.json()
    except ValueError:
        return {"error": f"HTTP {resp.status_code}: {resp.text[:500]}"}
    err = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(err, dict):
        return {"error": f"{err.get('code', 'unknown')}: {err.get('message', '')}"}
    if isinstance(err, str):
        return {"error": err}
    return {"error": f"HTTP {resp.status_code}"}


def _shape_pipeline_result(data: dict[str, Any], execution_id: str) -> dict[str, Any]:
    status = data.get("status")
    if status != "completed":
        err = data.get("error") or {}
        if isinstance(err, dict) and err:
            return {"error": f"{err.get('code', status)}: {err.get('message', '')}"}
        return {
            "error": f"pipeline {status or 'unknown'}",
            "execution_id": execution_id,
        }

    output = data.get("output") if isinstance(data, dict) else {}
    json_body = output.get("json") if isinstance(output, dict) else {}
    result: dict[str, Any] = {
        "content": json_body.get("content", "") if isinstance(json_body, dict) else "",
        "usage": {
            "input_tokens": output.get("prompt_tokens", 0)
            if isinstance(output, dict)
            else 0,
            "output_tokens": output.get("completion_tokens", 0)
            if isinstance(output, dict)
            else 0,
        },
        "provider": json_body.get("provider", "")
        if isinstance(json_body, dict)
        else "",
        "model": output.get("model_id", "") if isinstance(output, dict) else "",
        "execution_id": execution_id,
    }
    if isinstance(output, dict) and output.get("reasoning") is not None:
        result["thinking"] = output["reasoning"]
    if isinstance(json_body, dict):
        _set_if(result, "finish_reason", json_body.get("finish_reason"))
        _set_if(result, "block_reason", json_body.get("block_reason"))
        tool_calls_made = int(json_body.get("tool_calls_made", 0) or 0)
        if tool_calls_made:
            result["tool_calls_made"] = tool_calls_made
        if json_body.get("exhausted"):
            result["warning"] = "Tool loop reached max turns"
    return result


def _delegate_to_pipeline(
    *, messages: list[dict[str, Any]], system: str, pipeline_options: dict[str, Any]
) -> dict[str, Any]:
    stargate_url = os.environ.get("STARGATE_URL", "http://io:9999")
    body = {
        "model": _PIPELINE_ID,
        "messages": messages,
        "pipeline_options": dict(pipeline_options, system=system),
    }
    try:
        with httpx.Client(timeout=_DISPATCH_TIMEOUT) as client:
            dispatch_resp = client.post(
                f"{stargate_url}/api/v1/pipelines/dispatch", json=body
            )
            if dispatch_resp.status_code >= 400:
                return _extract_pipeline_error(dispatch_resp)
            execution_id = dispatch_resp.json().get("execution_id", "")
        if not execution_id:
            return {"error": "pipeline dispatch returned no execution_id"}
        with httpx.Client(timeout=_POLL_TIMEOUT) as client:
            poll_resp = client.get(
                f"{stargate_url}/api/v1/pipelines/executions/{execution_id}",
                params={"wait": _POLL_WAIT_SECONDS},
            )
        if poll_resp.status_code >= 400:
            return _extract_pipeline_error(poll_resp)
        return _shape_pipeline_result(poll_resp.json(), execution_id)
    except httpx.TimeoutException:
        return {"error": f"frontier pipeline timed out after {_POLL_WAIT_SECONDS}s"}
    except httpx.RequestError as exc:
        logger.error("Pipeline dispatch request failed: %s", exc)
        return {"error": f"pipeline transport failure: {exc}"}


def _frontier_grok(**kwargs: Any) -> dict[str, Any]:
    model = kwargs["model"]
    remote_mcp = bool(kwargs["remote_mcp"])
    if remote_mcp and model == _FRONTIER_DEFAULTS["grok"]:
        model = "grok-4.20-multi-agent-0309"
    effort = (
        kwargs.get("reasoning_effort")
        if kwargs.get("reasoning_effort") in ("low", "medium", "high")
        else None
    )
    include_encrypted = bool(kwargs["extra"].get("include_encrypted_reasoning"))
    thinking = None
    if effort or include_encrypted:
        thinking = {}
        if effort:
            thinking["effort"] = effort
        if include_encrypted:
            thinking["include_encrypted"] = True

    gen: dict[str, Any] = {}
    _set_if(gen, "max_tokens", kwargs.get("max_tokens"))
    _set_if(gen, "temperature", kwargs.get("temperature"))
    _set_if(gen, "top_p", kwargs.get("top_p"))
    _set_if(gen, "stop", kwargs.get("stop_sequences"))
    _set_if(gen, "seed", kwargs.get("seed"))
    _set_if(gen, "thinking", thinking)
    _set_if(gen, "tool_choice", kwargs.get("tool_choice"))
    _set_if(gen, "response_format", kwargs.get("response_format"))
    _set_if(gen, "conversation_id", kwargs["extra"].get("conversation_id"))
    _set_if(gen, "reasoning_trace", kwargs["extra"].get("reasoning_trace"))

    return {
        "model": model if "/" in model else f"xai/{model}",
        "agent": _agent_for_boot("grok", kwargs["boot"]),
        "generation_parameters": gen,
        "remote_mcp": remote_mcp,
        "tools": _collect_tools(
            kwargs.get("server_tools"), XAI_SERVER_TOOL_MAP, kwargs.get("tools")
        ),
    }


def _frontier_claude(**kwargs: Any) -> dict[str, Any]:
    thinking = kwargs.get("thinking")
    thinking_dict = None
    if isinstance(thinking, str):
        if thinking == "adaptive":
            thinking_dict = {"type": "adaptive"}
        elif thinking == "disabled":
            thinking_dict = {"type": "disabled"}
    elif isinstance(thinking, dict):
        thinking_dict = thinking

    opts = dict(kwargs["extra"].get("provider_options") or {})
    if kwargs["extra"].get("speed") == "fast":
        anthropic_opts = dict(opts.get("anthropic", {}))
        anthropic_opts["speed"] = "fast"
        opts["anthropic"] = anthropic_opts

    gen: dict[str, Any] = {}
    _set_if(gen, "max_tokens", kwargs.get("max_tokens"))
    _set_if(gen, "temperature", kwargs.get("temperature"))
    _set_if(gen, "top_p", kwargs.get("top_p"))
    _set_if(gen, "stop", kwargs.get("stop_sequences"))
    _set_if(gen, "thinking", thinking_dict)
    effort = kwargs.get("reasoning_effort")
    if effort in ("low", "medium", "high", "max"):
        gen["reasoning_effort"] = effort
    _set_if(gen, "tool_choice", kwargs.get("tool_choice"))
    _set_if(gen, "response_format", kwargs.get("response_format"))
    if opts:
        gen["provider_options"] = opts

    model = kwargs["model"]
    return {
        "model": model if "/" in model else f"anthropic/{model}",
        "agent": _agent_for_boot("claude", kwargs["boot"]),
        "generation_parameters": gen,
        "remote_mcp": bool(kwargs["remote_mcp"]),
        "tools": _collect_tools(
            kwargs.get("server_tools"),
            None,
            kwargs.get("tools"),
            passthrough_server_tools=True,
        ),
    }


def _frontier_openai(**kwargs: Any) -> dict[str, Any]:
    gen: dict[str, Any] = {}
    _set_if(gen, "max_tokens", kwargs.get("max_tokens"))
    _set_if(gen, "temperature", kwargs.get("temperature"))
    _set_if(gen, "top_p", kwargs.get("top_p"))
    _set_if(gen, "stop", kwargs.get("stop_sequences"))
    _set_if(gen, "seed", kwargs.get("seed"))
    effort = kwargs.get("reasoning_effort")
    if effort in ("minimal", "low", "medium", "high"):
        gen["thinking"] = {"effort": effort}
    _set_if(gen, "tool_choice", kwargs.get("tool_choice"))
    _set_if(gen, "response_format", kwargs.get("response_format"))
    _set_if(gen, "provider_options", kwargs["extra"].get("provider_options"))

    model = kwargs["model"]
    return {
        "model": model if "/" in model else f"openai/{model}",
        "agent": _agent_for_boot("openai", kwargs["boot"]),
        "generation_parameters": gen,
        "remote_mcp": bool(kwargs["remote_mcp"]),
        "tools": _collect_tools(
            kwargs.get("server_tools"), OPENAI_SERVER_TOOL_MAP, kwargs.get("tools")
        ),
    }


def _frontier_gemini(**kwargs: Any) -> dict[str, Any]:
    gen: dict[str, Any] = {}
    _set_if(gen, "max_tokens", kwargs.get("max_tokens"))
    _set_if(gen, "temperature", kwargs.get("temperature"))
    _set_if(gen, "top_p", kwargs.get("top_p"))
    _set_if(gen, "stop", kwargs.get("stop_sequences"))
    _set_if(gen, "seed", kwargs.get("seed"))
    effort = kwargs.get("reasoning_effort")
    if effort in ("low", "medium", "high"):
        gen["thinking"] = {"level": effort.upper()}
    _set_if(gen, "tool_choice", kwargs.get("tool_choice"))
    _set_if(gen, "response_format", kwargs.get("response_format"))
    _set_if(gen, "provider_options", kwargs["extra"].get("provider_options"))

    model = kwargs["model"]
    return {
        "model": model if "/" in model else f"google/{model}",
        "agent": _agent_for_boot("gemini", kwargs["boot"]),
        "generation_parameters": gen,
        "remote_mcp": bool(kwargs["remote_mcp"]),
        "tools": _collect_tools(
            kwargs.get("server_tools"), GOOGLE_SERVER_TOOL_MAP, kwargs.get("tools")
        ),
    }


_FRONTIER_ROUTERS: dict[FrontierKind, Callable[..., dict[str, Any]]] = {
    "grok": _frontier_grok,
    "claude": _frontier_claude,
    "openai": _frontier_openai,
    "gemini": _frontier_gemini,
}


def register_frontier_tools(mcp: FastMCP) -> None:
    """Register the unified frontier_generate tool."""

    @mcp.tool(title="Frontier Generate")
    def frontier_generate(
        messages: list[dict[str, Any]],
        agent: AgentName | None = None,
        frontier: FrontierKind | None = None,
        model: str | None = None,
        system: str = "",
        max_tokens: int | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
        stop_sequences: list[str] | None = None,
        seed: int | None = None,
        response_format: dict[str, Any] | None = None,
        reasoning_effort: ReasoningEffort | None = None,
        thinking: Literal["adaptive", "disabled"] | dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        server_tools: list[str] | None = None,
        boot: BootLevel = "mcp",
        boot_ref: str | None = None,
        remote_mcp: bool | None = None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        # Invariant: boot ∈ {"team","full"} requires agent — without it we refuse
        # to load a persona seed. See todo:frontier-generate-agent-enforcement.
        if boot in ("team", "full") and agent is None:
            return {
                "error": (
                    "boot='team'/'full' requires agent ∈ "
                    "{oppie, orion, bard, api_claude}. Without an agent, "
                    "frontier_generate refuses to load a persona seed."
                )
            }

        if agent is not None:
            cfg = _AGENT_CONFIG[agent]
            if frontier is not None and frontier != cfg["frontier"]:
                return {
                    "error": (
                        f"agent={agent!r} implies frontier={cfg['frontier']!r}; "
                        f"caller passed frontier={frontier!r}"
                    )
                }
            if model is not None and model not in cfg["allowed_models"]:
                return {
                    "error": (
                        f"agent={agent!r} does not allow model={model!r}; "
                        f"allowed: {cfg['allowed_models']}"
                    )
                }
            frontier = cfg["frontier"]
            model = model or cfg["default_model"]
            remote_mcp = cfg["require_remote_mcp"]
        elif frontier is None:
            return {"error": "must provide agent= or frontier="}

        effective_model = model if model is not None else _FRONTIER_DEFAULTS[frontier]
        common_kwargs: dict[str, Any] = {
            "model": effective_model,
            "reasoning_effort": reasoning_effort,
            "thinking": thinking if frontier == "claude" else None,
            "tools": tools,
            "tool_choice": tool_choice,
            "server_tools": server_tools,
            "remote_mcp": bool(remote_mcp),
            "boot": boot,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "top_p": top_p,
            "stop_sequences": stop_sequences,
            "seed": seed,
            "response_format": response_format,
            "extra": extra or {},
        }
        pipeline_options = _FRONTIER_ROUTERS[frontier](**common_kwargs)
        if "error" in pipeline_options:
            return pipeline_options

        if boot == "none":
            effective_system = system
        elif boot == "mcp":
            seed_system = _assemble_seed_system(frontier, boot_ref)
            effective_system = "\n\n".join(p for p in (seed_system, system) if p)
        else:
            effective_system = system

        pipeline_options["inject_tools"] = boot != "none"
        result = _delegate_to_pipeline(
            messages=messages,
            system=effective_system,
            pipeline_options=pipeline_options,
        )
        if "error" not in result and boot in ("team", "full"):
            result["_next"] = (
                "If this consultation surfaced a decision, insight, or correction "
                "worth remembering: cortex assert or observe with "
                "evidence_uris pointing to the agent-bus thread. "
                "If Cortex lacked context this consultation needed, "
                "record that gap via cortex observe."
            )
        return result
