"""Shared relay and enrichment helpers for ClaudeBurst perps MCP tools."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

import httpx
from mcp_events import record
from transport_utils import make_async_client

_SOCK = "/tmp/universal-protocol/claudeburst-perps.sock"
_UDS_URL = f"unix://{_SOCK}"
_LIGHTER_BASE_URL = os.environ.get(
    "LIGHTER_BASE_URL", "https://mainnet.zklighter.elliot.ai"
)
_REQUEST_TIMEOUT = 10.0
_TCP_HOST = os.environ.get("CLAUDEBURST_PERPS_HOST", "")
_TCP_PORT = int(os.environ.get("CLAUDEBURST_PERPS_PORT", "8891"))
_LAST_KNOWN_HEARTBEAT: str | None = None

OPS = {
    "status": ("GET", "/status"),
    "positions": ("GET", "/positions"),
    "risk": ("GET", "/risk"),
    "health": ("GET", "/health"),
    "signals": ("GET", "/signals"),
    "live_resolve": ("GET", "/live/resolve-account"),
    "live_account": ("GET", "/live/account"),
    "live_active_orders": ("GET", "/live/orders/active"),
    "live_recent_orders": ("GET", "/live/orders/recent"),
    "kill": ("POST", "/commands/kill-switch"),
    "pause": ("POST", "/commands/pause"),
    "resume": ("POST", "/commands/resume"),
}

SOURCE_MAP: dict[str, str] = {
    "status": "bot_local",
    "positions": "bot_local",
    "risk": "bot_local",
    "health": "bot_local",
    "signals": "bot_local",
    "live_resolve": "lighter_exchange",
    "live_account": "lighter_exchange",
    "live_active_orders": "lighter_exchange",
    "live_recent_orders": "lighter_exchange",
    "kill": "control",
    "pause": "control",
    "resume": "control",
}


def _service_url() -> str:
    if _TCP_HOST:
        return f"http://{_TCP_HOST}:{_TCP_PORT}"
    return _UDS_URL


def bot_hint() -> str:
    if _TCP_HOST:
        return f"Check bot is reachable at {_TCP_HOST}:{_TCP_PORT}"
    return "Start with: cd /mnt/torus/projects/claudeburst && python -m perps"


def _parse_timestamp(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        seconds = (
            float(value) / 1000.0
            if abs(float(value)) > 1_000_000_000_000
            else float(value)
        )
        return datetime.fromtimestamp(seconds, tz=UTC)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            parsed = datetime.fromisoformat(text)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        except ValueError:
            return None
    return None


def _iso_timestamp(value: Any) -> str | None:
    parsed = _parse_timestamp(value)
    return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z") if parsed else None


def _unix_ms_timestamp(value: Any) -> int | None:
    parsed = _parse_timestamp(value)
    return int(parsed.timestamp() * 1000) if parsed else None


def _looks_like_timestamp_key(key: str) -> bool:
    return (
        key in {"timestamp", "last_heartbeat"}
        or key.endswith("_at")
        or key.endswith("_time")
    )


def _augment_timestamp_pairs(node: Any) -> None:
    if isinstance(node, list):
        for item in node:
            _augment_timestamp_pairs(item)
        return
    if not isinstance(node, dict):
        return

    for key, value in list(node.items()):
        if _looks_like_timestamp_key(key):
            iso = _iso_timestamp(value)
            unix_ms = _unix_ms_timestamp(value)
            if iso and key != "timestamp":
                node.setdefault(f"{key}_iso", iso)
            if unix_ms is not None and not key.endswith("_unix_ms"):
                node.setdefault(f"{key}_unix_ms", unix_ms)
        _augment_timestamp_pairs(value)


def _extract_last_heartbeat(node: Any) -> str | None:
    if isinstance(node, dict):
        if "last_heartbeat" in node:
            iso = _iso_timestamp(node["last_heartbeat"])
            if iso:
                return iso
        heartbeat = node.get("heartbeat")
        if isinstance(heartbeat, dict):
            for key in ("timestamp", "last_heartbeat", "created_at"):
                iso = _iso_timestamp(heartbeat.get(key))
                if iso:
                    return iso
        for value in node.values():
            found = _extract_last_heartbeat(value)
            if found:
                return found
    elif isinstance(node, list):
        for item in node:
            found = _extract_last_heartbeat(item)
            if found:
                return found
    return None


def _remember_heartbeat(payload: Any) -> None:
    global _LAST_KNOWN_HEARTBEAT
    heartbeat = _extract_last_heartbeat(payload)
    if heartbeat:
        _LAST_KNOWN_HEARTBEAT = heartbeat


def _coerce_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _position_notional(position: dict[str, Any]) -> float | None:
    for key in ("notional", "notional_usdc", "size_usdc", "position_value"):
        value = _coerce_float(position.get(key))
        if value is not None:
            return abs(value)
    size = _coerce_float(position.get("size"))
    price = _coerce_float(position.get("price")) or _coerce_float(
        position.get("entry_price")
    )
    if size is not None and price is not None:
        return abs(size * price)
    return None


def _pnl_value(position: dict[str, Any]) -> float | None:
    for key in ("unrealized_pnl_usdc", "unrealized_pnl", "pnl_usdc"):
        value = _coerce_float(position.get(key))
        if value is not None:
            return value
    return None


def _position_lists(payload: dict[str, Any]) -> list[list[dict[str, Any]]]:
    lists: list[list[dict[str, Any]]] = []
    for key in ("positions", "open_positions"):
        value = payload.get(key)
        if isinstance(value, list):
            lists.append([item for item in value if isinstance(item, dict)])
    return lists


def _augment_position_metrics(payload: dict[str, Any]) -> None:
    collateral = _coerce_float(payload.get("collateral"))
    for positions in _position_lists(payload):
        for position in positions:
            notional = _position_notional(position)
            pnl = _pnl_value(position)
            if collateral and notional is not None:
                position.setdefault("leverage_ratio", round(notional / collateral, 4))
            if pnl is not None:
                position.setdefault("unrealized_pnl_usdc", round(pnl, 4))
                if collateral:
                    position.setdefault(
                        "unrealized_pnl_pct_of_collateral",
                        round((pnl / collateral) * 100.0, 4),
                    )


def _annotate_reconciliation(op: str, payload: dict[str, Any]) -> None:
    if SOURCE_MAP.get(op) != "bot_local":
        return
    recon = payload.get("reconciliation")
    if not isinstance(recon, dict):
        payload["_reconciliation_status"] = "pending"
        payload["_reconciliation_note"] = (
            "Reconciliation not yet run. Call live_account to verify full exchange state."
        )
        return

    status = recon.get("status")
    if status is not None:
        payload["_reconciliation_status"] = status
    untracked_count = int(recon.get("untracked_count", 0) or 0)
    ghost_count = int(recon.get("ghost_count", 0) or 0)
    if untracked_count or ghost_count:
        payload["_reconciliation_status"] = "diverged"
        payload["_reconciliation_warning"] = (
            f"EXCHANGE DIVERGE — {untracked_count} untracked + {ghost_count} ghost positions. "
            "Call positions for details or live_account for exchange truth."
        )
    elif recon.get("last_checked_at") is not None and status is None:
        payload["_reconciliation_status"] = "clean"


async def _fetch_funding_rates() -> dict[int, float]:
    import lighter

    api = lighter.ApiClient(configuration=lighter.Configuration(host=_LIGHTER_BASE_URL))
    try:
        response = await lighter.FundingApi(api).funding_rates()
        return {
            int(item.market_id): float(item.rate)
            for item in (getattr(response, "funding_rates", None) or [])
        }
    except Exception as exc:
        record("mcp.tool.claudeburst.perps.funding.failed", error=str(exc))
        return {}
    finally:
        await api.close()


def _augment_status_fields(
    payload: dict[str, Any], funding_rates: dict[int, float]
) -> None:
    heartbeat = _extract_last_heartbeat(payload)
    parsed = _parse_timestamp(heartbeat)
    if parsed:
        payload["last_known_heartbeat"] = heartbeat
        payload.setdefault(
            "time_since_last_heartbeat_seconds",
            round((datetime.now(UTC) - parsed).total_seconds(), 1),
        )
    for positions in _position_lists(payload):
        for position in positions:
            market_id = position.get("market_id")
            if isinstance(market_id, int) and market_id in funding_rates:
                position.setdefault("funding_rate", funding_rates[market_id])


async def call_bot(
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        async with make_async_client(
            _service_url(), timeout=_REQUEST_TIMEOUT
        ) as client:
            response = await client.request(
                method, path, params=params or None, json=body
            )
            response.raise_for_status()
            payload = response.json()
    except httpx.ConnectError:
        return {
            "error": "bot_unreachable",
            "last_known_heartbeat": _LAST_KNOWN_HEARTBEAT,
            "hint": bot_hint(),
        }
    except httpx.HTTPStatusError as exc:
        return {
            "error": "http_error",
            "status_code": exc.response.status_code,
            "body": exc.response.text,
        }
    except Exception as exc:
        return {"error": str(exc)}

    if isinstance(payload, dict):
        _remember_heartbeat(payload)
        _augment_timestamp_pairs(payload)
        return payload
    return {"result": payload}


async def enrich_perps_result(op: str, payload: dict[str, Any]) -> dict[str, Any]:
    payload["_data_source"] = SOURCE_MAP.get(op, "unknown")
    _annotate_reconciliation(op, payload)
    _augment_position_metrics(payload)
    if op in {"status", "live_account"}:
        _augment_status_fields(payload, await _fetch_funding_rates())
    return payload
