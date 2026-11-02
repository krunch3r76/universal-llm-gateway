"""Read latest terminal conductor dispatch row via GIW HTTP (read-only)."""

from __future__ import annotations

import json
import os
from typing import Any

import httpx

from giw_endpoint import git_integration_worker_base_url

_TIMEOUT_S = 5.0
_ROUTE = "/api/v1/cursor/dispatch/latest-terminal-conductor"


def latest_terminal_conductor_for_thread(thread_id: str) -> dict[str, Any] | None:
    """Return the latest terminal conductor payload for *thread_id*, or None."""
    result = fetch_latest_terminal_conductor(thread_id)
    if result.get("ledger_unreachable"):
        return None
    row = result.get("row")
    return row if isinstance(row, dict) else None


def _ledger_giw_base_url() -> str:
    legacy = os.getenv("GIW_BASE_URL")
    if legacy is not None and legacy.strip():
        return legacy.strip().rstrip("/")
    return git_integration_worker_base_url()


def fetch_latest_terminal_conductor(thread_id: str) -> dict[str, Any]:
    """Fetch terminal conductor row; fail-closed with ``ledger_unreachable``."""
    if not thread_id:
        return {"row": None, "ledger_unreachable": True}
    url = f"{_ledger_giw_base_url()}{_ROUTE}"
    try:
        with httpx.Client(timeout=_TIMEOUT_S) as client:
            resp = client.get(url, params={"thread_id": thread_id})
    except httpx.HTTPError:
        return {"row": None, "ledger_unreachable": True}
    if resp.status_code == 404:
        return {"row": None, "ledger_unreachable": False}
    if resp.status_code >= 400:
        return {"row": None, "ledger_unreachable": True}
    try:
        payload = resp.json()
    except json.JSONDecodeError:
        return {"row": None, "ledger_unreachable": True}
    if not isinstance(payload, dict):
        return {"row": None, "ledger_unreachable": True}
    if payload.get("found") is False:
        return {"row": None, "ledger_unreachable": False}
    row = {k: v for k, v in payload.items() if k != "found"}
    return {"row": row, "ledger_unreachable": False}


def record_json_dict(row: dict[str, Any]) -> dict[str, Any]:
    raw = row.get("record_json")
    if isinstance(raw, dict):
        return raw
    raw_str = str(raw or "")
    try:
        data = json.loads(raw_str) if raw_str else {}
    except json.JSONDecodeError:
        data = {}
    return data if isinstance(data, dict) else {}
