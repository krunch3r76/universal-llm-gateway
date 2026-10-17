"""Actual usage cost calculation across all Anthropic token classes (leg A9 core)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from event_store.model_rate_table import ModelRateRow

_TOKENS_PER_MILLION = Decimal("1000000")
_REQUIRED_USAGE_FIELDS: tuple[str, ...] = (
    "input_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
    "output_tokens",
)


def _extract_nonneg_int(mapping: dict[str, Any], key: str) -> int | None:
    if key not in mapping:
        return None
    val = mapping.get(key)
    if val is None or isinstance(val, bool) or not isinstance(val, int) or val < 0:
        return None
    return val


def usage_cost_usd(
    usage: dict[str, Any] | None,
    rate_row: ModelRateRow,
) -> tuple[Decimal | None, tuple[str, ...]]:
    """Compute actual USD cost and tuple of missing counter names.

    Pricing rules (PLAN-r2 § r2.3):
    - uncached ``input_tokens`` × input rate
    - ``cache_read_input_tokens`` × cache-read rate
    - ``cache_creation_input_tokens`` priced by
      ``cache_creation.ephemeral_5m_input_tokens`` (cache-write rate) and
      ``ephemeral_1h_input_tokens`` (2.0 × input rate) when present, else all
      at 2.0 × input rate
    - ``output_tokens`` × output rate
    - long tier when ``input_tokens + cache_creation_input_tokens + cache_read_input_tokens``
      exceeds ``rate_row.long_prompt_threshold_tokens``
    """
    if usage is None or not isinstance(usage, dict):
        return None, _REQUIRED_USAGE_FIELDS

    present: dict[str, int] = {}
    missing: list[str] = []
    for field in _REQUIRED_USAGE_FIELDS:
        val = _extract_nonneg_int(usage, field)
        if val is None:
            missing.append(field)
        else:
            present[field] = val

    if len(missing) == len(_REQUIRED_USAGE_FIELDS):
        return None, tuple(missing)

    input_tokens = present.get("input_tokens", 0)
    cache_creation_tokens = present.get("cache_creation_input_tokens", 0)
    cache_read_tokens = present.get("cache_read_input_tokens", 0)
    output_tokens = present.get("output_tokens", 0)

    total_input_tokens = input_tokens + cache_creation_tokens + cache_read_tokens
    threshold = rate_row.long_prompt_threshold_tokens
    use_long = threshold is not None and total_input_tokens > threshold

    if use_long:
        in_rate_f = (
            rate_row.long_input_rate_per_m
            if rate_row.long_input_rate_per_m is not None
            else rate_row.input_rate_per_m
        )
        out_rate_f = (
            rate_row.long_output_rate_per_m
            if rate_row.long_output_rate_per_m is not None
            else rate_row.output_rate_per_m
        )
        cw_rate_f = (
            rate_row.long_cache_write_rate_per_m
            if rate_row.long_cache_write_rate_per_m is not None
            else rate_row.cache_write_rate_per_m
        )
        cr_rate_f = (
            rate_row.long_cache_read_rate_per_m
            if rate_row.long_cache_read_rate_per_m is not None
            else rate_row.cache_read_rate_per_m
        )
    else:
        in_rate_f = rate_row.input_rate_per_m
        out_rate_f = rate_row.output_rate_per_m
        cw_rate_f = rate_row.cache_write_rate_per_m
        cr_rate_f = rate_row.cache_read_rate_per_m

    in_rate = Decimal(str(in_rate_f))
    out_rate = Decimal(str(out_rate_f))
    cw_5m_rate = (
        Decimal(str(cw_rate_f)) if cw_rate_f is not None else Decimal("1.25") * in_rate
    )
    cw_1h_rate = Decimal("2.0") * in_rate
    cr_rate = Decimal(str(cr_rate_f)) if cr_rate_f is not None else in_rate

    # Cache creation breakdown: 5m vs 1h when present and valid, else 2.0x input.
    cache_creation_cost_num = Decimal(0)
    if cache_creation_tokens > 0:
        breakdown = usage.get("cache_creation")
        e5m = (
            _extract_nonneg_int(breakdown, "ephemeral_5m_input_tokens")
            if isinstance(breakdown, dict)
            else None
        )
        e1h = (
            _extract_nonneg_int(breakdown, "ephemeral_1h_input_tokens")
            if isinstance(breakdown, dict)
            else None
        )
        if e5m is not None and e1h is not None and (e5m + e1h) <= cache_creation_tokens:
            remainder = cache_creation_tokens - (e5m + e1h)
            cache_creation_cost_num = (
                Decimal(e5m) * cw_5m_rate + Decimal(e1h + remainder) * cw_1h_rate
            )
        else:
            cache_creation_cost_num = Decimal(cache_creation_tokens) * cw_1h_rate

    total_num = (
        Decimal(input_tokens) * in_rate
        + cache_creation_cost_num
        + Decimal(cache_read_tokens) * cr_rate
        + Decimal(output_tokens) * out_rate
    )
    cost = total_num / _TOKENS_PER_MILLION
    return cost, tuple(missing)
