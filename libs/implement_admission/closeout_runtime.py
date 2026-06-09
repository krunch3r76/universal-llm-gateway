"""IO runtime for closeout adapters — cortex dispatch, pipelines, agent-bus."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from transport_utils import (
    DEFAULT_AGENT_BUS_URL,
    DEFAULT_CORTEX_URL,
    DEFAULT_STARGATE_URL,
    make_sync_client,
)

DispatchFn = Callable[[str, dict[str, Any]], dict[str, Any]]
PipelineFn = Callable[[str, dict[str, Any]], dict[str, Any]]
AgentBusFn = Callable[[str, str, str, str], dict[str, Any]]
WriteTextFn = Callable[[Path, str], None]


def _default_dispatch(tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    with make_sync_client(DEFAULT_CORTEX_URL, timeout=15.0) as client:
        try:
            resp = client.post("/dispatch", json={"tool": tool, "arguments": arguments})
        except Exception as exc:
            return {"error": f"transport_error: {exc}"}
        if resp.status_code >= 400:
            return {"error": resp.text[:300]}
        try:
            data = resp.json()
        except Exception as exc:
            return {"error": f"invalid_json: {exc}"}
        return data if isinstance(data, dict) else {"error": "non_object_response"}


def _default_pipeline(pipeline_id: str, options: dict[str, Any]) -> dict[str, Any]:
    body = {
        "model": pipeline_id,
        "messages": [{"role": "user", "content": "closeout"}],
        "pipeline_options": options,
    }
    with make_sync_client(DEFAULT_STARGATE_URL, timeout=60.0) as client:
        try:
            resp = client.post("/v1/chat/completions", json=body)
        except Exception as exc:
            return {"error": f"transport_error: {exc}"}
        if resp.status_code >= 400:
            return {"error": resp.text[:300]}
        try:
            data = resp.json()
        except Exception as exc:
            return {"error": f"invalid_json: {exc}"}
        if not isinstance(data, dict):
            return {"error": "non_object_response"}
        choices = data.get("choices") or []
        if not choices:
            return {"error": "empty_pipeline_response"}
        message = choices[0].get("message") or {}
        content = message.get("content") or ""
        try:
            parsed = json.loads(content)
            return parsed if isinstance(parsed, dict) else {"raw": content}
        except json.JSONDecodeError:
            return {"raw": content}


def _default_agent_bus(
    thread_id: str, subject: str, body: str, from_agent: str
) -> dict[str, Any]:
    token = os.environ.get("AGENT_BUS_TOKEN", "").strip()
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    payload = {
        "thread": thread_id,
        "from": from_agent,
        "to": "dispatch",
        "subject": subject,
        "body": body,
        "status": "closed",
        "after_turn": 0,
    }
    with make_sync_client(DEFAULT_AGENT_BUS_URL, timeout=15.0) as client:
        try:
            resp = client.post("/turns", json=payload, headers=headers)
        except Exception as exc:
            return {"error": f"transport_error: {exc}"}
        if resp.status_code >= 400:
            return {"error": resp.text[:300]}
        try:
            return resp.json()
        except Exception as exc:
            return {"error": f"invalid_json: {exc}"}


def _default_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


@dataclass
class CloseoutRuntime:
    dispatch: DispatchFn = field(default_factory=lambda: _default_dispatch)
    run_pipeline: PipelineFn = field(default_factory=lambda: _default_pipeline)
    agent_bus_reply: AgentBusFn = field(default_factory=lambda: _default_agent_bus)
    write_text: WriteTextFn = field(default_factory=lambda: _default_write_text)


_RUNTIME = CloseoutRuntime()


def get_runtime() -> CloseoutRuntime:
    return _RUNTIME


def set_runtime(runtime: CloseoutRuntime) -> None:
    global _RUNTIME
    _RUNTIME = runtime


def reset_runtime() -> None:
    set_runtime(CloseoutRuntime())
