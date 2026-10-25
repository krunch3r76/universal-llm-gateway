"""Economics blocks for API-role team_dispatch closeout sidecars and ledger pricing."""

from __future__ import annotations

import json
from typing import Any

from event_store.dispatch_economics_pricing import price_row


def normalize_api_role_usage(
    usage: dict[str, Any],
    *,
    model: str,
) -> dict[str, Any]:
    """Map outcome-shaped usage to ledger-compatible token keys."""
    input_tokens = usage.get("input_tokens")
    if input_tokens is None:
        input_tokens = usage.get("prompt_tokens")
    output_tokens = usage.get("output_tokens")
    if output_tokens is None:
        output_tokens = usage.get("completion_tokens")
    cache_read = usage.get("cache_read_tokens")
    if cache_read is None:
        cache_read = usage.get("cached_tokens")
    cache_write = usage.get("cache_write_tokens")
    if cache_write is None:
        cache_write = usage.get("cache_creation_input_tokens")
    total_basis: Any = usage.get("total_tokens_basis")
    if total_basis is None and str(model).startswith("anthropic/"):
        total_basis = "exclusive_sum"
    normalized: dict[str, Any] = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_tokens": cache_read,
        "cache_write_tokens": cache_write,
        "total_tokens": usage.get("total_tokens"),
        "total_tokens_basis": total_basis,
    }
    return normalized


def price_usage(
    usage: dict[str, Any] | None,
    *,
    model: str,
    knobs: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    """Map frontier usage dict to priced economics fields for sidecar and ledger rows."""
    if not usage:
        return None
    normalized = normalize_api_role_usage(usage, model=model)
    row = {
        "prompt_tokens": normalized.get("input_tokens"),
        "completion_tokens": normalized.get("output_tokens"),
        "cache_read_tokens": normalized.get("cache_read_tokens"),
        "cache_write_tokens": normalized.get("cache_write_tokens"),
        "total_tokens_basis": normalized.get("total_tokens_basis"),
        "model_id": model,
        "model_knobs_requested": knobs,
    }
    priced = price_row(row, None)
    return {
        "input_tokens": normalized.get("input_tokens"),
        "output_tokens": normalized.get("output_tokens"),
        "cache_read_tokens": priced.get("cache_read_tokens"),
        "cache_write_tokens": priced.get("cache_write_tokens"),
        "total_tokens": normalized.get("total_tokens"),
        "cost_usd": priced.get("cost_usd"),
        "cost_source": priced.get("cost_source"),
    }


def append_api_closeout_economics(
    content: str,
    *,
    work_key: str | None,
    execution_id: str,
    model: str,
    usage: dict[str, Any] | None,
    contract: str | None = None,
) -> str:
    """Append markdown + structured JSON economics footer for durable sidecars."""
    priced = price_usage(usage, model=model)
    if not work_key and priced is None:
        return content
    lines = [content.rstrip(), "", "## dispatch economics", ""]
    if work_key:
        lines.append(f"work_key: {work_key}")
    lines.append(f"execution_id: {execution_id}")
    if contract:
        lines.append(f"contract: {contract}")
    if priced:
        for key in (
            "input_tokens",
            "output_tokens",
            "cache_read_tokens",
            "cache_write_tokens",
            "cost_usd",
            "cost_source",
        ):
            if key in priced and priced[key] is not None:
                lines.append(f"{key}: {priced[key]}")
    structured = {
        "work_key": work_key,
        "execution_id": execution_id,
        "contract": contract,
        "usage": priced,
    }
    lines.extend(["", "```json", json.dumps(structured, indent=2), "```", ""])
    return "\n".join(lines)
