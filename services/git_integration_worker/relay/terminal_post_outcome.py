"""Terminal bus-post outcome → job ledger state (arc 6655 terminal-report honesty).

Four-row contract — each post outcome maps to exactly one ledger shape:

| Post outcome | Job ledger state | Retry? | What a reader sees |
|---|---|---|---|
| 2xx | ``status=done``, ``lifecycle_phase=terminal_done`` | n/a | turn present, job done |
| 4xx that a retry cannot fix (413, 422) | ``status=report_undelivered``, ``lifecycle_phase=terminal_report_undelivered``, ``terminal_reason=bus_reject_<code>`` | no | work acted (journal entry exists); no bus turn; ``terminal_reason`` names reject |
| 5xx / transport error (incl. 599) | ``status=report_undelivered``, ``lifecycle_phase=terminal_report_undelivered``, ``terminal_reason=bus_transport_error``, ``record_json.terminal_post_retryable=true`` | yes (manual re-post / operator) | same as 413 row but retryable marker set |
| Post never attempted (crash before reply) | ``status=claimed`` (unchanged) | yes (GIW reconcile on restart) | in-flight from observer until reconcile; not confused with done or failed |

``report_undelivered`` means the seat finished its work and attempted the
terminal post, but the bus did not accept it — distinct from ``failed`` (work
did not succeed) and ``done`` (report landed).
"""

from __future__ import annotations

STATUS_REPORT_UNDELIVERED = "report_undelivered"

TERMINAL_REASON_BUS_TRANSPORT = "bus_transport_error"
TERMINAL_REASON_BUS_REJECT_PREFIX = "bus_reject_"


def terminal_post_delivered(status_code: int) -> bool:
    """True when the terminal POST reached the bus (HTTP 2xx)."""
    return 200 <= status_code < 400


def terminal_post_retryable(status_code: int) -> bool:
    """True when a transport/server fault may succeed on re-post."""
    return status_code >= 500 or status_code == 599


def terminal_post_permanent_reject(status_code: int) -> bool:
    """True for 4xx rejects where retrying the same body will not help."""
    return 400 <= status_code < 500


def terminal_reason_for_status(status_code: int) -> str:
    """Map HTTP status to durable ``terminal_reason`` enum fragment."""
    if terminal_post_retryable(status_code):
        return TERMINAL_REASON_BUS_TRANSPORT
    if terminal_post_permanent_reject(status_code):
        return f"{TERMINAL_REASON_BUS_REJECT_PREFIX}{status_code}"
    return f"bus_post_{status_code}"


__all__ = [
    "STATUS_REPORT_UNDELIVERED",
    "TERMINAL_REASON_BUS_REJECT_PREFIX",
    "TERMINAL_REASON_BUS_TRANSPORT",
    "terminal_post_delivered",
    "terminal_post_permanent_reject",
    "terminal_post_retryable",
    "terminal_reason_for_status",
]
