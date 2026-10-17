"""Credit refusal parsing and native-loop cap notices (shared libs surface)."""

from __future__ import annotations

import json

import httpx

from .policy import AnthropicCreditRefused, emit


def parse_credit_refusal_type(exc: BaseException) -> str | None:
    """Extract anthropic credit refusal code from HTTP-layer errors."""
    if isinstance(exc, AnthropicCreditRefused):
        return exc.code
    if isinstance(exc, httpx.HTTPStatusError) and exc.response is not None:
        try:
            payload = exc.response.json()
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            err = payload.get("error")
            if isinstance(err, dict):
                code = err.get("type")
                if isinstance(code, str):
                    return code
            detail = payload.get("detail")
            if isinstance(detail, dict):
                err = detail.get("error")
                if isinstance(err, dict):
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


def emit_loop_refused(turn_index: int) -> None:
    emit(
        "anthropic.credit.loop_refused",
        {"turn_index": turn_index},
    )


def credit_cap_notice() -> str:
    return (
        "[Anthropic credit cycle budget exhausted] Further model turns were "
        "stopped to stay within the configured spend cap. Partial results above "
        "are preserved."
    )
