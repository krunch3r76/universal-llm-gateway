"""Multi-model agent loop — provider calling and orchestration.

Runs queries through external LLM providers (xAI Grok, OpenAI GPT) with
native function calling. Tool definitions and execution live in _agent_tools.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

import httpx
from mcp_events import monotonic_now, record
from universal_logging import get_logger

from ._agent_tools import SYSTEM_PROMPT, TOOL_DEFINITIONS, execute_tool
from ._cortex_relay import _cx

logger = get_logger(__name__)

_MAX_TURNS = 10
_API_TIMEOUT = httpx.Timeout(connect=10.0, read=120.0, write=30.0, pool=10.0)

PROVIDERS: dict[str, dict[str, str]] = {
    "grok": {
        "base_url": "https://api.x.ai/v1",
        "model": "grok-3-mini",
        "api_key_env": "XAI_API_KEY",
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o",
        "api_key_env": "OPENAI_API_KEY",
    },
}


def _has_api_key(provider_key: str) -> bool:
    env_var = PROVIDERS[provider_key]["api_key_env"]
    return bool(os.environ.get(env_var, "").strip())


def _call_provider(
    provider_key: str,
    messages: list[dict[str, Any]],
) -> dict[str, Any]:
    """Single chat completion call to a provider. Returns raw response dict."""
    config = PROVIDERS[provider_key]
    api_key = os.environ.get(config["api_key_env"], "").strip()
    if not api_key:
        return {"error": f"API key not configured ({config['api_key_env']})"}

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    body: dict[str, Any] = {
        "model": config["model"],
        "messages": messages,
        "tools": TOOL_DEFINITIONS,
        "tool_choice": "auto",
    }

    try:
        with httpx.Client(timeout=_API_TIMEOUT) as client:
            resp = client.post(
                f"{config['base_url']}/chat/completions",
                headers=headers,
                json=body,
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.TimeoutException:
        return {"error": "Provider API timed out"}
    except httpx.HTTPStatusError as e:
        return {
            "error": f"Provider API error ({e.response.status_code})",
            "detail": e.response.text[:500],
        }
    except httpx.RequestError as e:
        logger.error("Provider %s request failed: %s", provider_key, e)
        return {"error": f"Provider connection failed: {e}"}


def _agent_loop(
    provider_key: str,
    query: str,
    context_entities: list[str] | None = None,
) -> dict[str, Any]:
    """Run multi-turn function-calling loop for one provider."""
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
    ]

    if context_entities:
        context_parts = []
        for eid in context_entities:
            entity_data = _cx("GET", f"/entities/{eid}")
            if "error" not in entity_data:
                context_parts.append(
                    f"## Entity: {eid}\n```json\n"
                    f"{json.dumps(entity_data, indent=2, default=str)}\n```"
                )
        if context_parts:
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "Here is pre-loaded context from Cortex:\n\n"
                        + "\n\n".join(context_parts)
                    ),
                }
            )

    messages.append({"role": "user", "content": query})

    t0 = time.monotonic()
    tool_calls_total = 0

    for turn in range(_MAX_TURNS):
        response = _call_provider(provider_key, messages)
        if "error" in response:
            return response

        choices = response.get("choices", [])
        if not choices:
            return {"error": "Empty response from provider"}

        message = choices[0].get("message", {})
        finish_reason = choices[0].get("finish_reason", "")
        tool_calls = message.get("tool_calls")

        if not tool_calls or finish_reason == "stop":
            duration = time.monotonic() - t0
            return {
                "provider": provider_key,
                "model": response.get("model", PROVIDERS[provider_key]["model"]),
                "content": message.get("content", ""),
                "tool_calls_made": tool_calls_total,
                "turns": turn + 1,
                "duration_s": round(duration, 2),
                "usage": response.get("usage", {}),
            }

        messages.append(message)

        for tc in tool_calls:
            fn = tc.get("function", {})
            tool_name = fn.get("name", "")
            try:
                tool_args = json.loads(fn.get("arguments", "{}"))
            except json.JSONDecodeError:
                tool_args = {}

            tool_calls_total += 1
            logger.info(
                "agent_consult [%s] turn %d: %s(%s)",
                provider_key,
                turn,
                tool_name,
                tool_args,
            )

            result = execute_tool(tool_name, tool_args)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.get("id", ""),
                    "content": result,
                }
            )

    duration = time.monotonic() - t0
    return {
        "provider": provider_key,
        "error": f"Max turns ({_MAX_TURNS}) reached",
        "tool_calls_made": tool_calls_total,
        "duration_s": round(duration, 2),
    }


def run_consult(
    query: str,
    providers: list[str] | None = None,
    context_entities: list[str] | None = None,
) -> dict[str, Any]:
    """Run advisory consultation across providers. Returns per-provider results."""
    if providers is None:
        providers = [k for k in PROVIDERS if _has_api_key(k)]
        if not providers:
            return {"error": "No providers configured (missing API keys)"}

    invalid = [p for p in providers if p not in PROVIDERS]
    if invalid:
        return {
            "error": f"Unknown providers: {invalid}. Available: {sorted(PROVIDERS)}"
        }

    unavailable = [p for p in providers if not _has_api_key(p)]
    if unavailable:
        return {
            "error": (
                f"Missing API keys for: {unavailable}. "
                f"Set env vars: {[PROVIDERS[p]['api_key_env'] for p in unavailable]}"
            )
        }

    t0 = monotonic_now()
    record(
        "mcp.agent.consult.called",
        query=query[:200],
        providers=providers,
        context_entities=context_entities or [],
    )

    results: dict[str, Any] = {}
    for provider in providers:
        results[provider] = _agent_loop(provider, query, context_entities)

    duration = monotonic_now() - t0
    record(
        "mcp.agent.consult.completed",
        duration_s=round(duration, 3),
        providers=providers,
        results_summary={
            p: {"ok": "error" not in r, "turns": r.get("turns", 0)}
            for p, r in results.items()
        },
    )
    return {"results": results, "duration_s": round(duration, 2)}
