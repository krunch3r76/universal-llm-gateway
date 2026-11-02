"""Read-only relay for endpoint wake ledger rows."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from ._shared import _structured_relay_error, relay


def _relay_result(result: dict[str, Any], op: str) -> dict[str, Any]:
    if "error" in result:
        structured = _structured_relay_error(result, op=op)
        if structured is not None:
            return structured
        return {"error": f"agent-bus error: {result['error']}"}
    return result


def _wake_ledger_dispatch(
    *,
    thread: str | None = None,
    turn_id: int | None = None,
    turn_number: int | None = None,
    agent: str | None = None,
    state: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    params = {
        key: value
        for key, value in (
            ("thread", thread),
            ("turn_id", turn_id),
            ("turn_number", turn_number),
            ("agent", agent),
            ("state", state),
            ("limit", limit),
        )
        if value is not None
    }
    path = "/endpoint-wake/ledger"
    if params:
        path = f"{path}?{urlencode(params)}"
    return _relay_result(relay("agent-bus", "GET", path), "wake_ledger")
