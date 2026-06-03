"""Structured Gemini functionResponse payloads — bounded, typed envelopes.

Gemini expects ``functionResponse.response`` as a JSON object. The native tool
loop passes tool results as JSON strings; this module parses them once and
emits a compact ``{ok, status, summary, data, error}`` shape instead of
double-encoding a string under ``result``.
"""

from __future__ import annotations

import json
from typing import Any

_MAX_SUMMARY_CHARS = 500
_MAX_DATA_CHARS = 8000
_MAX_ERROR_CHARS = 2000
_MAX_STRING_FIELD_CHARS = 2000


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _bound_value(value: Any, *, depth: int = 0) -> Any:
    if depth > 4:
        return "[truncated: max depth]"
    if isinstance(value, str):
        return _truncate(value, _MAX_STRING_FIELD_CHARS)
    if isinstance(value, dict):
        return {str(k): _bound_value(v, depth=depth + 1) for k, v in value.items()}
    if isinstance(value, list):
        return [_bound_value(item, depth=depth + 1) for item in value[:50]]
    return value


def _infer_ok(parsed: dict[str, Any]) -> bool:
    if parsed.get("ok") is False:
        return False
    if parsed.get("passed") is False:
        return False
    status = parsed.get("status")
    if isinstance(status, str) and status.lower() in {"error", "failed", "failure"}:
        return False
    error = parsed.get("error")
    if error not in (None, "", {}, []):
        return False
    return True


def _extract_summary(parsed: dict[str, Any]) -> str:
    for key in ("summary", "message", "detail", "result", "content"):
        val = parsed.get(key)
        if val not in (None, ""):
            return _truncate(str(val), _MAX_SUMMARY_CHARS)
    compact = json.dumps(parsed, ensure_ascii=False, default=str)
    return _truncate(compact, _MAX_SUMMARY_CHARS)


def _extract_error(parsed: dict[str, Any]) -> str | None:
    error = parsed.get("error")
    if error in (None, "", {}, []):
        return None
    if isinstance(error, dict):
        msg = error.get("message") or error.get("detail") or error.get("code")
        if msg:
            return _truncate(str(msg), _MAX_ERROR_CHARS)
        return _truncate(json.dumps(error, ensure_ascii=False), _MAX_ERROR_CHARS)
    return _truncate(str(error), _MAX_ERROR_CHARS)


def build_function_response_payload(content: str) -> dict[str, Any]:
    """Build a structured Gemini ``functionResponse.response`` object."""
    raw = content or ""
    parsed: Any
    try:
        parsed = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        text = _truncate(raw, _MAX_DATA_CHARS)
        return {
            "ok": True,
            "status": "success",
            "summary": _truncate(text, _MAX_SUMMARY_CHARS),
            "data": {"text": text},
            "error": None,
        }

    if not isinstance(parsed, dict):
        return {
            "ok": True,
            "status": "success",
            "summary": _truncate(str(parsed), _MAX_SUMMARY_CHARS),
            "data": {"value": _bound_value(parsed)},
            "error": None,
        }

    ok = _infer_ok(parsed)
    status_raw = parsed.get("status")
    status = (
        str(status_raw).lower()
        if isinstance(status_raw, str) and status_raw
        else ("success" if ok else "error")
    )
    bounded = _bound_value(parsed)
    data_json = json.dumps(bounded, ensure_ascii=False, default=str)
    if len(data_json) > _MAX_DATA_CHARS:
        bounded = {"truncated": True, "preview": _truncate(data_json, _MAX_DATA_CHARS)}

    return {
        "ok": ok,
        "status": status,
        "summary": _extract_summary(parsed),
        "data": bounded,
        "error": _extract_error(parsed) if not ok else None,
    }
