from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Protocol

import httpx
from transport_utils import DEFAULT_CLOUD_PROXY_URL, make_sync_client

from research_dialogue._types import Snapshot

_STATUS_PATH = "/internal/anthropic-credit/status"


class _HttpClient(Protocol):
    def get(self, path: str) -> httpx.Response: ...


def _parse_decimal(val: Any) -> Decimal | None:
    if val is None:
        return None
    try:
        return Decimal(str(val))
    except (InvalidOperation, ValueError):
        return None


def _unhealthy_snapshot(payload: dict[str, Any], *, as_of: datetime | None = None) -> Snapshot:
    return Snapshot(
        as_of_utc=as_of or datetime.now(tz=UTC),
        healthy=False,
        committed_usd=Decimal("0"),
        open_reservations_usd=Decimal("0"),
        window_start="",
        ledger_instance_id="",
        latch_active=False,
        last_entries=[],
        raw=payload,
    )


def snapshot_from_payload(payload: dict[str, Any], *, as_of: datetime | None = None) -> Snapshot:
    if payload.get("healthy") is not True:
        return _unhealthy_snapshot(payload, as_of=as_of)
    committed = _parse_decimal(payload.get("committed_usd"))
    open_res = _parse_decimal(payload.get("open_reservations_usd"))
    if committed is None or open_res is None:
        raise ValueError("missing committed_usd or open_reservations_usd")
    window = payload.get("window_start")
    instance = payload.get("ledger_instance_id")
    if not isinstance(window, str) or not window:
        raise ValueError("missing window_start")
    if not isinstance(instance, str) or not instance:
        raise ValueError("missing ledger_instance_id")
    latch = payload.get("latch")
    latch_active = bool(isinstance(latch, dict) and latch.get("active"))
    entries = payload.get("last_entries")
    if not isinstance(entries, list):
        entries = []
    return Snapshot(
        as_of_utc=as_of or datetime.now(tz=UTC),
        healthy=True,
        committed_usd=committed,
        open_reservations_usd=open_res,
        window_start=window,
        ledger_instance_id=instance,
        latch_active=latch_active,
        last_entries=[e for e in entries if isinstance(e, dict)],
        raw=payload,
    )


def read_status(
    *,
    client_factory: Callable[[], _HttpClient] | None = None,
) -> Snapshot:
    """Read ledger status from cloud proxy (no side effects)."""

    def _default() -> _HttpClient:
        return make_sync_client(DEFAULT_CLOUD_PROXY_URL, timeout=15.0)

    factory = client_factory or _default
    with factory() as client:
        resp = client.get(_STATUS_PATH)
        if resp.status_code != 200:
            raise httpx.HTTPStatusError(
                "ledger status non-200",
                request=resp.request,
                response=resp,
            )
        payload = resp.json()
        if not isinstance(payload, dict):
            raise ValueError("status payload not object")
        return snapshot_from_payload(payload)


def check_ok(
    snap: Snapshot,
    s0: Snapshot,
    *,
    last_post_committed: Decimal | None = None,
    last_post_ledger_instance_id: str | None = None,
    last_post_ledger_max_id: int | None = None,
    pre_ledger_max_id: int | None = None,
) -> str | None:
    if not snap.healthy:
        return "ledger_unhealthy"
    if not snap.window_start or not snap.ledger_instance_id:
        return "ledger_read_failed"
    if snap.latch_active:
        return "ledger_latched"
    if last_post_ledger_instance_id and snap.ledger_instance_id != last_post_ledger_instance_id:
        return "stale_read"
    if snap.window_start != s0.window_start or snap.ledger_instance_id != s0.ledger_instance_id:
        return "frame_changed"
    if last_post_committed is not None and snap.committed_usd < last_post_committed:
        return "stale_read"
    if (
        last_post_ledger_max_id is not None
        and pre_ledger_max_id is not None
        and pre_ledger_max_id < last_post_ledger_max_id
    ):
        return "stale_read"
    return None
