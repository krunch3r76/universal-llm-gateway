"""Economics blocks for API-role team_dispatch closeout sidecars."""

from __future__ import annotations

import json
from typing import Any

from event_store.dispatch_economics_pricing import _price_row


def price_usage(
    usage: dict[str, Any] | None,
    *,
    model: str,
    knobs: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    if not usage:
        return None
    row = {
        "prompt_tokens": usage.get("input_tokens"),
        "completion_tokens": usage.get("output_tokens"),
        "cache_read_tokens": usage.get("cached_tokens") or usage.get("cache_read_tokens"),
        "cache_write_tokens": usage.get("cache_write_tokens"),
        "total_tokens_basis": usage.get("total_tokens"),
        "model_id": model,
        "model_knobs_requested": knobs,
    }
    priced = _price_row(row, None)
    return {
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "cache_read_tokens": priced.get("cache_read_tokens"),
        "cache_write_tokens": priced.get("cache_write_tokens"),
        "total_tokens": usage.get("total_tokens"),
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
