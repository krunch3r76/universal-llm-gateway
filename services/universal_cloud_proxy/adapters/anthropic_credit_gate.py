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
    emit,
    latch,
    release,
)
from anthropic_credit.reservation import _is_mcp_connector_class

logger = logging.getLogger(__name__)

_PRICED_USAGE_KEYS = (
    "input_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
    "output_tokens",
)
_KNOWN_OUTPUT_DETAIL_KEYS = frozenset({"thinking_tokens"})


def _is_count(v: object) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v >= 0


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
            text = str(tag).strip()
            if text and text != "ttl_expired":
                return text
    if upstream_body:
        metadata = upstream_body.get("metadata")
        if isinstance(metadata, dict):
            tag = metadata.get("x-ulg-credit-tag") or metadata.get("ulg_credit_tag")
            if tag:
                text = str(tag).strip()
                if text and text != "ttl_expired":
                    return text
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
    _unknown_dict_usage: bool = False
    _unknown_usage_key_names: list[str] = field(default_factory=list)
    _output_details: dict[str, int] = field(default_factory=dict)
    _output_details_invalid: bool = False
    _invalid_usage_value: bool = False
    _invalid_usage_key_names: list[str] = field(default_factory=list)
    _message_model: str | None = None
    _current_event: str = ""
    _delta_usage_seen: bool = False

    def _note_invalid_usage_key(self, key: str) -> None:
        self._invalid_usage_value = True
        if key not in self._invalid_usage_key_names:
            self._invalid_usage_key_names.append(key)

    def _note_unknown_dict_key(self, key: str) -> None:
        if key not in self._unknown_usage_key_names:
            self._unknown_usage_key_names.append(key)

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
                model = msg.get("model")
                if isinstance(model, str):
                    self._message_model = model
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
            if key in _PRICED_USAGE_KEYS:
                if _is_count(val):
                    self._usage[key] = val
                else:
                    self._note_invalid_usage_key(key)
            elif key == "cache_creation":
                if isinstance(val, dict):
                    existing = self._usage.get("cache_creation")
                    if not isinstance(existing, dict):
                        existing = {}
                    for sub_key, sub_val in val.items():
                        if _is_count(sub_val):
                            existing[sub_key] = sub_val
                        else:
                            self._note_invalid_usage_key("cache_creation")
                    self._usage["cache_creation"] = existing
                else:
                    self._note_invalid_usage_key("cache_creation")
            elif key == "output_tokens_details":
                if isinstance(val, dict):
                    for sub_key, sub_val in val.items():
                        if sub_key not in _KNOWN_OUTPUT_DETAIL_KEYS:
                            self._output_details_invalid = True
                        if _is_count(sub_val):
                            self._output_details[sub_key] = sub_val
                        else:
                            self._output_details_invalid = True
                else:
                    self._output_details_invalid = True
            elif key == "server_tool_use":
                if isinstance(val, dict):
                    existing = self._usage.get("server_tool_use")
                    if not isinstance(existing, dict):
                        existing = {}
                    for sub_key, sub_val in val.items():
                        if _is_count(sub_val):
                            existing[sub_key] = sub_val
                        else:
                            self._unknown_dict_usage = True
                            self._note_unknown_dict_key("server_tool_use")
                    self._usage["server_tool_use"] = existing
                else:
                    self._note_invalid_usage_key("server_tool_use")
            elif isinstance(val, dict):
                self._unknown_dict_usage = True
                self._note_unknown_dict_key(key)
            elif _is_count(val):
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

    @property
    def unknown_dict_usage(self) -> bool:
        return self._unknown_dict_usage

    @property
    def unknown_usage_keys(self) -> list[str]:
        return list(self._unknown_usage_key_names)

    @property
    def invalid_usage_value(self) -> bool:
        return self._invalid_usage_value

    @property
    def invalid_usage_keys(self) -> list[str]:
        return list(self._invalid_usage_key_names)

    @property
    def output_details_undercount_risk(self) -> bool:
        if self._output_details_invalid:
            return True
        billing_output = self._usage.get("output_tokens")
        if not _is_count(billing_output):
            return True
        detail_sum = sum(self._output_details.values())
        return detail_sum > billing_output

    @property
    def stream_meter_model(self) -> str | None:
        if not self._message_model:
            return None
        normalized = normalize_meter_model(self._message_model)
        return normalized or None


def _normal_end_floor_reasons(tap: StreamUsageTap) -> list[str]:
    reasons: list[str] = []
    if tap.unknown_dict_usage:
        reasons.append("unknown_dict_usage")
    if tap.invalid_usage_value:
        reasons.append("invalid_usage_value")
    if tap.output_details_undercount_risk:
        reasons.append("output_details_undercount_risk")
    return reasons


def _primary_normal_end_floor_reason(tap: StreamUsageTap) -> str:
    reasons = _normal_end_floor_reasons(tap)
    assert reasons
    return reasons[0]


def _emit_stream_credit_floored(
    request_id: str,
    *,
    reason: str,
    tap: StreamUsageTap,
    reasons: list[str] | None = None,
) -> None:
    output_tokens = tap._usage.get("output_tokens")
    if not _is_count(output_tokens):
        output_tokens = None
    emit_reasons = reasons if reasons is not None else [reason]
    try:
        emit(
            "anthropic.credit.stream.floored",
            {
                "request_id": request_id,
                "model": tap.stream_meter_model,
                "reason": reason,
                "reasons": emit_reasons,
                "unknown_usage_keys": tap.unknown_usage_keys,
                "invalid_usage_keys": tap.invalid_usage_keys,
                "output_tokens": output_tokens,
                "output_details": dict(tap._output_details),
            },
        )
    except Exception:
        logger.debug("anthropic.credit.stream.floored emit failed", exc_info=True)


def _stream_usage_complete(usage: dict[str, Any]) -> bool:
    required = (
        "input_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
        "output_tokens",
    )
    return all(key in usage and _is_count(usage.get(key)) for key in required)


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
        floor = (
            tap.unknown_dict_usage
            or tap.invalid_usage_value
            or tap.output_details_undercount_risk
        )
        commit(request_id, usage, floor=floor)
        if floor:
            floor_reason = _primary_normal_end_floor_reason(tap)
            _emit_stream_credit_floored(
                request_id,
                reason=floor_reason,
                tap=tap,
                reasons=_normal_end_floor_reasons(tap),
            )
        return
    reason = "stream_cancelled" if cancelled else "stream_abnormal_end"
    if tap.parse_error:
        reason = "stream_unparseable_frame"
    if exc is not None:
        reason = f"stream_error:{type(exc).__name__}"
    commit(request_id, tap.usage, floor=True)
    _emit_stream_credit_floored(
        request_id, reason=reason, tap=tap, reasons=[reason]
    )
