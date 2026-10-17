"""Anthropic credit admission, count_tokens, stream usage tap, and commit/release."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

import httpx
from anthropic_credit.policy import (
    Admit,
    AnthropicCreditRefused,
    Refuse,
    admit,
    commit,
    commit_full,
    latch,
    release,
)
from anthropic_credit.reservation import _is_mcp_connector_class

logger = logging.getLogger(__name__)

_COUNT_TOKENS_TIMEOUT = httpx.Timeout(connect=3.0, read=5.0, write=3.0, pool=3.0)
_CREDIT_BALANCE_SNIPPET = "credit balance is too low"


def normalize_meter_model(raw_model: str) -> str:
    text = str(raw_model or "").strip()
    if not text:
        return ""
    if "/" in text:
        return text
    return f"anthropic/{text}"


def extract_credit_tag(
    *,
    extra_headers: dict[str, str] | None = None,
    upstream_body: dict[str, Any] | None = None,
) -> str | None:
    if extra_headers:
        tag = extra_headers.get("x-ulg-credit-tag") or extra_headers.get(
            "X-Ulg-Credit-Tag"
        )
        if tag:
            return str(tag)
    if upstream_body:
        metadata = upstream_body.get("metadata")
        if isinstance(metadata, dict):
            tag = metadata.get("x-ulg-credit-tag") or metadata.get("ulg_credit_tag")
            if tag:
                return str(tag)
    return None


def refuse_to_json(refuse: Refuse) -> dict[str, Any]:
    return {
        "error": {
            "type": refuse.code,
            "reservation_usd": (
                str(refuse.reservation_usd)
                if refuse.reservation_usd is not None
                else None
            ),
            "headroom_usd": (
                str(refuse.headroom_usd) if refuse.headroom_usd is not None else None
            ),
            "detail": refuse.detail,
        }
    }


def raise_if_refused(result: Admit | Refuse) -> Admit:
    if isinstance(result, Refuse):
        raise AnthropicCreditRefused(result)
    return result


def count_tokens_body(upstream_body: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key in ("model", "messages", "system", "tools", "tool_choice", "thinking"):
        if key in upstream_body:
            payload[key] = upstream_body[key]
    return payload


async def fetch_token_count(
    client: httpx.AsyncClient,
    *,
    base_url: str,
    headers: dict[str, str],
    upstream_body: dict[str, Any],
) -> int | None:
    if _is_mcp_connector_class(upstream_body):
        return None
    url = f"{base_url.rstrip('/')}/messages/count_tokens"
    try:
        response = await client.post(
            url,
            json=count_tokens_body(upstream_body),
            headers=headers,
            timeout=_COUNT_TOKENS_TIMEOUT,
        )
        if response.status_code >= 400:
            return None
        data = response.json()
        if isinstance(data, dict):
            count = data.get("input_tokens")
            if isinstance(count, int) and count >= 0:
                return count
    except Exception:
        logger.debug("count_tokens failed; using byte fallback", exc_info=True)
    return None


async def gate_admit(
    *,
    client: httpx.AsyncClient,
    base_url: str,
    headers: dict[str, str],
    request_id: str,
    upstream_body: dict[str, Any],
    catalog_model: str | None = None,
    tag: str | None = None,
) -> Admit:
    model_raw = catalog_model or str(upstream_body.get("model", ""))
    meter_model = normalize_meter_model(model_raw)
    token_count = await fetch_token_count(
        client, base_url=base_url, headers=headers, upstream_body=upstream_body
    )
    result = admit(
        request_id,
        meter_model,
        upstream_body,
        tag,
        token_count=token_count,
    )
    return raise_if_refused(result)


def _is_connect_before_upstream(exc: BaseException) -> bool:
    return isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout))


def finalize_non_stream_credit(
    request_id: str,
    *,
    response: httpx.Response | None = None,
    connect_error: BaseException | None = None,
) -> None:
    if connect_error is not None and _is_connect_before_upstream(connect_error):
        release(request_id)
        return
    if response is None:
        commit_full(request_id, "upstream_error")
        return
    if response.status_code >= 400:
        body_text = (response.text or "").lower()
        if _CREDIT_BALANCE_SNIPPET in body_text:
            latch("credit_balance_too_low")
        commit_full(request_id, f"upstream_http_{response.status_code}")
        return
    try:
        payload = response.json()
    except json.JSONDecodeError:
        commit_full(request_id, "upstream_invalid_json")
        return
    usage = payload.get("usage") if isinstance(payload, dict) else None
    commit(request_id, usage if isinstance(usage, dict) else None)


@dataclass
class StreamUsageTap:
    """Passive SSE usage accumulator (no message text retained)."""

    _usage: dict[str, Any] = field(default_factory=dict)
    _parse_error: bool = False
    _current_event: str = ""
    _delta_usage_seen: bool = False

    def feed_line(self, line: str) -> None:
        stripped = line.strip()
        if not stripped or stripped.startswith(":"):
            return
        if stripped.startswith("event:"):
            self._current_event = stripped.split(":", 1)[1].strip()
            return
        if not stripped.startswith("data:"):
            return
        data_raw = stripped.split(":", 1)[1].strip()
        if not data_raw or data_raw == "[DONE]":
            return
        try:
            payload = json.loads(data_raw)
        except json.JSONDecodeError:
            self._parse_error = True
            return
        if not isinstance(payload, dict):
            return
        event = self._current_event or str(payload.get("type", ""))
        if event == "message_start":
            msg = payload.get("message")
            if isinstance(msg, dict):
                usage = msg.get("usage")
                if isinstance(usage, dict):
                    self._merge_usage(usage)
        elif event == "message_delta":
            usage = payload.get("usage")
            if isinstance(usage, dict):
                self._delta_usage_seen = True
                self._merge_usage(usage)

    def _merge_usage(self, usage: dict[str, Any]) -> None:
        for key, val in usage.items():
            if key == "cache_creation" and isinstance(val, dict):
                existing = self._usage.get("cache_creation")
                if not isinstance(existing, dict):
                    existing = {}
                existing.update(val)
                self._usage["cache_creation"] = existing
            elif isinstance(val, int) and val >= 0:
                self._usage[key] = val

    @property
    def usage(self) -> dict[str, Any] | None:
        return dict(self._usage) if self._usage else None

    @property
    def parse_error(self) -> bool:
        return self._parse_error

    @property
    def delta_usage_seen(self) -> bool:
        return self._delta_usage_seen


def _stream_usage_complete(usage: dict[str, Any]) -> bool:
    required = (
        "input_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
        "output_tokens",
    )
    return all(
        key in usage and isinstance(usage.get(key), int) and usage[key] >= 0
        for key in required
    )


def finalize_stream_credit(
    request_id: str,
    tap: StreamUsageTap,
    *,
    normal_end: bool,
    cancelled: bool = False,
    exc: BaseException | None = None,
) -> None:
    if exc is not None and _is_connect_before_upstream(exc):
        release(request_id)
        return
    usage = tap.usage
    if (
        normal_end
        and not tap.parse_error
        and usage is not None
        and tap.delta_usage_seen
        and _stream_usage_complete(usage)
    ):
        commit(request_id, usage)
        return
    reason = "stream_cancelled" if cancelled else "stream_abnormal_end"
    if tap.parse_error:
        reason = "stream_unparseable_frame"
    if exc is not None:
        reason = f"stream_error:{type(exc).__name__}"
    _ = reason
    commit(request_id, tap.usage, floor=True)
