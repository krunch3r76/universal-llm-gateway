"""RFC 8785 JSON Canonicalization Scheme (JCS) for material fingerprints."""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any

_MAX_SAFE_INTEGER = (1 << 53) - 1
_MATERIAL_MAX_BYTES = 256 * 1024

_SURROGATE_RE = re.compile(r"[\ud800-\udfff]")


class CanonicalJsonError(ValueError):
    """Material cannot be canonicalized under JCS rules."""


def _utf16_sort_key(key: str) -> bytes:
    return key.encode("utf-16-be")


def _reject_non_string_keys(obj: dict[Any, Any]) -> None:
    for key in obj:
        if not isinstance(key, str):
            raise CanonicalJsonError("object keys must be strings")


def _validate_string(value: str) -> None:
    if _SURROGATE_RE.search(value):
        raise CanonicalJsonError("lone surrogate code units are not allowed in strings")


def _validate_number(value: float | int) -> None:
    if isinstance(value, bool):
        raise CanonicalJsonError("booleans are not JSON numbers")
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            raise CanonicalJsonError("NaN and Infinity are not allowed")
    if isinstance(value, int):
        if abs(value) > _MAX_SAFE_INTEGER:
            raise CanonicalJsonError("integer outside IEEE-754 safe range")


def _format_digits_decimal(*, digits: str, decimal_pos: int, negative: bool) -> str:
    """Place the decimal point *decimal_pos* characters from the left of *digits*."""
    if decimal_pos <= 0:
        body = "0." + ("0" * (-decimal_pos)) + digits
    elif decimal_pos >= len(digits):
        body = digits + ("0" * (decimal_pos - len(digits)))
    else:
        body = digits[:decimal_pos] + "." + digits[decimal_pos:]
    if "." in body:
        body = body.rstrip("0").rstrip(".")
    if not body:
        body = "0"
    return ("-" if negative else "") + body


def _format_exponential(*, coeff: str, exp10: int, negative: bool) -> str:
    if "." in coeff:
        ip, fp = coeff.split(".")
        digits = ip + fp
    else:
        digits = coeff
    if len(digits) > 1:
        mantissa = digits[0] + "." + digits[1:]
        mantissa = mantissa.rstrip("0").rstrip(".")
    else:
        mantissa = digits
    prefix = "-" if negative else ""
    if exp10 >= 0:
        return f"{prefix}{mantissa}e+{exp10}"
    return f"{prefix}{mantissa}e{exp10}"


def _es6_number_string(value: float) -> str:
    if value == 0.0 or value == -0.0:
        return "0"
    if value == math.trunc(value):
        iv = int(value)
        if abs(iv) <= _MAX_SAFE_INTEGER:
            return str(iv)

    text = repr(value)
    negative = text.startswith("-")
    if negative:
        text = text[1:]

    if "e" not in text:
        if "." in text:
            text = text.rstrip("0").rstrip(".")
        return ("-" if negative else "") + text

    coeff, exp_part = text.split("e")
    exp10 = int(exp_part)
    if "." in coeff:
        ip, fp = coeff.split(".")
        int_len = len(ip)
        digits = ip + fp
    else:
        int_len = len(coeff)
        digits = coeff

    if -7 < exp10 < 21:
        return _format_digits_decimal(
            digits=digits,
            decimal_pos=int_len + exp10,
            negative=negative,
        )
    return _format_exponential(coeff=coeff, exp10=exp10, negative=negative)


def _serialize_number(value: float | int) -> str:
    _validate_number(value)
    if isinstance(value, int):
        return str(value)
    return _es6_number_string(float(value))


def _escape_string(value: str) -> str:
    _validate_string(value)
    return json.dumps(value, ensure_ascii=False)


def jcs_serialize(value: Any) -> str:
    """Serialize *value* to a JCS string (no whitespace)."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return _escape_string(value)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return _serialize_number(value)
    if isinstance(value, list):
        return "[" + ",".join(jcs_serialize(item) for item in value) + "]"
    if isinstance(value, dict):
        _reject_non_string_keys(value)
        keys = sorted(value.keys(), key=_utf16_sort_key)
        parts = []
        for key in keys:
            parts.append(_escape_string(key) + ":" + jcs_serialize(value[key]))
        return "{" + ",".join(parts) + "}"
    raise CanonicalJsonError(f"unsupported JSON type: {type(value)!r}")


def material_fingerprint(material: dict[str, Any]) -> str:
    """Return ``sha256:<hex>`` over JCS(material)."""
    encoded = jcs_serialize(material).encode("utf-8")
    if len(encoded) > _MATERIAL_MAX_BYTES:
        raise CanonicalJsonError("material exceeds maximum byte size")
    digest = hashlib.sha256(encoded).hexdigest()
    return f"sha256:{digest}"


def parse_json_strict(text: str) -> Any:
    """Parse JSON rejecting NaN/Infinity via parse_constant."""
    return json.loads(text, parse_constant=_reject_parse_constant)


def _reject_parse_constant(value: str) -> None:
    raise CanonicalJsonError(f"non-finite constant not allowed: {value!r}")
