from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

from anthropic_credit.config import credit_config_path, load_credit_config
from anthropic_credit.usage_cost import usage_cost_usd_for_config
from event_store.model_rate_table import ModelRateRow, resolve_pinned_rate

from research_dialogue.models import normalize_ledger_model

# Native Messages API model id -> pinned rate row id.
_RATE_MODEL = {
    "claude-opus-5-5": "anthropic/claude-opus-5-5",
    "claude-sonnet-5": "anthropic/claude-sonnet-5",
}


def rate_row_for_api_model(model: str, *, rates_path: Path | None = None) -> ModelRateRow:
    pinned = _RATE_MODEL.get(model, normalize_ledger_model(model))
    path = rates_path or (Path(__file__).resolve().parents[2] / "config" / "model_rates.yaml")
    row = resolve_pinned_rate(pinned, path=path)
    if row is None:
        raise ValueError(f"no pinned rate row for model {model!r} ({pinned})")
    return row


def billed_usd(
    usage: dict[str, Any] | None,
    *,
    model: str,
    rates_path: Path | None = None,
) -> tuple[Decimal, list[str], list[str]]:
    row = rate_row_for_api_model(model, rates_path=rates_path)
    cfg_path = credit_config_path()
    cfg = load_credit_config(cfg_path)
    cost, missing, unknown = usage_cost_usd_for_config(usage, row, cfg)
    if cost is None:
        return Decimal("0"), list(missing or []), list(unknown or [])
    return cost, list(missing or []), list(unknown or [])
