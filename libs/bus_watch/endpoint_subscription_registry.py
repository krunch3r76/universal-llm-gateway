"""GIW registry claim fetch and live-claim predicates for holder join."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
from giw_endpoint import giw_api_url

from bus_watch.endpoint_subscription_row_parse import is_uuid_agent_id, parse_tab_uuid

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RegistryClaims:
    entries: tuple[dict[str, Any], ...]
    refusal: str | None = None


def fetch_registry_claims(
    *,
    timeout_s: float = 10.0,
    fetch_json: Callable[[str], dict[str, Any]] | None = None,
) -> RegistryClaims:
    url = giw_api_url("/api/v1/cursor/registry?state=live")
    if fetch_json is not None:
        try:
            payload = fetch_json(url)
        except Exception:
            logger.exception("endpoint_subscription_registry: registry fetch failed")
            return RegistryClaims((), "claim_source_unavailable")
    else:
        try:
            with httpx.Client(timeout_s) as client:
                response = client.get(url)
            response.raise_for_status()
            payload = response.json()
        except Exception:
            logger.exception("endpoint_subscription_registry: registry fetch failed")
            return RegistryClaims((), "claim_source_unavailable")
    if not isinstance(payload, dict):
        return RegistryClaims((), "claim_source_unavailable")
    if payload.get("truncated"):
        return RegistryClaims(tuple(payload.get("entries") or []), "claim_source_truncated")
    entries = payload.get("entries") or []
    return RegistryClaims(tuple(e for e in entries if isinstance(e, dict)))


def _claim_is_live(entry: dict[str, Any], *, now: datetime | None = None) -> bool:
    if str(entry.get("kind") or "") != "claim":
        return False
    if str(entry.get("status") or "") != "claimed":
        return False
    exp = str(entry.get("expires_at") or "").strip()
    if not exp:
        return False
    try:
        exp_dt = datetime.fromisoformat(exp.replace("Z", "+00:00"))
    except ValueError:
        return False
    now_dt = now or datetime.now(UTC)
    if exp_dt.tzinfo is None:
        exp_dt = exp_dt.replace(tzinfo=UTC)
    return exp_dt > now_dt


def live_claims_for_scope(
    registry: RegistryClaims,
    scope: str,
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    scope_s = str(scope or "").strip()
    out: list[dict[str, Any]] = []
    for entry in registry.entries:
        if str(entry.get("thread_id") or "").strip() != scope_s:
            continue
        if not _claim_is_live(entry, now=now):
            continue
        out.append(entry)
    return out


def claim_tab_uuids(claims: list[dict[str, Any]]) -> set[str]:
    uuids: set[str] = set()
    for claim in claims:
        address = claim.get("address")
        if not isinstance(address, dict):
            continue
        tab_url = address.get("tab_url")
        uid = parse_tab_uuid(str(tab_url) if tab_url is not None else None)
        if uid:
            uuids.add(uid)
    return uuids


def scope_claim_stale(claim_uuids: set[str], h1_rows: list[dict[str, Any]]) -> bool:
    if not claim_uuids:
        return False
    live_ids = {
        is_uuid_agent_id(row.get("agent_id"))
        for row in h1_rows
        if is_uuid_agent_id(row.get("agent_id"))
    }
    return any(uid not in live_ids for uid in claim_uuids)


__all__ = [
    "RegistryClaims",
    "claim_tab_uuids",
    "fetch_registry_claims",
    "live_claims_for_scope",
    "scope_claim_stale",
]
