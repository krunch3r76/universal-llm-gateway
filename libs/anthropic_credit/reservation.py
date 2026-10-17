"""Upper-bound USD reservation sizing for Anthropic Messages requests (leg A2)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal
from typing import Any

from event_store.model_rate_table import ModelRateRow

from .config import CreditConfig

_MICRO_USD = Decimal("0.000001")
_TOKENS_PER_MILLION = Decimal("1000000")
_PLAIN_TOOL_TYPES = frozenset({"custom", "function"})


class ReservationRefused(Exception):  # noqa: N818
    """Raised when an Anthropic request cannot be bounded for reservation."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


@dataclass(frozen=True, slots=True)
class Reservation:
    """Upper-bound dollar reservation for an Anthropic request."""

    usd: Decimal
    input_tokens_bound: int
    output_tokens_bound: int
    mcp_class: bool
    basis: str


def _count_media_blocks(node: Any) -> int:
    """Count image and document content blocks recursively."""
    if isinstance(node, dict):
        block_type = node.get("type")
        own = 1 if block_type in ("image", "document", "image_url") else 0
        return own + sum(_count_media_blocks(v) for v in node.values())
    if isinstance(node, list):
        return sum(_count_media_blocks(item) for item in node)
    return 0


def _has_cache_control(upstream_body: dict[str, Any]) -> bool:
    """Return True when ``cache_control`` appears anywhere in the request body."""
    if upstream_body.get("cache_control") is not None:
        return True

    def _walk(node: Any) -> bool:
        if isinstance(node, dict):
            if node.get("cache_control") is not None:
                return True
            return any(_walk(v) for v in node.values())
        if isinstance(node, list):
            return any(_walk(item) for item in node)
        return False

    for section in ("system", "messages", "tools"):
        if _walk(upstream_body.get(section)):
            return True
    return False


def _is_mcp_connector_class(upstream_body: dict[str, Any]) -> bool:
    """Return True if the body uses MCP servers or any server-side tool."""
    mcp_servers = upstream_body.get("mcp_servers")
    if isinstance(mcp_servers, list) and len(mcp_servers) > 0:
        return True
    if mcp_servers:
        return True

    tools = upstream_body.get("tools")
    if isinstance(tools, list):
        for tool in tools:
            if not isinstance(tool, dict):
                continue
            tool_type = tool.get("type")
            if tool_type is None:
                continue
            tool_type_str = str(tool_type).strip()
            if tool_type_str and tool_type_str not in _PLAIN_TOOL_TYPES:
                return True
    return False


def _compute_input_bound(
    upstream_body: dict[str, Any],
    token_count: int | None,
    cfg: CreditConfig,
) -> tuple[int, str]:
    """Compute conservative input token upper bound and basis label."""
    if token_count is not None:
        if (
            isinstance(token_count, bool)
            or not isinstance(token_count, int)
            or token_count < 0
        ):
            raise ReservationRefused(
                "anthropic_reservation_refused",
                f"token_count must be a non-negative integer, got {token_count!r}",
            )
        scaled = (Decimal(token_count) * cfg.count_tokens_margin).to_integral_value(
            rounding=ROUND_CEILING
        )
        return int(scaled), "count_tokens"

    utf8_bytes = len(json.dumps(upstream_body).encode("utf-8"))
    media_blocks = _count_media_blocks(
        upstream_body.get("messages")
    ) + _count_media_blocks(upstream_body.get("system"))
    bound = (
        utf8_bytes
        + cfg.fallback_overhead_tokens
        + cfg.fallback_tokens_per_image * media_blocks
    )
    return bound, "byte_fallback"


def worst_case_usd(
    upstream_body: dict[str, Any],
    rate_row: ModelRateRow,
    token_count: int | None,
    cfg: CreditConfig,
) -> Reservation:
    """Return upper-bound ``Reservation`` or raise ``ReservationRefused``."""
    if not isinstance(upstream_body, dict):
        raise ReservationRefused(
            "anthropic_reservation_refused",
            "upstream_body must be a dict",
        )

    speed = upstream_body.get("speed")
    if isinstance(speed, str) and speed.strip().lower() == "fast":
        raise ReservationRefused(
            "anthropic_reservation_refused",
            "speed='fast' is refused by the Anthropic credit gate",
        )

    max_tokens = upstream_body.get("max_tokens")
    if (
        max_tokens is None
        or isinstance(max_tokens, bool)
        or not isinstance(max_tokens, int)
        or max_tokens <= 0
    ):
        raise ReservationRefused(
            "anthropic_reservation_refused",
            f"max_tokens is required and must be a positive integer, got {max_tokens!r}",
        )

    input_bound, input_basis = _compute_input_bound(upstream_body, token_count, cfg)
    output_bound = max_tokens

    if _is_mcp_connector_class(upstream_body):
        allowance = cfg.mcp_connector_allowance_usd.quantize(
            _MICRO_USD, rounding=ROUND_CEILING
        )
        return Reservation(
            usd=allowance,
            input_tokens_bound=input_bound,
            output_tokens_bound=output_bound,
            mcp_class=True,
            basis="mcp_allowance",
        )

    threshold = rate_row.long_prompt_threshold_tokens
    use_long = threshold is not None and input_bound > threshold
    if use_long:
        base_in = (
            rate_row.long_input_rate_per_m
            if rate_row.long_input_rate_per_m is not None
            else rate_row.input_rate_per_m
        )
        base_out = (
            rate_row.long_output_rate_per_m
            if rate_row.long_output_rate_per_m is not None
            else rate_row.output_rate_per_m
        )
    else:
        base_in = rate_row.input_rate_per_m
        base_out = rate_row.output_rate_per_m

    input_multiplier = (
        Decimal("2.0") if _has_cache_control(upstream_body) else Decimal("1.0")
    )
    in_rate = Decimal(str(base_in)) * input_multiplier
    out_rate = Decimal(str(base_out))

    raw_usd = (
        Decimal(input_bound) * in_rate + Decimal(output_bound) * out_rate
    ) / _TOKENS_PER_MILLION
    usd = raw_usd.quantize(_MICRO_USD, rounding=ROUND_CEILING)
    return Reservation(
        usd=usd,
        input_tokens_bound=input_bound,
        output_tokens_bound=output_bound,
        mcp_class=False,
        basis=input_basis,
    )
