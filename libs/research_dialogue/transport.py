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


def _merge_usage(start: dict[str, Any] | None, delta: dict[str, Any] | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for src in (start or {}, delta or {}):
        for k, v in src.items():
            if isinstance(v, int):
                out[k] = out.get(k, 0) + v
            elif k not in out:
                out[k] = v
    return out


def _parse_sse_stream(resp: httpx.Response) -> SendResult:
    text_parts: list[str] = []
    usage_start: dict[str, Any] | None = None
    usage_delta: dict[str, Any] | None = None
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
                    usage_start = u
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
                usage_delta = u

    usage = _merge_usage(usage_start, usage_delta)
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
    with factory() as client:
        resp = client.post(path, headers=headers, json=body)
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
