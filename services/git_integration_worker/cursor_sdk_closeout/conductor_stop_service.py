"""Every conductor stop is a harness service, not a human page.

``ROW_HOP`` is the hop reactor. ``CONSULT_PENDING`` and harvest parks are
the continue services. ``DONE`` closes the lane when the closeout lands.
``HOLD_MERGE`` and ``OPERATOR_GATE`` are the explicit human stops.

``PARKED_TRANSPORT`` with ``hop_budget_admit_retry_cap`` is this module:
one more admit once the worker thread is empty. It does not page.
"""

from __future__ import annotations

from typing import Any

from services.git_integration_worker.cursor_sdk_closeout.conductor_hop_budget import (
    PARK_REASON_ADMIT_RETRY_CAP,
)
from services.git_integration_worker.cursor_sdk_closeout.conductor_hop_progress import (
    record_data,
)

STOP_SERVICE = {
    "ROW_HOP": "hop_reactor",
    "CONSULT_PENDING": "consult_continue",
    "PARKED_TRANSPORT": "park_service",
    "ROW_PINNED": "holds_until_explicit_gate",
    "HOLD_MERGE": "explicit_human",
    "OPERATOR_GATE": "explicit_human",
    "CONFIRM_PENDING": "explicit_human",
    "DONE": "close_lane",
}


def _record(row: dict[str, Any]) -> dict[str, Any]:
    data = record_data(str(row.get("record_json") or ""))
    return data if isinstance(data, dict) else {}


def admit_retry_park_unserviced(row: dict[str, Any]) -> bool:
    """True when an admit-retry park still owes one service hop."""
    record = _record(row)
    if record.get("hop_parked") is not True:
        return False
    if str(record.get("hop_park_reason") or "") != PARK_REASON_ADMIT_RETRY_CAP:
        return False
    return record.get("hop_park_serviced") is not True


def budget_park_pages(reason: str) -> bool:
    """Admit-retry is serviced by the harness. Other budget parks still page."""
    return reason != PARK_REASON_ADMIT_RETRY_CAP
