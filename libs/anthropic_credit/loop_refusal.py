"""Credit refusal parsing and native-loop cap notices (shared libs surface)."""

from __future__ import annotations

import json
import logging

import httpx

from .policy import AnthropicCreditRefused

logger = logging.getLogger(__name__)


def _error_payload_from_http(exc: httpx.HTTPStatusError) -> dict[str, object] | None:
    if exc.response is None:
        return None
    try:
        payload = exc.response.json()
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    err = payload.get("error")
    if isinstance(err, dict):
        return err
    detail = payload.get("detail")
    if isinstance(detail, dict):
        nested = detail.get("error")
        if isinstance(nested, dict):
            return nested
    return None


def parse_credit_refusal_type(exc: BaseException) -> str | None:
    """Extract anthropic credit refusal code from HTTP-layer errors."""
    if isinstance(exc, AnthropicCreditRefused):
        return exc.code
    err = _error_payload_from_http(exc) if isinstance(exc, httpx.HTTPStatusError) else None
    if err is not None:
        code = err.get("type")
        if isinstance(code, str):
            return code
    if isinstance(exc, RuntimeError):
        text = str(exc)
        marker = '"type":'
        if marker in text:
            try:
                start = text.index("{")
                payload = json.loads(text[start:])
                err = payload.get("error") if isinstance(payload, dict) else None
                if isinstance(err, dict) and isinstance(err.get("type"), str):
                    return err["type"]
            except (ValueError, json.JSONDecodeError):
                pass
    return None


def parse_credit_refusal_detail(exc: BaseException) -> str | None:
    """Extract human-readable refusal detail from credit-layer errors."""
    if isinstance(exc, AnthropicCreditRefused):
        detail = exc.detail
        return detail if detail else None
    err = _error_payload_from_http(exc) if isinstance(exc, httpx.HTTPStatusError) else None
    if err is not None:
        for key in ("message", "detail"):
            val = err.get(key)
            if isinstance(val, str) and val.strip():
                return val
    return None


def emit_loop_refused(turn_index: int) -> None:
    payload = {"turn_index": turn_index}
    from . import policy as _policy

    _policy.emit("anthropic.credit.loop_refused", payload)
    if _policy._module_emit is _policy._noop_emit:
        logger.info("anthropic.credit.loop_refused %s", payload)


def credit_cap_notice(
    code: str | None = None, detail: str | None = None
) -> str:
    if code is None:
        return (
            "[Anthropic credit cycle budget exhausted] Further model turns were "
            "stopped to stay within the configured spend cap. Partial results above "
            "are preserved."
        )
    if code == "anthropic_credit_latched":
        headline = "credit latch active"
        suffix = (
            "Further model turns were stopped until the credit latch clears. "
            "Partial results above are preserved."
        )
    elif code == "anthropic_mcp_serialized":
        headline = (
            "MCP connector requests are serialized; another MCP reservation was open"
        )
        suffix = (
            "Further model turns were stopped until the open MCP reservation "
            "completes. Partial results above are preserved."
        )
    elif code == "anthropic_mcp_cap_exceeded":
        headline = "MCP connector spend cap exceeded"
        suffix = (
            "Further model turns were stopped to stay within the configured MCP "
            "spend cap. Partial results above are preserved."
        )
    else:
        headline = "credit cycle budget exhausted"
        suffix = (
            "Further model turns were stopped to stay within the configured spend "
            "cap. Partial results above are preserved."
        )
    body = f"[Anthropic {headline}] {suffix} code={code}"
    if detail:
        body = f"{body} {detail}"
    return body
