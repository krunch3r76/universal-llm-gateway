"""Normalize and finalize cursor-sdk TokenUsage for worker closeout emit.

Post-wait ``run.usage`` / ``result.usage`` is authoritative for run totals.
Stream ``SDKUsageMessage`` / turn-ended payloads supply per-turn breakdown and
status nuance. ``reasoning_tokens`` is optional enrichment (subset of output) —
not required for dashboard-comparable ``total_tokens``.

Cache / total semantics (R finding #1, path-sim 5361): when wire ``total_tokens``
is absent, fallback recompute is ``input + output + cache_read + cache_write``
under the assumption that ``input_tokens`` excludes cache (Anthropic-style
breakdown columns). That derived total is tagged ``_total_derived`` so
reconcile never compares a recomputed stream total against a wire post-wait
total (heterogeneous formulas → spurious ``reconciled_delta``). Prefer wire
totals whenever present. Tag is stripped before emit.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

UsageCaptureStatus = Literal["captured", "partial", "missing", "reconciled_delta"]

_INPUT_TOKEN_KEYS = ("input_tokens", "prompt_tokens", "input", "inputTokens")
_OUTPUT_TOKEN_KEYS = ("output_tokens", "completion_tokens", "output", "outputTokens")
_TOTAL_TOKEN_KEYS = ("total_tokens", "total", "totalTokens")
_CACHE_READ_KEYS = ("cache_read_tokens", "cacheReadTokens")
_CACHE_WRITE_KEYS = ("cache_write_tokens", "cacheWriteTokens")
_REASONING_KEYS = ("reasoning_tokens", "reasoningTokens")
_SPEND_PASS_THROUGH_KEYS = ("cost_usd", "credits", "spend", "cost")
TOTAL_DERIVED_KEY = "_total_derived"
_SUM_FIELDS = (
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
)


def public_usage(usage: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Drop internal normalize tags before emit / return."""
    if usage is None:
        return None
    return {key: value for key, value in usage.items() if not str(key).startswith("_")}


def coerce_non_negative_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float):
        return int(value) if value >= 0 else None
    if isinstance(value, str):
        try:
            parsed = int(value)
        except ValueError:
            return None
        return parsed if parsed >= 0 else None
    return None


def _first_token_count(raw: Mapping[str, Any], keys: tuple[str, ...]) -> int | None:
    for key in keys:
        if key in raw:
            parsed = coerce_non_negative_int(raw[key])
            if parsed is not None:
                return parsed
    return None


def usage_payload_from_object(raw: Any) -> Mapping[str, Any] | None:
    """Coerce ``TokenUsage``, dataclass-like SDK objects, or mappings to a dict."""
    if raw is None:
        return None
    if isinstance(raw, Mapping):
        return raw
    payload: dict[str, Any] = {}
    for field in (
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
        "reasoning_tokens",
    ):
        value = getattr(raw, field, None)
        if value is not None:
            payload[field] = value
    return payload or None


def normalize_usage_map(raw: Mapping[str, Any]) -> tuple[dict[str, Any] | None, bool]:
    """Map SDK usage payloads to a canonical token vector (+ optional spend).

    Prefer wire ``total_tokens`` (dashboard-comparable). When absent, recompute
    as input + output + cache_read + cache_write (assumes ``input_tokens``
    excludes cache) and tag ``_total_derived`` so reconcile stays formula-safe.
    ``reasoning_tokens`` is a subset of output — never added into total.
    """
    input_tokens = _first_token_count(raw, _INPUT_TOKEN_KEYS)
    output_tokens = _first_token_count(raw, _OUTPUT_TOKEN_KEYS)
    total_tokens = _first_token_count(raw, _TOTAL_TOKEN_KEYS)
    cache_read = _first_token_count(raw, _CACHE_READ_KEYS)
    cache_write = _first_token_count(raw, _CACHE_WRITE_KEYS)
    reasoning = _first_token_count(raw, _REASONING_KEYS)
    if (
        input_tokens is None
        and output_tokens is None
        and total_tokens is None
        and cache_read is None
        and cache_write is None
    ):
        return None, False
    total_derived = False
    if total_tokens is None:
        parts = [
            value
            for value in (input_tokens, output_tokens, cache_read, cache_write)
            if value is not None
        ]
        if parts:
            total_tokens = sum(parts)
            total_derived = True
    normalized: dict[str, Any] = {}
    if input_tokens is not None:
        normalized["input_tokens"] = input_tokens
    if output_tokens is not None:
        normalized["output_tokens"] = output_tokens
    if cache_read is not None:
        normalized["cache_read_tokens"] = cache_read
    if cache_write is not None:
        normalized["cache_write_tokens"] = cache_write
    if total_tokens is not None:
        normalized["total_tokens"] = total_tokens
    if total_derived:
        normalized[TOTAL_DERIVED_KEY] = True
    if reasoning is not None:
        normalized["reasoning_tokens"] = reasoning
    for key in _SPEND_PASS_THROUGH_KEYS:
        if key in raw:
            normalized[key] = raw[key]
    return normalized, True


def sum_normalized_usages(items: tuple[dict[str, Any], ...]) -> dict[str, Any]:
    """Sum per-turn mappable usage maps before emit."""
    aggregated: dict[str, Any] = {}
    for field in _SUM_FIELDS:
        values = [item[field] for item in items if field in item]
        if values:
            aggregated[field] = sum(values)
    reasoning_values = [
        item["reasoning_tokens"] for item in items if "reasoning_tokens" in item
    ]
    if reasoning_values:
        aggregated["reasoning_tokens"] = sum(reasoning_values)
    for key in _SPEND_PASS_THROUGH_KEYS:
        for item in reversed(items):
            if key in item:
                aggregated[key] = item[key]
                break
    return aggregated


def aggregate_stream_usage(
    *,
    turn_usages: tuple[Mapping[str, Any] | None, ...],
    token_delta_sum: int,
) -> tuple[dict[str, Any] | None, UsageCaptureStatus]:
    """Derive stream-side usage + status before post-wait finalize."""
    turns_with_usage = sum(1 for usage in turn_usages if usage)
    turns_without_usage = sum(1 for usage in turn_usages if not usage)
    mixed_turns = turns_with_usage > 0 and turns_without_usage > 0

    normalized_turns: list[dict[str, Any]] = []
    for raw in turn_usages:
        if not raw:
            continue
        normalized, mappable = normalize_usage_map(raw)
        if not mappable:
            return {"usage_raw": dict(raw)}, "partial"
        if normalized is not None:
            normalized_turns.append(normalized)

    if normalized_turns:
        aggregated = sum_normalized_usages(tuple(normalized_turns))
        # Any turn used a recomputed total → aggregate total is not wire-pure.
        if any(turn.get(TOTAL_DERIVED_KEY) for turn in normalized_turns):
            aggregated[TOTAL_DERIVED_KEY] = True
        if mixed_turns:
            return aggregated, "partial"
        return aggregated, "captured"

    if token_delta_sum > 0:
        return {TOTAL_DERIVED_KEY: True, "total_tokens": token_delta_sum}, "partial"

    return None, "missing"


def _post_wait_payload(*, run: Any, result: Any) -> Mapping[str, Any] | None:
    for source in (
        getattr(run, "usage", None) if run is not None else None,
        getattr(result, "usage", None) if result is not None else None,
    ):
        payload = usage_payload_from_object(source)
        if payload is not None:
            return payload
    return None


def finalize_usage_with_post_wait(
    *,
    stream_usage: dict[str, Any] | None,
    stream_status: UsageCaptureStatus,
    run: Any = None,
    result: Any = None,
) -> tuple[dict[str, Any] | None, UsageCaptureStatus]:
    """Apply post-wait authority; reconcile against stream totals when both present."""
    payload = _post_wait_payload(run=run, result=result)
    if payload is None:
        return public_usage(stream_usage), stream_status

    normalized, mappable = normalize_usage_map(payload)
    if not mappable:
        return {"usage_raw": dict(payload)}, "partial"
    if normalized is None:
        return public_usage(stream_usage), stream_status

    public = public_usage(normalized)
    assert public is not None

    if stream_usage is None:
        return public, "captured"

    stream_total = coerce_non_negative_int(stream_usage.get("total_tokens"))
    post_total = coerce_non_negative_int(normalized.get("total_tokens"))
    stream_derived = bool(stream_usage.get(TOTAL_DERIVED_KEY))
    post_derived = bool(normalized.get(TOTAL_DERIVED_KEY))
    # Only wire-vs-wire deltas earn reconciled_delta (R finding #1).
    if (
        stream_total is not None
        and post_total is not None
        and stream_total != post_total
        and not stream_derived
        and not post_derived
    ):
        return public, "reconciled_delta"
    # Authoritative post-wait with a total is captured even if stream was holey
    # (R finding #3) — understating quality was the prior bug.
    if post_total is not None:
        return public, "captured"
    if stream_status == "partial":
        return public, "partial"
    return public, "captured"
