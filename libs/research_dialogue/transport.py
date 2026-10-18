from __future__ import annotations

import json
from typing import Any, Callable, Protocol
from urllib.parse import urlparse

import httpx
from transport_utils import DEFAULT_STARGATE_URL, STARGATE_UNIX_SOCKET, make_sync_client

from research_dialogue._types import SendResult

ANTHROPIC_MESSAGES_PATH = "/api/v1/providers/anthropic/messages"
_ALLOWED_PATH = ANTHROPIC_MESSAGES_PATH
# Matches Stargate / cloud_proxy 1800 s read hops.
_READ_TIMEOUT_S = 1800.0

_PRICED_USAGE_KEYS = (
    "input_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
    "output_tokens",
)


def _is_count(v: object) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v >= 0


class _HttpClient(Protocol):
    def post(
        self,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
    ) -> httpx.Response: ...


def stream_usage_complete(usage: dict[str, Any]) -> bool:
    return all(key in usage and _is_count(usage.get(key)) for key in _PRICED_USAGE_KEYS)


def merge_usage_into(
    target: dict[str, Any],
    usage: dict[str, Any],
    invalid: list[str] | None = None,
) -> list[str]:
    """Mirror anthropic_credit_gate._StreamUsageTap._merge_usage; record skipped dict keys."""
    skipped: list[str] = []
    for key, val in usage.items():
        if key in _PRICED_USAGE_KEYS:
            if _is_count(val):
                target[key] = val
            elif invalid is not None:
                invalid.append(key)
        elif key == "cache_creation":
            if isinstance(val, dict):
                existing = target.get("cache_creation")
                if not isinstance(existing, dict):
                    existing = {}
                for sub_key, sub_val in val.items():
                    if _is_count(sub_val):
                        existing[sub_key] = sub_val
                    elif invalid is not None:
                        invalid.append("cache_creation")
                target["cache_creation"] = existing
            elif invalid is not None:
                invalid.append("cache_creation")
        elif key == "server_tool_use":
            if isinstance(val, dict):
                existing = target.get("server_tool_use")
                if not isinstance(existing, dict):
                    existing = {}
                for sub_key, sub_val in val.items():
                    if _is_count(sub_val):
                        existing[sub_key] = sub_val
                target["server_tool_use"] = existing
            elif invalid is not None:
                invalid.append("server_tool_use")
        elif isinstance(val, dict):
            skipped.append(key)
            continue
        elif _is_count(val):
            target[key] = val
    return skipped


def assert_allowed_stargate_base(url: str) -> None:
    """Refuse sends when Stargate is not the default UDS or local :9999."""
    if url.startswith("unix://"):
        if STARGATE_UNIX_SOCKET and url == f"unix://{STARGATE_UNIX_SOCKET}":
            return
        raise ValueError(
            f"refused stargate base {url!r}; expected unix://{STARGATE_UNIX_SOCKET!r}"
        )
    parsed = urlparse(url)
    if parsed.scheme != "http":
        raise ValueError(f"refused stargate base {url!r}")
    host = (parsed.hostname or "").lower()
    if host not in {"localhost", "127.0.0.1"}:
        raise ValueError(f"refused stargate host {host!r}")
    port = parsed.port or 80
    if port != 9999:
        raise ValueError(f"refused stargate port {port!r}")


def _parse_non_stream_json(resp: httpx.Response) -> SendResult:
    try:
        payload = resp.json()
    except json.JSONDecodeError:
        return SendResult(
            ok=False,
            status_code=resp.status_code,
            text="",
            usage={},
            stop_reason=None,
            message_id=None,
            error_body=resp.text,
            parse_error=True,
            usage_incomplete=True,
        )
    if not isinstance(payload, dict):
        return SendResult(
            ok=False,
            status_code=resp.status_code,
            text="",
            usage={},
            stop_reason=None,
            message_id=None,
            error_body="response not object",
            parse_error=True,
            usage_incomplete=True,
        )
    text_parts: list[str] = []
    content = payload.get("content")
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                t = block.get("text")
                if isinstance(t, str):
                    text_parts.append(t)
    usage_raw = payload.get("usage")
    usage: dict[str, Any] = {}
    skipped: list[str] = []
    invalid_keys: list[str] = []
    if isinstance(usage_raw, dict):
        skipped = merge_usage_into(usage, usage_raw, invalid=invalid_keys)
    stop = payload.get("stop_reason")
    stop_reason = stop if isinstance(stop, str) else None
    mid = payload.get("id")
    message_id = mid if isinstance(mid, str) else None
    incomplete = bool(invalid_keys) or (not stream_usage_complete(usage) if usage else True)
    return SendResult(
        ok=True,
        status_code=resp.status_code,
        text="".join(text_parts),
        usage=usage,
        stop_reason=stop_reason,
        message_id=message_id,
        usage_incomplete=incomplete,
        delta_usage_seen=bool(usage),
        usage_skipped_dict_keys=skipped or None,
        usage_invalid_keys=invalid_keys or None,
    )


def _parse_sse_stream(resp: httpx.Response) -> SendResult:
    # S1 (c1-s1-20261017) ran streamed as an undisclosed deviation; streams floor at the gate until FLOOR-FIX F1 lands.
    text_parts: list[str] = []
    usage: dict[str, Any] = {}
    skipped_keys: list[str] = []
    invalid_keys: list[str] = []
    stop_reason: str | None = None
    message_id: str | None = None
    delta_usage_seen = False
    parse_error = False

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
            parse_error = True
            continue
        if not isinstance(event, dict):
            parse_error = True
            continue
        etype = event.get("type")
        if etype == "message_start":
            msg = event.get("message")
            if isinstance(msg, dict):
                message_id = msg.get("id") if isinstance(msg.get("id"), str) else message_id
                u = msg.get("usage")
                if isinstance(u, dict):
                    skipped_keys.extend(merge_usage_into(usage, u, invalid=invalid_keys))
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
                delta_usage_seen = True
                skipped_keys.extend(merge_usage_into(usage, u, invalid=invalid_keys))

    usage_incomplete = (
        parse_error
        or not delta_usage_seen
        or not stream_usage_complete(usage)
        or bool(invalid_keys)
    )
    uniq_skipped = sorted(set(skipped_keys))
    return SendResult(
        ok=True,
        status_code=resp.status_code,
        text="".join(text_parts),
        usage=usage,
        stop_reason=stop_reason,
        message_id=message_id,
        usage_incomplete=usage_incomplete,
        delta_usage_seen=delta_usage_seen,
        parse_error=parse_error,
        usage_skipped_dict_keys=uniq_skipped or None,
        usage_invalid_keys=invalid_keys or None,
    )


def send_messages(
    body: dict[str, Any],
    *,
    client_factory: Callable[[], _HttpClient] | None = None,
    path: str = ANTHROPIC_MESSAGES_PATH,
    stream: bool | None = None,
    stargate_base: str | None = None,
) -> SendResult:
    if path != _ALLOWED_PATH:
        raise ValueError(f"refused URL path {path!r}; only {_ALLOWED_PATH!r} is allowed")

    base = stargate_base if stargate_base is not None else DEFAULT_STARGATE_URL
    assert_allowed_stargate_base(base)

    use_stream = stream if stream is not None else bool(body.get("stream"))

    def _default() -> _HttpClient:
        timeout = httpx.Timeout(connect=10.0, read=_READ_TIMEOUT_S, write=30.0, pool=10.0)
        return make_sync_client(base, timeout=timeout)

    headers = {
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    factory = client_factory or _default
    post_body = dict(body)
    if use_stream:
        post_body["stream"] = True
    else:
        post_body.pop("stream", None)
    try:
        with factory() as client:
            resp = client.post(path, headers=headers, json=post_body)
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
    if use_stream:
        return _parse_sse_stream(resp)
    return _parse_non_stream_json(resp)
