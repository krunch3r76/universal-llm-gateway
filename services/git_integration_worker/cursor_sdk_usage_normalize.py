"""Normalize and finalize cursor-sdk TokenUsage for worker closeout emit.

Post-wait ``run.usage`` / ``result.usage`` is authoritative for run field
breakdown. Stream ``SDKUsageMessage`` / turn-ended payloads supply per-turn
breakdown and status nuance. ``reasoning_tokens`` is optional enrichment
(subset of output) — never added into ``total_tokens``.

Cache / total semantics (friction a:37158 + review A1/A2): Cursor SDK
``TokenUsage`` on the local agent store reports ``inputTokens`` **including**
cache reads (observed on 80/80 recent ledger rows: ``cache_read ≤ input``;
wire ``totalTokens = input + output + cache_read + cache_write`` therefore
double-counts cache). Dashboard/Admin API rows can show ``cache ≫ input``
(exclusive columns) — when that shape appears, keep the exclusive sum.

Honest dashboard-comparable ``total_tokens`` for the inclusive Cursor shape is
``input + output`` (cache fields remain as breakdown). Every normalized map
carries public ``total_tokens_basis`` ∈ {``inclusive``, ``wire``,
``exclusive_sum``}. Reconcile compares totals only when both sides share the
same basis (A1). Internal ``_total_derived`` remains True when basis ≠
``wire`` for StreamCapture compatibility; underscore tags are stripped before
emit.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

UsageCaptureStatus = Literal["captured", "partial", "missing", "reconciled_delta"]
TotalTokensBasis = Literal["inclusive", "wire", "exclusive_sum"]

_INPUT_TOKEN_KEYS = ("input_tokens", "prompt_tokens", "input", "inputTokens")
_OUTPUT_TOKEN_KEYS = ("output_tokens", "completion_tokens", "output", "outputTokens")
_TOTAL_TOKEN_KEYS = ("total_tokens", "total", "totalTokens")
_CACHE_READ_KEYS = ("cache_read_tokens", "cacheReadTokens")
_CACHE_WRITE_KEYS = ("cache_write_tokens", "cacheWriteTokens")
_REASONING_KEYS = ("reasoning_tokens", "reasoningTokens")
_SPEND_PASS_THROUGH_KEYS = ("cost_usd", "credits", "spend", "cost")
TOTAL_DERIVED_KEY = "_total_derived"
TOTAL_BASIS_KEY = "_total_basis"
TOTAL_TOKENS_BASIS_FIELD = "total_tokens_basis"
_SUM_FIELDS = (
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
)


def _input_includes_cache(
    *,
    input_tokens: int | None,
    cache_read: int | None,
) -> bool:
    """True when cache_read looks like a subset of input (Cursor SDK shape).

    Requires a positive ``cache_read``: ``0`` is not a signal (FakeUsage /
    sparse payloads). ``cache_read > input`` is the Admin/dashboard exclusive
    shape — do not rewrite those totals. Absent cache_read ⇒ no correction.
    """
    if input_tokens is None or cache_read is None or cache_read == 0:
        return False
    return cache_read <= input_tokens


def _honest_total_tokens(
    *,
    input_tokens: int | None,
    output_tokens: int | None,
    cache_read: int | None,
    cache_write: int | None,
) -> int | None:
    """Dashboard-comparable total for this Cursor-sdk payload shape."""
    if _input_includes_cache(input_tokens=input_tokens, cache_read=cache_read):
        parts = [value for value in (input_tokens, output_tokens) if value is not None]
        return sum(parts) if parts else None
    parts = [
        value
        for value in (input_tokens, output_tokens, cache_read, cache_write)
        if value is not None
    ]
    return sum(parts) if parts else None


def _usage_basis(usage: Mapping[str, Any] | None) -> TotalTokensBasis | None:
    if not usage:
        return None
    public = usage.get(TOTAL_TOKENS_BASIS_FIELD)
    if public in ("inclusive", "wire", "exclusive_sum"):
        return public  # type: ignore[return-value]
    internal = usage.get(TOTAL_BASIS_KEY)
    if internal in ("inclusive", "wire", "exclusive_sum"):
        return internal  # type: ignore[return-value]
    # Legacy pre-A1 maps: derived bool ⇒ unknown formula; treat as exclusive_sum
    # only when no basis was recorded and _total_derived is set.
    if usage.get(TOTAL_DERIVED_KEY):
        return "exclusive_sum"
    if usage.get("total_tokens") is not None:
        return "wire"
    return None


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

    For Cursor-inclusive payloads (``cache_read ≤ input``), emit honest
    ``total_tokens = input + output`` even when wire ``totalTokens`` double-counts
    cache (a:37158). Exclusive shape (``cache_read > input``) keeps wire total
    when present, else ``input + output + cache_read + cache_write``. Emits
    public ``total_tokens_basis`` and keeps ``_total_derived`` when basis ≠
    ``wire``. ``reasoning_tokens`` is a subset of output — never added into total.
    """
    input_tokens = _first_token_count(raw, _INPUT_TOKEN_KEYS)
    output_tokens = _first_token_count(raw, _OUTPUT_TOKEN_KEYS)
    wire_total = _first_token_count(raw, _TOTAL_TOKEN_KEYS)
    cache_read = _first_token_count(raw, _CACHE_READ_KEYS)
    cache_write = _first_token_count(raw, _CACHE_WRITE_KEYS)
    reasoning = _first_token_count(raw, _REASONING_KEYS)
    if (
        input_tokens is None
        and output_tokens is None
        and wire_total is None
        and cache_read is None
        and cache_write is None
    ):
        return None, False

    honest = _honest_total_tokens(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read=cache_read,
        cache_write=cache_write,
    )
    basis: TotalTokensBasis
    if _input_includes_cache(input_tokens=input_tokens, cache_read=cache_read):
        total_tokens = honest
        basis = "inclusive"
    elif wire_total is None:
        total_tokens = honest
        basis = "exclusive_sum"
    else:
        total_tokens = wire_total
        basis = "wire"

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
    normalized[TOTAL_TOKENS_BASIS_FIELD] = basis
    normalized[TOTAL_BASIS_KEY] = basis
    if basis != "wire":
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
    bases = {_usage_basis(item) for item in items}
    bases.discard(None)
    if len(bases) == 1:
        basis = next(iter(bases))
        assert basis is not None
        aggregated[TOTAL_TOKENS_BASIS_FIELD] = basis
        aggregated[TOTAL_BASIS_KEY] = basis
        if basis != "wire":
            aggregated[TOTAL_DERIVED_KEY] = True
    elif bases:
        # Mixed bases → do not claim a comparable aggregate total basis.
        aggregated[TOTAL_TOKENS_BASIS_FIELD] = "exclusive_sum"
        aggregated[TOTAL_BASIS_KEY] = "exclusive_sum"
        aggregated[TOTAL_DERIVED_KEY] = True
    return aggregated


def latest_turn_used_tokens(
    turn_usages: tuple[Mapping[str, Any] | None, ...],
) -> int | None:
    """Return the latest turn's input/total tokens — never sum across turns.

    On Cursor SDK end-of-run payloads this is cumulative ``input_tokens``, not a
    live context-window occupancy (a:37158 ``usage_live.used_tokens`` nuance).
    """
    for raw in reversed(turn_usages):
        if not raw:
            continue
        normalized, mappable = normalize_usage_map(raw)
        if not mappable or normalized is None:
            continue
        for key in ("input_tokens", "prompt_tokens", "total_tokens"):
            value = coerce_non_negative_int(normalized.get(key))
            if value is not None:
                return value
    return None


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
        if mixed_turns:
            return aggregated, "partial"
        return aggregated, "captured"

    if token_delta_sum > 0:
        return {
            TOTAL_DERIVED_KEY: True,
            TOTAL_BASIS_KEY: "exclusive_sum",
            TOTAL_TOKENS_BASIS_FIELD: "exclusive_sum",
            "total_tokens": token_delta_sum,
        }, "partial"

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
    """Apply post-wait authority; reconcile same-basis totals when both present."""
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
    stream_basis = _usage_basis(stream_usage)
    post_basis = _usage_basis(normalized)
    # A1: same-basis deltas earn reconciled_delta (not wire-only).
    if (
        stream_total is not None
        and post_total is not None
        and stream_total != post_total
        and stream_basis is not None
        and stream_basis == post_basis
    ):
        return public, "reconciled_delta"
    # Authoritative post-wait with a total is captured even if stream was holey
    # (R finding #3) — understating quality was the prior bug.
    if post_total is not None:
        return public, "captured"
    if stream_status == "partial":
        return public, "partial"
    return public, "captured"
