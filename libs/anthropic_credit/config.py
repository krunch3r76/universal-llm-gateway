"""Load and validate ``config/anthropic_credit.yaml`` (fail closed on error)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_CONFIG_PATH = _REPO_ROOT / "config" / "anthropic_credit.yaml"


class CreditConfigInvalid(ValueError):  # noqa: N818
    """Raised when anthropic credit config fails validation."""


@dataclass(frozen=True, slots=True)
class CycleConfig:
    anchor_day: int
    anchor_time_utc: str
    reset_tz: str
    early_slack_hours: int
    late_slack_hours: int
    owner: str
    steward: str


@dataclass(frozen=True, slots=True)
class AvailableBalanceConfig:
    usd: Decimal
    as_of: str | None
    owner: str


@dataclass(frozen=True, slots=True)
class CreditConfig:
    cycle: CycleConfig
    grant_usd: Decimal
    hard_stop_usd: Decimal
    mcp_connector_stop_usd: Decimal
    mcp_connector_allowance_usd: Decimal
    reservation_ttl_minutes: int
    count_tokens_margin: Decimal
    fallback_overhead_tokens: int
    fallback_tokens_per_image: int
    ledger_path: Path
    reconcile_drift_usd: Decimal
    available_balance: AvailableBalanceConfig
    probe_cap_usd: Decimal
    metered_models: tuple[str, ...]
    standard_models: tuple[str, ...]
    high_cost_models: tuple[str, ...]
    fail_mode: str
    server_tool_fees_usd: dict[str, Decimal]


def credit_config_path() -> Path:
    override = os.environ.get("ANTHROPIC_CREDIT_CONFIG_PATH", "").strip()
    if override:
        return Path(override)
    return _DEFAULT_CONFIG_PATH


def _decimal(value: Any, *, field: str) -> Decimal:
    if value is None:
        raise CreditConfigInvalid(f"{field} is required")
    try:
        return Decimal(str(value))
    except Exception as exc:
        raise CreditConfigInvalid(f"{field} must be a number") from exc


def _int_nonneg(value: Any, *, field: str) -> int:
    if value is None:
        raise CreditConfigInvalid(f"{field} is required")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise CreditConfigInvalid(f"{field} must be an integer") from exc
    if parsed < 0:
        raise CreditConfigInvalid(f"{field} must be >= 0")
    return parsed


def _parse_as_of(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    normalized = text.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise CreditConfigInvalid(
            "available_balance.as_of must be ISO-8601 UTC"
        ) from exc
    if dt.tzinfo is None:
        raise CreditConfigInvalid("available_balance.as_of must be ISO-8601 UTC")
    return text


def _validate_metered_models(raw: Any) -> tuple[str, ...]:
    if not isinstance(raw, list) or not raw:
        raise CreditConfigInvalid("metered_models must be a non-empty list")
    out: list[str] = []
    for entry in raw:
        model_id = str(entry or "").strip()
        if not model_id.startswith("anthropic/"):
            raise CreditConfigInvalid(
                f"metered_models entry must be anthropic/* id, got {model_id!r}"
            )
        out.append(model_id)
    return tuple(out)


def _validate_ledger_path(raw: Any) -> Path:
    if raw is None or not str(raw).strip():
        raise CreditConfigInvalid("ledger_path is required")
    expanded = Path(str(raw).strip()).expanduser().resolve()
    path_str = str(expanded)
    if path_str.startswith("/mnt/"):
        raise CreditConfigInvalid("ledger_path must not resolve under /mnt/ (K38)")
    return expanded


def _model_id_list(raw: Any, *, field: str) -> tuple[str, ...]:
    if not isinstance(raw, list):
        raise CreditConfigInvalid(f"{field} must be a list")
    return tuple(str(x).strip() for x in raw if str(x or "").strip())


def load_credit_config(path: Path | None = None) -> CreditConfig:
    """Load anthropic credit config; raise ``CreditConfigInvalid`` on any failure."""
    yaml_path = path or credit_config_path()
    if not yaml_path.is_file():
        raise CreditConfigInvalid(f"config file not found: {yaml_path}")
    try:
        payload = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise CreditConfigInvalid(f"malformed YAML in {yaml_path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise CreditConfigInvalid("config root must be a mapping")

    cycle_raw = payload.get("cycle")
    if not isinstance(cycle_raw, dict):
        raise CreditConfigInvalid("cycle must be a mapping")

    anchor_day = cycle_raw.get("anchor_day")
    try:
        anchor_day_int = int(anchor_day)
    except (TypeError, ValueError) as exc:
        raise CreditConfigInvalid("cycle.anchor_day must be an integer 1–28") from exc
    if not 1 <= anchor_day_int <= 28:
        raise CreditConfigInvalid("cycle.anchor_day must be in 1–28")

    early_slack = _int_nonneg(
        cycle_raw.get("early_slack_hours"), field="early_slack_hours"
    )
    late_slack = _int_nonneg(
        cycle_raw.get("late_slack_hours"), field="late_slack_hours"
    )

    cycle_owner = str(cycle_raw.get("owner") or "").strip()
    if not cycle_owner:
        raise CreditConfigInvalid("cycle.owner must be non-empty")

    grant_usd = _decimal(payload.get("grant_usd"), field="grant_usd")
    hard_stop_usd = _decimal(payload.get("hard_stop_usd"), field="hard_stop_usd")
    if hard_stop_usd >= grant_usd:
        raise CreditConfigInvalid("hard_stop_usd must be < grant_usd")

    mcp_stop = _decimal(
        payload.get("mcp_connector_stop_usd"), field="mcp_connector_stop_usd"
    )
    if mcp_stop > hard_stop_usd:
        raise CreditConfigInvalid("mcp_connector_stop_usd must be <= hard_stop_usd")

    mcp_allowance = _decimal(
        payload.get("mcp_connector_allowance_usd"),
        field="mcp_connector_allowance_usd",
    )
    if mcp_allowance <= Decimal("0"):
        raise CreditConfigInvalid("mcp_connector_allowance_usd must be > 0")

    ttl_minutes = _int_nonneg(
        payload.get("reservation_ttl_minutes"), field="reservation_ttl_minutes"
    )
    if ttl_minutes < 1:
        raise CreditConfigInvalid("reservation_ttl_minutes must be >= 1")

    overlap_minutes = (early_slack + late_slack) * 60
    min_overlap = 4 * ttl_minutes
    if overlap_minutes < min_overlap:
        raise CreditConfigInvalid(
            "(early_slack_hours + late_slack_hours) * 60 must be >= "
            f"4 * reservation_ttl_minutes ({min_overlap})"
        )

    fees_raw = payload.get("server_tool_fees_usd")
    if fees_raw is None:
        fees_raw = {}
    if not isinstance(fees_raw, dict):
        raise CreditConfigInvalid("server_tool_fees_usd must be a mapping")
    server_tool_fees: dict[str, Decimal] = {}
    for key, val in fees_raw.items():
        fee = _decimal(val, field=f"server_tool_fees_usd.{key}")
        if fee < Decimal("0"):
            raise CreditConfigInvalid(
                f"server_tool_fees_usd.{key} must be >= 0"
            )
        server_tool_fees[str(key)] = fee

    fail_mode = str(payload.get("fail_mode") or "").strip()
    if fail_mode != "closed":
        raise CreditConfigInvalid('fail_mode must be "closed"')

    margin = _decimal(payload.get("count_tokens_margin"), field="count_tokens_margin")
    if margin < Decimal("1.0"):
        raise CreditConfigInvalid("count_tokens_margin must be >= 1.0")

    probe_cap = _decimal(payload.get("probe_cap_usd"), field="probe_cap_usd")
    if probe_cap <= Decimal("0"):
        raise CreditConfigInvalid("probe_cap_usd must be > 0")

    balance_raw = payload.get("available_balance")
    if not isinstance(balance_raw, dict):
        raise CreditConfigInvalid("available_balance must be a mapping")
    balance_usd = _decimal(balance_raw.get("usd"), field="available_balance.usd")
    if balance_usd < Decimal("0"):
        raise CreditConfigInvalid("available_balance.usd must be >= 0")
    balance_owner = str(balance_raw.get("owner") or "").strip()
    if not balance_owner:
        raise CreditConfigInvalid("available_balance.owner must be non-empty")
    as_of = _parse_as_of(balance_raw.get("as_of"))

    cycle = CycleConfig(
        anchor_day=anchor_day_int,
        anchor_time_utc=str(cycle_raw.get("anchor_time_utc") or "00:00"),
        reset_tz=str(cycle_raw.get("reset_tz") or "unverified"),
        early_slack_hours=early_slack,
        late_slack_hours=late_slack,
        owner=cycle_owner,
        steward=str(cycle_raw.get("steward") or "").strip(),
    )

    return CreditConfig(
        cycle=cycle,
        grant_usd=grant_usd,
        hard_stop_usd=hard_stop_usd,
        mcp_connector_stop_usd=mcp_stop,
        mcp_connector_allowance_usd=mcp_allowance,
        reservation_ttl_minutes=ttl_minutes,
        count_tokens_margin=margin,
        fallback_overhead_tokens=_int_nonneg(
            payload.get("fallback_overhead_tokens"), field="fallback_overhead_tokens"
        ),
        fallback_tokens_per_image=_int_nonneg(
            payload.get("fallback_tokens_per_image"), field="fallback_tokens_per_image"
        ),
        ledger_path=_validate_ledger_path(payload.get("ledger_path")),
        reconcile_drift_usd=_decimal(
            payload.get("reconcile_drift_usd"), field="reconcile_drift_usd"
        ),
        available_balance=AvailableBalanceConfig(
            usd=balance_usd, as_of=as_of, owner=balance_owner
        ),
        probe_cap_usd=probe_cap,
        metered_models=_validate_metered_models(payload.get("metered_models")),
        standard_models=_model_id_list(
            payload.get("standard_models"), field="standard_models"
        ),
        high_cost_models=_model_id_list(
            payload.get("high_cost_models"), field="high_cost_models"
        ),
        fail_mode=fail_mode,
        server_tool_fees_usd=server_tool_fees,
    )
