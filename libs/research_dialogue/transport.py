from __future__ import annotations

import json
from typing import Any, Callable, Protocol

import httpx
from transport_utils import DEFAULT_STARGATE_URL, make_sync_client

from research_dialogue._types import SendResult

ANTHROPIC_MESSAGES_PATH = "/api/v1/providers/anthropic/messages"
_ALLOWED_PATH = ANTHROPIC_MESSAGES_PATH
_READ_TIMEOUT_S = 900.0


class _HttpClient(Protocol):
    def post(
        self,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
    ) -> httpx.Response: ...


def merge_usage_into(target: dict[str, Any], usage: dict[str, Any]) -> None:
    """Mirror anthropic_credit_gate._StreamUsageTap._merge_usage (last-value-wins ints)."""
    for key, val in usage.items():
        if key == "cache_creation" and isinstance(val, dict):
            existing = target.get("cache_creation")
            if not isinstance(existing, dict):
                existing = {}
            existing.update(val)
            target["cache_creation"] = existing
        elif key == "server_tool_use" and isinstance(val, dict):
            existing = target.get("server_tool_use")
            if not isinstance(existing, dict):
                existing = {}
            for sub_key, sub_val in val.items():
                if isinstance(sub_val, int) and sub_val >= 0:
                    existing[sub_key] = sub_val
            target["server_tool_use"] = existing
        elif isinstance(val, dict):
            continue
        elif isinstance(val, int) and val >= 0:
            target[key] = val


def _parse_sse_stream(resp: httpx.Response) -> SendResult:
    text_parts: list[str] = []
    usage: dict[str, Any] = {}
    stop_reason: str | None = None
    message_id: str | None = None

    for raw_line in resp.iter_lines():
        if not raw_line:
            continue
        line = raw_line.decode("utf-8") if isinstance(raw_line, bytes) else raw_line
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            event = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        etype = event.get("type")
        if etype == "message_start":
            msg = event.get("message")
            if isinstance(msg, dict):
                message_id = msg.get("id") if isinstance(msg.get("id"), str) else message_id
                u = msg.get("usage")
                if isinstance(u, dict):
                    merge_usage_into(usage, u)
        elif etype == "content_block_delta":
            delta = event.get("delta")
            if isinstance(delta, dict) and delta.get("type") == "text_delta":
                t = delta.get("text")
                if isinstance(t, str):
                    text_parts.append(t)
        elif etype == "message_delta":
            d = event.get("delta")
            if isinstance(d, dict):
                sr = d.get("stop_reason")
                if isinstance(sr, str):
                    stop_reason = sr
            u = event.get("usage")
            if isinstance(u, dict):
                merge_usage_into(usage, u)

    return SendResult(
        ok=True,
        status_code=resp.status_code,
        text="".join(text_parts),
        usage=usage,
        stop_reason=stop_reason,
        message_id=message_id,
    )


def send_messages(
    body: dict[str, Any],
    *,
    client_factory: Callable[[], _HttpClient] | None = None,
    path: str = ANTHROPIC_MESSAGES_PATH,
) -> SendResult:
    if path != _ALLOWED_PATH:
        raise ValueError(f"refused URL path {path!r}; only {_ALLOWED_PATH!r} is allowed")

    def _default() -> _HttpClient:
        timeout = httpx.Timeout(connect=10.0, read=_READ_TIMEOUT_S, write=30.0, pool=10.0)
        return make_sync_client(DEFAULT_STARGATE_URL, timeout=timeout)

    headers = {
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    factory = client_factory or _default
    try:
        with factory() as client:
            resp = client.post(path, headers=headers, json=body)
    except httpx.HTTPError as exc:
        resp = getattr(exc, "response", None)
        status_code = resp.status_code if resp is not None else None
        return SendResult(
            ok=False,
            status_code=status_code,
            text="",
            usage={},
            stop_reason=None,
            message_id=None,
            error_body=repr(exc),
        )
    if resp.status_code >= 400:
        err_text = resp.text
        gate = False
        try:
            parsed = resp.json()
            if isinstance(parsed, dict):
                et = parsed.get("type") or parsed.get("error", {}).get("type")
                if isinstance(et, str) and et.startswith("anthropic_"):
                    gate = True
        except json.JSONDecodeError:
            pass
        return SendResult(
            ok=False,
            status_code=resp.status_code,
            text="",
            usage={},
            stop_reason=None,
            message_id=None,
            error_body=err_text,
            gate_refusal=gate,
        )
    return _parse_sse_stream(resp)
