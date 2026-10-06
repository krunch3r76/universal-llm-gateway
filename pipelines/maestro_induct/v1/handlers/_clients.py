"""HTTP helpers for maestro-induct handlers."""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

from transport_utils import DEFAULT_AGENT_BUS_URL, DEFAULT_CORTEX_URL, make_async_client

REQUEST_TIMEOUT_S = 3.0
RUN_BUDGET_S = 8.0
MIN_REQUEST_S = 0.25


def now_epoch() -> float:
    return time.time()


def step_output_json(outputs: dict[str, Any], step_name: str) -> dict[str, Any]:
    raw = outputs.get(step_name)
    if raw is None:
        return {}
    if hasattr(raw, "json"):
        data = getattr(raw, "json", None)
        return data if isinstance(data, dict) else {}
    if isinstance(raw, dict):
        inner = raw.get("json")
        return inner if isinstance(inner, dict) else raw
    return {}


def _bus_headers() -> dict[str, str]:
    token = os.getenv("AGENT_BUS_TOKEN", "").strip()
    return {"Authorization": f"Bearer {token}"} if token else {}


def _remaining(deadline_epoch: float | None) -> float | None:
    if deadline_epoch is None:
        return None
    return deadline_epoch - now_epoch()


async def bus_get(
    path: str,
    *,
    params: dict[str, Any] | None = None,
    deadline_epoch: float | None = None,
) -> tuple[dict[str, Any], int]:
    rem = _remaining(deadline_epoch)
    if rem is not None and rem < MIN_REQUEST_S:
        return (
            {"error": {"code": "deadline_exceeded", "message": "budget exhausted"}},
            504,
        )
    timeout = REQUEST_TIMEOUT_S if rem is None else min(REQUEST_TIMEOUT_S, rem)
    async with make_async_client(
        DEFAULT_AGENT_BUS_URL, timeout=timeout
    ) as client:
        try:
            resp = await client.get(path, params=params or {}, headers=_bus_headers())
        except Exception as exc:  # noqa: BLE001
            return {"error": {"code": "agent_bus_unreachable", "message": str(exc)}}, 503
    try:
        payload = resp.json()
    except Exception:  # noqa: BLE001
        payload = {"error": {"code": f"http_{resp.status_code}", "message": resp.text[:500]}}
    if not isinstance(payload, dict):
        payload = {"error": {"code": "invalid_response", "message": str(payload)}}
    return payload, resp.status_code


async def cortex_dispatch(
    tool: str,
    arguments: dict[str, Any],
    *,
    deadline_epoch: float | None = None,
) -> dict[str, Any]:
    rem = _remaining(deadline_epoch)
    if rem is not None and rem < MIN_REQUEST_S:
        return {"error": "deadline_exceeded"}
    timeout = REQUEST_TIMEOUT_S if rem is None else min(REQUEST_TIMEOUT_S, rem)
    async with make_async_client(DEFAULT_CORTEX_URL, timeout=timeout) as client:
        try:
            resp = await client.post(
                "/dispatch",
                json={"tool": tool, "arguments": json.dumps(arguments)},
            )
        except Exception as exc:  # noqa: BLE001
            return {"error": f"transport_error: {exc}"}
    if resp.status_code >= 400:
        try:
            body = resp.json()
        except Exception:  # noqa: BLE001
            return {"error": f"http_{resp.status_code}"}
        return body if isinstance(body, dict) else {"error": str(body)}
    try:
        data = resp.json()
    except Exception as exc:  # noqa: BLE001
        return {"error": f"invalid_json: {exc}"}
    return data if isinstance(data, dict) else {"error": "non_object"}


def read_cortex_file(uri: str) -> tuple[str, str] | dict[str, dict[str, str]]:
    root_env = os.environ.get("CORTEX_FILES_ROOT", "").strip()
    if not root_env:
        return {"error": {"kind": "files_root_unset", "message": "CORTEX_FILES_ROOT unset"}}
    if not uri.startswith("cortex://"):
        return {"error": {"kind": "bad_uri", "message": "expected cortex:// URI"}}
    path = Path(root_env) / uri.removeprefix("cortex://")
    if not path.is_file():
        return {"error": {"kind": "file_missing", "message": str(path)}}
    try:
        data = path.read_bytes()
    except OSError as exc:
        return {"error": {"kind": "read_failed", "message": str(exc)}}
    digest = hashlib.sha256(data).hexdigest()
    return data.decode("utf-8"), digest
